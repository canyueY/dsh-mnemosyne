# -*- coding: utf-8 -*-
"""AnswerSelection：有限结构化目标下的当前/历史答案选择（E03）。

读路径：不写入 Claim verified。复用 parser、ClaimStore、Derivation 修订重查。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from cognitive_memory.claims.ast import Atom, ClaimAST, Negation, Unresolved
from cognitive_memory.claims.parser import parse_memory_text
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.evidence.derivation import DerivationStore


Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _in_interval(at: datetime, start: str | None, end: str | None) -> bool:
    """有效区间 [from, to)。"""
    s = _parse_ts(start)
    e = _parse_ts(end)
    if s is not None and at < s:
        return False
    if e is not None and at >= e:
        return False
    return True


def _unwrap_atom(node: Any) -> tuple[Atom | None, bool, dict[str, Any]]:
    """返回 (atom, negated, scope_hints)。"""
    hints: dict[str, Any] = {}
    if node is None:
        return None, False, hints
    kind = getattr(node, "kind", None) or (node.get("kind") if isinstance(node, dict) else None)
    if kind == "atom":
        atom = node if isinstance(node, Atom) else Atom.model_validate(node)
        return atom, False, hints
    if kind == "negation":
        target = node.target if hasattr(node, "target") else node.get("target")
        atom, _, h = _unwrap_atom(target)
        hints.update(h)
        return atom, True, hints
    if kind == "temporal":
        target = node.target if hasattr(node, "target") else node.get("target")
        interval = node.interval if hasattr(node, "interval") else node.get("interval") or {}
        if hasattr(interval, "model_dump"):
            interval = interval.model_dump()
        hints["temporal"] = interval
        return _unwrap_atom(target)[0], _unwrap_atom(target)[1], hints
    if kind == "conditional":
        cond = node.condition if hasattr(node, "condition") else node.get("condition")
        cons = node.consequence if hasattr(node, "consequence") else node.get("consequence")
        hints["conditional"] = True
        hints["condition"] = cond.model_dump() if hasattr(cond, "model_dump") else cond
        atom, neg, h = _unwrap_atom(cons)
        hints.update(h)
        return atom, neg, hints
    return None, False, hints


class AnswerSelectionService:
    def __init__(self, service: Any, *, clock: Clock | None = None) -> None:
        self.service = service
        self.db = service.db
        self.claims = ClaimStore(self.db)
        self.derivations = DerivationStore(self.db)
        self.clock = clock or _utcnow

    def select_answer(
        self,
        *,
        bank_id: str = "personal",
        query: str = "",
        target: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        at: str | None = None,
        mode: str = "current",
        budget: dict[str, Any] | None = None,
        expected_source_revisions: list[dict[str, Any]] | None = None,
        current_source_revisions: dict[str, str] | None = None,
        principal: Any | None = None,
    ) -> dict[str, Any]:
        decision_id = f"ans_{uuid.uuid4().hex[:12]}"
        trace_id = f"tr_{uuid.uuid4().hex[:10]}"
        budget = dict(budget or {})
        max_candidates = int(budget.get("max_candidates") or 32)
        max_steps = int(budget.get("max_steps") or 16)
        ctx = dict(context or {})
        at_dt = _parse_ts(at) or self.clock()
        mode = (mode or "current").strip().lower()
        if mode not in {"current", "history"}:
            mode = "current"

        correlation_id = str(
            ctx.get("correlation_id") or budget.get("correlation_id") or decision_id
        )
        attempt = int(ctx.get("attempt") or budget.get("attempt") or 1)
        # E07：调用/轮数/耗时限额在入口执法，不只写字段
        call_gate = self._enforce_call_budget(
            correlation_id=correlation_id,
            attempt=attempt,
            budget=budget,
            ctx=ctx,
        )
        if call_gate and not call_gate.get("allowed", True):
            return self._result(
                decision_id,
                trace_id,
                status="unknown",
                reason_codes=list(call_gate.get("reason_codes") or ["budget_exhausted"]),
                budget_usage={
                    "steps": 0,
                    "candidates": 0,
                    "degraded": True,
                    "call_gate": call_gate,
                },
                checks_complete=True,
                extras={
                    "degraded": True,
                    "action": call_gate.get("action"),
                    "correlation_id": correlation_id,
                },
            )

        # Bank 权限：低权限不能读其他 Bank
        if principal is not None and hasattr(principal, "allows_bank"):
            if not principal.allows_bank(bank_id):
                return self._result(
                    decision_id,
                    trace_id,
                    status="unknown",
                    reason_codes=["bank_denied"],
                    budget_usage={"steps": 0, "candidates": 0},
                )

        resolved_target, parse_meta = self._resolve_target(query=query, target=target)
        if resolved_target is None:
            return self._result(
                decision_id,
                trace_id,
                status="unknown",
                reason_codes=list(parse_meta.get("reason_codes") or ["unsupported_query", "needs_target"]),
                budget_usage={"steps": 1, "candidates": 0},
                checks_complete=True,
                extras={"parse": parse_meta},
            )

        subject = str(resolved_target.get("subject_id") or resolved_target.get("subject") or "")
        predicate = str(resolved_target.get("predicate") or "")
        want_negation = resolved_target.get("negated")
        target_arguments = resolved_target.get("arguments")
        if target_arguments is not None and not isinstance(target_arguments, list):
            target_arguments = list(target_arguments) if target_arguments else None
        # 未指定或空列表 = 不约束 arguments；仅显式非空目标才要求精确匹配
        if isinstance(target_arguments, list) and len(target_arguments) == 0:
            target_arguments = None
        steps = 1
        candidates_raw = self._list_bank_claims(bank_id=bank_id, limit=max(50, max_candidates * 4))
        matched: list[dict[str, Any]] = []
        counter_pending = 0

        for claim in candidates_raw:
            if steps >= max_steps:
                break
            steps += 1
            if len(matched) >= max_candidates:
                counter_pending += 1
                continue
            pack = self._match_claim(
                claim,
                subject=subject,
                predicate=predicate,
                at=at_dt,
                mode=mode,
                context=ctx,
                want_negation=want_negation,
                target_arguments=target_arguments,
            )
            if pack is None:
                continue
            if pack.get("role") == "counter":
                counter_pending += 1
            matched.append(pack)

        budget_exhausted = steps >= max_steps or counter_pending > 0 and len(
            [m for m in matched if m.get("role") == "counter"]
        ) < counter_pending and len(matched) >= max_candidates

        # 修订竞态重查（E02 衔接）
        if expected_source_revisions:
            race = self.derivations.recheck_revisions(
                expected_source_revisions,
                current_revisions=dict(current_source_revisions or {}),
            )
            if not race.get("chosen_allowed"):
                return self._result(
                    decision_id,
                    trace_id,
                    status="unknown",
                    reason_codes=list(race.get("reason_codes") or ["source_stale"]),
                    selected=[],
                    alternatives=matched[:5],
                    budget_usage={"steps": steps, "candidates": len(matched)},
                    checks_complete=False,
                    extras={"revision_recheck": race},
                )

        if budget_exhausted and not matched:
            return self._result(
                decision_id,
                trace_id,
                status="unknown",
                reason_codes=["budget_exhausted", "counterevidence_pending"],
                budget_usage={"steps": steps, "candidates": 0, "counter_pending": counter_pending},
                checks_complete=False,
            )

        if not matched:
            reasons = ["source_missing"]
            if ctx.get("require_daytime_permanent") or ctx.get("query_scope") == "daytime_permanent":
                reasons = ["context_mismatch", "coverage_insufficient"]
            return self._result(
                decision_id,
                trace_id,
                status="unknown",
                reason_codes=reasons,
                budget_usage={"steps": steps, "candidates": 0},
                checks_complete=True,
                extras={"target": resolved_target},
            )

        # 情境不足：仅有条件/时段声明却要求永久/其他时段
        scoped_only = [
            m
            for m in matched
            if m.get("applicability", {}).get("conditional")
            or m.get("applicability", {}).get("situation")
        ]
        if (
            ctx.get("query_scope") in {"daytime_permanent", "permanent"}
            and scoped_only
            and all(m in scoped_only for m in matched)
        ):
            return self._result(
                decision_id,
                trace_id,
                status="unknown",
                reason_codes=["context_mismatch", "coverage_insufficient"],
                selected=[],
                alternatives=matched,
                evidence_refs=[m["ref"] for m in matched],
                budget_usage={"steps": steps, "candidates": len(matched)},
                checks_complete=True,
                applicability={"query_scope": ctx.get("query_scope"), "matched_scope": "situational_only"},
                extras={"target": resolved_target},
            )

        # 排序：lifecycle 优先；current 模式下较新 validity_from 优先
        life_rank = {"verified": 0, "supported": 1, "proposed": 2}

        def sort_key(item: dict[str, Any]) -> tuple:
            rank = life_rank.get(item.get("lifecycle") or "", 9)
            vf = item.get("validity_from") or ""
            # reverse chron via inverted string key for current
            return (rank, vf if mode == "history" else "".join(chr(255 - ord(c)) for c in vf))

        matched_sorted = sorted(matched, key=sort_key)

        primary = matched_sorted[0]
        alternatives = matched_sorted[1:]
        self_report = primary.get("epistemic_tag") == "self_report"
        counter_refs = [m["ref"] for m in matched if m.get("role") == "counter"]

        if budget_exhausted or counter_pending:
            status = "partial" if matched else "unknown"
            reasons = ["budget_exhausted"]
            if counter_pending:
                reasons.append("counterevidence_pending")
            return self._result(
                decision_id,
                trace_id,
                status=status,
                reason_codes=reasons,
                selected=[primary] if status == "partial" else [],
                alternatives=alternatives,
                evidence_refs=[primary["ref"]],
                counterevidence_refs=counter_refs,
                budget_usage={
                    "steps": steps,
                    "candidates": len(matched),
                    "counter_pending": counter_pending,
                },
                checks_complete=False,
                applicability=primary.get("applicability"),
                extras={"target": resolved_target, "self_report": self_report},
            )

        # chosen：有限结论，不写库 verified
        status = "chosen"
        reasons = ["selected"]
        if self_report:
            reasons.append("self_report")
        return self._result(
            decision_id,
            trace_id,
            status=status,
            reason_codes=reasons,
            selected=[primary],
            alternatives=alternatives,
            evidence_refs=[primary["ref"]],
            counterevidence_refs=counter_refs,
            source_revisions=list(expected_source_revisions or []),
            budget_usage={"steps": steps, "candidates": len(matched)},
            checks_complete=True,
            applicability=primary.get("applicability"),
            extras={
                "target": resolved_target,
                "self_report": self_report,
                "wrote_verified": False,
                "mode": mode,
                "at": at_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )

    def _resolve_target(
        self, *, query: str, target: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if isinstance(target, dict) and (
            target.get("subject_id") or target.get("subject")
        ) and target.get("predicate"):
            return {
                "subject_id": str(target.get("subject_id") or target.get("subject")),
                "predicate": str(target.get("predicate")),
                "negated": bool(target.get("negated", False)) if "negated" in target else None,
                "arguments": list(target.get("arguments") or []),
            }, {"source": "structured_target"}

        text = str(query or "").strip()
        if not text:
            return None, {"reason_codes": ["needs_target", "unsupported_query"]}

        parsed = parse_memory_text(text)
        ast: ClaimAST = parsed.ast
        if ast.is_unresolved() or isinstance(ast.root, Unresolved):
            return None, {
                "reason_codes": ["unsupported_query", "needs_target"],
                "parse_notes": parsed.notes,
            }
        atom, negated, hints = _unwrap_atom(ast.root)
        if atom is None:
            return None, {"reason_codes": ["unsupported_query", "needs_target"]}
        return {
            "subject_id": atom.subject,
            "predicate": atom.predicate,
            "negated": negated,
            "arguments": list(atom.arguments),
            "hints": hints,
        }, {"source": "parser", "notes": parsed.notes}

    def _list_bank_claims(self, *, bank_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT id FROM claims
                WHERE bank_id=? AND redirects_to IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (bank_id, int(limit)),
            ).fetchall()
        out = []
        for row in rows:
            pack = self.claims.get(row["id"], follow_redirect=False)
            if pack:
                out.append(pack)
        return out

    def _match_claim(
        self,
        claim: dict[str, Any],
        *,
        subject: str,
        predicate: str,
        at: datetime,
        mode: str,
        context: dict[str, Any],
        want_negation: bool | None,
        target_arguments: list[Any] | None = None,
    ) -> dict[str, Any] | None:
        life = str(claim.get("lifecycle") or "")
        # rejected/retracted/expired 不得进入 chosen 候选
        if life in {"retracted", "rejected"}:
            return None
        # history：按 as-of 有效；current：要求当下仍有效
        vf = claim.get("validity_from")
        vt = claim.get("validity_to")
        if mode == "current":
            if life in {"superseded", "expired"}:
                # 若仍落在有效区间内（尚未到 to）可作历史，但 current 不用
                if not _in_interval(at, vf, vt):
                    return None
                if life == "superseded" and vt and _parse_ts(vt) and at >= _parse_ts(vt):
                    return None
                if life in {"superseded", "expired"}:
                    return None
            if not _in_interval(at, vf, vt):
                return None
        else:
            # history：只要 as-of 落在区间
            if not _in_interval(at, vf, vt):
                # 无 validity_from 的旧 claim：用 created_at 作为起点
                created = claim.get("created_at")
                if not _in_interval(at, vf or created, vt):
                    return None
            if life in {"expired"} and not _in_interval(at, vf, vt):
                return None

        ast_raw = claim.get("ast") or {}
        try:
            ast = ClaimAST.model_validate(ast_raw) if not isinstance(ast_raw, ClaimAST) else ast_raw
        except Exception:
            return None
        atom, negated, hints = _unwrap_atom(ast.root)
        if atom is None:
            return None
        if atom.subject != subject:
            return None
        # predicate 宽松：完全匹配或同义偏好
        pred_ok = atom.predicate == predicate or (
            predicate in {"偏好", "likes", "喜欢"} and atom.predicate in {"偏好", "likes", "喜欢"}
        ) or (
            predicate in {"喝", "drinks"} and atom.predicate in {"喝", "drinks", "偏好"}
        )
        if not pred_ok and predicate not in atom.arguments and predicate not in (atom.predicate,):
            # 允许 target.predicate 出现在 arguments（如 喝咖啡）
            if predicate and predicate not in "".join(atom.arguments):
                if not (predicate in atom.predicate or any(predicate in a for a in atom.arguments)):
                    return None

        # 完整目标 Atom：目标 arguments 必须精确匹配（同主体同谓词不同对象不得错配）
        if target_arguments is not None:
            want_args = [str(a) for a in target_arguments]
            have_args = [str(a) for a in list(atom.arguments or [])]
            if want_args != have_args:
                # 宽松：若目标只有一个对象，允许在 claim arguments 中精确命中该对象
                if not (
                    len(want_args) == 1
                    and want_args[0] in have_args
                    and len(have_args) == 1
                ):
                    return None

        if want_negation is True and not negated:
            return None
        if want_negation is False and negated:
            # 查询肯定时，否定声明作为 counter
            pass

        applicability = {
            "validity_from": vf,
            "validity_to": vt,
            "negated": negated,
            "conditional": bool(hints.get("conditional") or ast.scope.get("situation") or context.get("situation")),
            "situation": ast.scope.get("situation") or hints.get("condition"),
        }
        # 晚上情境等
        surface = str(claim.get("surface") or "")
        subj = str(atom.subject or "")
        if any(
            tok in surface or tok in subj
            for tok in ("晚上", "夜间", "only at night", "evening")
        ):
            applicability["situation"] = applicability.get("situation") or "evening"
            applicability["conditional"] = True

        epistemic = "claim"
        ast_scope = {}
        if isinstance(claim.get("ast"), dict):
            ast_scope = claim["ast"].get("scope") if isinstance(claim["ast"].get("scope"), dict) else {}
        meta_scope = claim.get("scope") if isinstance(claim.get("scope"), dict) else {}
        merged_scope = {**ast_scope, **meta_scope}
        if merged_scope.get("self_report") or merged_scope.get("epistemic") == "self_report":
            epistemic = "self_report"
        # 自述例外必须可追溯（显式 scope），不因 preference 语法自动洗成 self_report。
        # 来源资格：没有 source_memory_id / evidence / verification evidence 的候选
        # 不能进入确定选择；仅声明 wrote_verified=false 不能替代来源检查。
        if not self._has_traceable_source(claim):
            return None

        role = "counter" if (want_negation is False and negated) else "support"
        return {
            "ref_type": "claim",
            "ref": claim["id"],
            "claim_id": claim["id"],
            "lifecycle": life,
            "epistemic_tag": epistemic,
            "negated": negated,
            "surface": surface,
            "validity_from": vf,
            "validity_to": vt,
            "applicability": applicability,
            "role": role,
            "subject": atom.subject,
            "predicate": atom.predicate,
            "arguments": list(atom.arguments),
        }

    def _has_traceable_source(self, claim: dict[str, Any]) -> bool:
        """A claim is eligible for chosen only when its provenance is traceable.

        This is deliberately narrower than "content is true": a user self-report
        with an explicit source/evidence can still be chosen; a sourceless
        proposed claim cannot.
        """
        source_id = str(claim.get("source_memory_id") or "").strip()
        if source_id:
            try:
                row = self.service.db.connect().execute(
                    "SELECT status FROM memories WHERE id=?", (source_id,)
                ).fetchone()
                if row is not None and str(row["status"]) != "invalidated":
                    return True
            except Exception:
                pass
        claim_id = str(claim.get("id") or "")
        if not claim_id:
            return False
        from cognitive_memory.evidence.object_store import EvidenceObjectStore

        store = EvidenceObjectStore(self.service.db)
        try:
            refs = [e["evidence_ref"] for e in self.claims.list_evidence(claim_id)]
        except Exception:
            refs = []
        try:
            rec = self.claims._ver_store.latest_for_claim(claim_id)
            if rec is not None:
                refs.extend(getattr(rec, "evidence_refs", None) or [])
        except Exception:
            pass
        for ref in set(refs):
            try:
                if store.get(ref):
                    return True
            except Exception:
                continue
        return False

    def _enforce_call_budget(
        self,
        *,
        correlation_id: str,
        attempt: int,
        budget: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any] | None:
        db = getattr(self.service, "db", None)
        if db is None:
            return None
        from cognitive_memory.tracing.cost_ledger import UsageLedger

        limits = {}
        cfg = getattr(self.service, "call_budget_config", None) or getattr(
            self.service, "context_budget_config", None
        )
        if isinstance(cfg, dict):
            for key in ("max_external_calls", "max_retrieval_rounds", "max_duration_ms"):
                if key in cfg:
                    limits[key] = cfg[key]
        for key in ("max_external_calls", "max_retrieval_rounds", "max_duration_ms"):
            if key in budget:
                limits[key] = budget[key]
        ledger = UsageLedger(db, limits=limits or None)
        # 宿主 usage 缺失时记 unknown（不转零）
        if not ctx.get("host_usage") and not budget.get("host_usage"):
            ledger.record_host_usage_missing(
                correlation_id=correlation_id, attempt=attempt
            )
        duration_ms = float(ctx.get("duration_ms_so_far") or budget.get("duration_ms_so_far") or 0)
        # 答案选择计一次检索轮；若显式 external 则再检查外部调用
        gate = ledger.check_and_enter(
            correlation_id=correlation_id,
            call_kind="retrieval",
            attempt=attempt,
            duration_ms_so_far=duration_ms,
            retrieval_round=int(ctx.get("retrieval_round") or budget.get("retrieval_round") or 0) or None,
        )
        if not gate.get("allowed"):
            return gate
        if budget.get("count_as_external") or ctx.get("count_as_external"):
            gate2 = ledger.check_and_enter(
                correlation_id=correlation_id,
                call_kind="external",
                attempt=attempt,
                duration_ms_so_far=duration_ms,
            )
            if not gate2.get("allowed"):
                return gate2
            return gate2
        return gate

    def _result(
        self,
        decision_id: str,
        trace_id: str,
        *,
        status: str,
        reason_codes: list[str],
        selected: list[dict[str, Any]] | None = None,
        alternatives: list[dict[str, Any]] | None = None,
        evidence_refs: list[str] | None = None,
        counterevidence_refs: list[str] | None = None,
        source_revisions: list[dict[str, Any]] | None = None,
        budget_usage: dict[str, Any] | None = None,
        checks_complete: bool = False,
        applicability: dict[str, Any] | None = None,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        out = {
            "decision_id": decision_id,
            "status": status,
            "selected": list(selected or []),
            "alternatives": list(alternatives or []),
            "evidence_refs": list(evidence_refs or []),
            "counterevidence_refs": list(counterevidence_refs or []),
            "source_revisions": list(source_revisions or []),
            "applicability": applicability or {},
            "checks_complete": checks_complete,
            "reason_codes": list(reason_codes or []),
            "budget_usage": budget_usage or {},
            "trace_id": trace_id,
            "wrote_verified": False,
        }
        if extras:
            out["meta"] = extras
            out["extras"] = extras
        return out
