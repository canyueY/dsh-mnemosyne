# -*- coding: utf-8 -*-
"""冲突治理：用户决策 + 预览提交 + TMS。不自动 Verified，不静默删任一侧。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.domain.models import MemoryStatus, MemoryUpdateIn

SCHEMES = (
    "keep_a_edit_b",
    "keep_b_edit_a",
    "merge",
    "qualify_scope",
    "edit_both",
    "invalidate_or_dismiss",
)

STATUS_OPEN = "open"
STATUS_PENDING = "pending_review"
STATUS_RESOLVED = "resolved"
STATUS_DISMISSED = "dismissed"
STATUS_REVERTED = "reverted"

_NEG = re.compile(r"(不喜欢|讨厌|不是|并非|否认|反对|don't|does not|hate|dislike)", re.I)
_POS = re.compile(r"(喜欢|爱好|是|确实|支持|like|love|prefer)", re.I)
_TIME = re.compile(r"(以前|曾经|现在|后来|去年|今年|当时|valid_from|20\d{2})", re.I)
_COND = re.compile(r"(如果|当|在.+情况下|条件|除非|if |when |unless )", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "cf") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _meta(mem: dict[str, Any]) -> dict[str, Any]:
    raw = mem.get("metadata") if isinstance(mem.get("metadata"), dict) else {}
    return dict(raw or {})


def _edit_from_ast(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    root = node.get("root") if isinstance(node.get("root"), dict) else node
    kind = str(root.get("kind") or "")
    out: dict[str, Any] = {}
    if kind == "unresolved":
        out["unresolved"] = True
        return out
    if kind == "negation":
        out["polarity"] = "negative"
        nested = _edit_from_ast(root.get("target") or {})
        nested.update(out)
        nested["polarity"] = "negative"
        return nested
    if kind == "temporal":
        interval = root.get("interval") if isinstance(root.get("interval"), dict) else {}
        nested = _edit_from_ast(root.get("target") or {})
        nested["temporal"] = {"start": interval.get("start") or "", "end": interval.get("end") or ""}
        return nested
    if kind == "conditional":
        nested = _edit_from_ast(root.get("consequence") or {})
        cond = root.get("condition") if isinstance(root.get("condition"), dict) else {}
        nested["conditional"] = str(cond.get("raw_text") or cond.get("predicate") or cond.get("kind") or "")
        return nested
    if kind == "atom":
        args = root.get("arguments") or []
        out["subject"] = str(root.get("subject") or "")
        out["predicate"] = str(root.get("predicate") or "")
        out["object"] = str(args[0] if args else "")
        out["modality"] = str(root.get("modality") or "unknown")
        out["polarity"] = "positive"
        out["unresolved"] = not (out["subject"] and out["predicate"])
        return out
    return {"unresolved": True}


def _as_memory_from_claim(pack: dict[str, Any], evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ast = pack.get("ast") if isinstance(pack.get("ast"), dict) else {}
    edit = _edit_from_ast(ast)
    content = str(pack.get("surface") or "")
    return {
        "id": pack.get("id"),
        "content": content,
        "status": pack.get("lifecycle") or pack.get("verification_status") or "candidate",
        "type": "semantic",
        "bank_id": pack.get("bank_id") or "personal",
        "version": pack.get("version") or 1,
        "confidence": pack.get("confidence"),
        "created_at": pack.get("created_at"),
        "updated_at": pack.get("updated_at"),
        "metadata": {
            "claim_edit": edit,
            "ast": ast,
            "verification_status": pack.get("verification_status") or "unverified",
        },
        "evidence": evidence or [],
        "entity_kind": "claim",
        "lifecycle": pack.get("lifecycle"),
        "surface": content,
    }


def _load_entity(service: Any, mid: str) -> dict[str, Any] | None:
    mid = str(mid or "").strip()
    if not mid:
        return None
    try:
        mem = service.get_memory(mid, include_evidence=True)
        if isinstance(mem, dict) and mem.get("id"):
            mem = dict(mem)
            mem.setdefault("entity_kind", "memory")
            return mem
    except Exception:
        pass
    try:
        from cognitive_memory.claims.store import ClaimStore

        store = ClaimStore(service.db)
        pack = store.get(mid, follow_redirect=True)
        if not pack:
            return None
        resolved = pack.get("redirect_resolved") if isinstance(pack.get("redirect_resolved"), dict) else pack
        successor = str(resolved.get("superseded_by") or resolved.get("redirects_to") or "")
        if successor and successor != str(resolved.get("id") or mid):
            nxt = store.get(successor, follow_redirect=True)
            if nxt:
                resolved = nxt.get("redirect_resolved") if isinstance(nxt.get("redirect_resolved"), dict) else nxt
        ev = []
        try:
            ev = store.list_evidence(str(resolved.get("id") or mid))
        except Exception:
            ev = []
        shown = _as_memory_from_claim(resolved, ev)
        shown["id"] = pack.get("id") or mid
        shown["superseded_by"] = successor or None
        shown["display_id"] = resolved.get("id")
        return shown
    except Exception:
        return None


def _claim_fields(mem: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(mem)
    edit = meta.get("claim_edit") if isinstance(meta.get("claim_edit"), dict) else {}
    if not edit and isinstance(mem.get("ast"), dict):
        edit = _edit_from_ast(mem.get("ast") or {})
    content = str(mem.get("content") or mem.get("surface") or "")
    return {
        "id": mem.get("id"),
        "content": content,
        "status": mem.get("status"),
        "type": mem.get("type"),
        "bank_id": mem.get("bank_id") or "personal",
        "version": mem.get("version"),
        "confidence": mem.get("confidence"),
        "created_at": mem.get("created_at"),
        "updated_at": mem.get("updated_at"),
        "polarity": edit.get("polarity") or ("negative" if _NEG.search(content) else "positive" if _POS.search(content) else "unknown"),
        "modality": edit.get("modality") or "unknown",
        "subject": edit.get("subject") or "",
        "predicate": edit.get("predicate") or "",
        "object": edit.get("object") or "",
        "temporal_start": (edit.get("temporal") or {}).get("start") if isinstance(edit.get("temporal"), dict) else edit.get("temporal_start") or "",
        "temporal_end": (edit.get("temporal") or {}).get("end") if isinstance(edit.get("temporal"), dict) else edit.get("temporal_end") or "",
        "conditional": edit.get("conditional") or "",
        "unresolved": bool(edit.get("unresolved")),
    }


def _lookup_text(db: Any, ref: str, _seen: set[str] | None = None) -> dict[str, Any]:
    rid = str(ref or "").strip()
    if not rid:
        return {}
    seen = _seen if _seen is not None else set()
    if rid in seen:
        return {}
    seen.add(rid)
    conn = db.connect()
    try:
        obs = conn.execute(
            "SELECT id, content, source_kind, producer, created_at FROM observations WHERE id=?",
            (rid,),
        ).fetchone()
        if obs:
            return {
                "content": obs["content"] or "",
                "source_kind": obs["source_kind"] or "",
                "producer": obs["producer"] or "",
                "observation_id": obs["id"],
                "created_at": obs["created_at"],
            }
        ev = conn.execute("SELECT * FROM evidences WHERE id=?", (rid,)).fetchone()
        if ev:
            nested = _lookup_text(db, str(ev["source_ref"] or ""), seen)
            meta = {}
            try:
                meta = json.loads(ev["metadata_json"] or "{}")
            except Exception:
                meta = {}
            text = nested.get("content") or str(meta.get("content") or meta.get("text") or "")
            return {
                "content": text,
                "source_kind": nested.get("source_kind") or ev["source_type"] or "",
                "producer": nested.get("producer") or ev["producer"] or "",
                "observation_id": nested.get("observation_id") or ev["source_ref"] or "",
                "evidence_id": ev["id"],
                "created_at": nested.get("created_at") or ev["created_at"],
            }
        mem = conn.execute("SELECT id, content FROM memories WHERE id=?", (rid,)).fetchone()
        if mem:
            return {"content": mem["content"] or "", "memory_id": mem["id"]}
    except Exception:
        return {}
    return {}


def _hydrate_evidence(db: Any, rows: list[Any], *, owner_id: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in rows or []:
        item = dict(raw) if isinstance(raw, dict) else {}
        packed: dict[str, Any] = {}
        for key in ("evidence_ref", "observation_id", "id", "source_ref"):
            packed = _lookup_text(db, str(item.get(key) or ""))
            if packed.get("content"):
                break
        item["content"] = packed.get("content") or str(item.get("note") or "")
        item["source_kind"] = packed.get("source_kind") or item.get("evidence_kind") or item.get("source_type") or ""
        item["source_ref"] = packed.get("observation_id") or packed.get("source_ref") or item.get("observation_id") or item.get("evidence_ref") or ""
        item["producer"] = packed.get("producer") or item.get("producer") or ""
        if owner_id:
            item.setdefault("memory_id", owner_id)
        out.append(item)
    return out


def classify_kind(a: dict[str, Any], b: dict[str, Any]) -> str:
    ta, tb = str(a.get("content") or ""), str(b.get("content") or "")
    fa, fb = _claim_fields(a), _claim_fields(b)
    if fa.get("polarity") != fb.get("polarity") and "unknown" not in (fa.get("polarity"), fb.get("polarity")):
        return "polarity"
    if _TIME.search(ta) or _TIME.search(tb) or fa.get("temporal_start") != fb.get("temporal_start"):
        return "temporal"
    if _COND.search(ta) or _COND.search(tb) or fa.get("conditional") or fb.get("conditional"):
        return "conditional"
    return "semantic"


def _safe_get(service: Any, mid: str) -> dict[str, Any] | None:
    return _load_entity(service, mid)


class ConflictGovernance:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.db = service.db

    def _conn(self):
        return self.db.connect()

    def sync_from_relations(self) -> int:
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT * FROM memory_relations
            WHERE relation='contradicts'
            ORDER BY id ASC
            """
        ).fetchall()
        added = 0
        for row in rows:
            rid = int(row["id"])
            cid = f"cf_{rid}"
            existing = conn.execute("SELECT id FROM conflict_cases WHERE id=?", (cid,)).fetchone()
            if existing:
                continue
            a = _safe_get(self.service, str(row["from_id"]))
            b = _safe_get(self.service, str(row["to_id"]))
            if not a or not b:
                continue
            kind = classify_kind(a, b)
            now = _now()
            with self.db.transaction() as tx:
                tx.execute(
                    """
                    INSERT INTO conflict_cases(
                      id, relation_id, from_id, to_id, kind, status, priority,
                      snapshot_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        rid,
                        a["id"],
                        b["id"],
                        kind,
                        STATUS_OPEN,
                        "high" if kind == "polarity" else "normal",
                        json.dumps({"a": a, "b": b}, ensure_ascii=False, default=str),
                        str(row["created_at"] or now),
                        now,
                    ),
                )
            added += 1
        return added

    def list_cases(self, *, kind: str = "", status: str = "") -> dict[str, Any]:
        self.sync_from_relations()
        conn = self._conn()
        sql = "SELECT * FROM conflict_cases WHERE 1=1"
        args: list[Any] = []
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if status and status != "all":
            sql += " AND status=?"
            args.append(status)
        elif status != "all":
            sql += " AND status IN (?,?)"
            args.extend((STATUS_OPEN, STATUS_PENDING))
        sql += " ORDER BY updated_at DESC"
        items = []
        for row in conn.execute(sql, args):
            items.append(self._case_row(row, light=True))
        stats = {
            "total": len(items) if not kind and not status else conn.execute("SELECT COUNT(*) FROM conflict_cases").fetchone()[0],
            "open": conn.execute("SELECT COUNT(*) FROM conflict_cases WHERE status=?", (STATUS_OPEN,)).fetchone()[0],
            "high": conn.execute("SELECT COUNT(*) FROM conflict_cases WHERE priority='high' AND status!=?", (STATUS_RESOLVED,)).fetchone()[0],
            "pending_review": conn.execute("SELECT COUNT(*) FROM conflict_cases WHERE status=?", (STATUS_PENDING,)).fetchone()[0],
            "resolved": conn.execute("SELECT COUNT(*) FROM conflict_cases WHERE status=?", (STATUS_RESOLVED,)).fetchone()[0],
            "reverted": conn.execute("SELECT COUNT(*) FROM conflict_cases WHERE status=?", (STATUS_REVERTED,)).fetchone()[0],
            "dismissed": conn.execute("SELECT COUNT(*) FROM conflict_cases WHERE status=?", (STATUS_DISMISSED,)).fetchone()[0],
        }
        if kind or status:
            stats["filtered"] = len(items)
        return {"ok": True, "items": items, "count": len(items), "stats": stats}

    def _case_row(self, row: Any, *, light: bool = False) -> dict[str, Any]:
        a = _safe_get(self.service, str(row["from_id"])) or {"id": row["from_id"], "content": ""}
        b = _safe_get(self.service, str(row["to_id"])) or {"id": row["to_id"], "content": ""}
        ev_a = list(a.get("evidence") or []) if isinstance(a, dict) else []
        ev_b = list(b.get("evidence") or []) if isinstance(b, dict) else []
        out = {
            "id": row["id"],
            "relation_id": row["relation_id"],
            "kind": row["kind"],
            "kind_label": {
                "polarity": "极性冲突",
                "temporal": "时间冲突",
                "conditional": "条件冲突",
                "semantic": "语义冲突",
                "evidence": "证据冲突",
                "relation": "关系冲突",
            }.get(str(row["kind"]), str(row["kind"])),
            "status": row["status"],
            "priority": row["priority"],
            "from_id": row["from_id"],
            "to_id": row["to_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "a": _claim_fields(a),
            "b": _claim_fields(b),
            "evidence_count": len(ev_a) + len(ev_b),
            "confidence_a": a.get("confidence") if isinstance(a, dict) else None,
            "confidence_b": b.get("confidence") if isinstance(b, dict) else None,
        }
        if not light:
            out["a_full"] = a
            out["b_full"] = b
            out["evidence_a"] = _hydrate_evidence(self.db, ev_a, owner_id=str(a.get("id") or ""))
            out["evidence_b"] = _hydrate_evidence(self.db, ev_b, owner_id=str(b.get("id") or ""))
            out["snapshot"] = json.loads(row["snapshot_json"] or "{}")
            out["last_resolution"] = json.loads(row["last_resolution_json"] or "null")
        return out

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        self.sync_from_relations()
        conn = self._conn()
        row = conn.execute("SELECT * FROM conflict_cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            row = conn.execute(
                """
                SELECT * FROM conflict_cases
                WHERE from_id=? OR to_id=?
                ORDER BY updated_at DESC
                """,
                (case_id, case_id),
            ).fetchone()
        if not row:
            return None
        pack = self._case_row(row, light=False)
        pack["history"] = self.history(case_id)["items"]
        return pack

    def analyze(self, case_id: str) -> dict[str, Any]:
        case = self.get_case(case_id)
        if not case:
            return {"ok": False, "detail": "conflict_not_found"}
        a, b = case["a"], case["b"]
        reasons = []
        kind = case["kind"]
        if kind == "polarity":
            reasons.append("真实极性/肯否冲突：双方对同一对象的态度或命题方向相反。")
        elif kind == "temporal":
            reasons.append("时间范围可能不一致，未必是事实互斥。")
        elif kind == "conditional":
            reasons.append("条件范围可能不同，可同时成立。")
        else:
            reasons.append("语义相近但表述冲突，或解析不完整（Unresolved）。")
        if a.get("unresolved") or b.get("unresolved"):
            reasons.append("结构化字段不完整，不能伪造解析成功。")
        if (case.get("evidence_count") or 0) < 2:
            reasons.append("证据不足：优先 Unresolved / needs_review，而不是强行消除冲突。")
        suggestions = []
        for scheme, title, risk in (
            ("keep_a_edit_b", "保留 A，修正 B", "B 的原始表述进入版本历史，不删除。"),
            ("keep_b_edit_a", "保留 B，修正 A", "A 的原始表述进入版本历史，不删除。"),
            ("qualify_scope", "保留双方，用时间/条件消解", "若依据不足则保持待核实。"),
            ("merge", "合并为一条派生 Claim", "原始两侧必须保留，只新增 derived_from。"),
            ("edit_both", "同时修改双方", "两侧都可能部分不准确。"),
            ("invalidate_or_dismiss", "软删除错误侧或撤销误判", "不级联删除独立 Evidence。"),
        ):
            suggestions.append({
                "scheme": scheme,
                "title": title,
                "explanation": reasons[0],
                "target": "B" if scheme == "keep_a_edit_b" else "A" if scheme == "keep_b_edit_a" else "both",
                "before": {"a": a.get("content"), "b": b.get("content")},
                "after": self._draft_after(scheme, a, b),
                "evidence_basis": f"A evid {len(case.get('evidence_a') or [])} / B evid {len(case.get('evidence_b') or [])}",
                "confidence": "low" if (case.get("evidence_count") or 0) < 2 else "medium",
                "risk": risk,
                "impact_hint": "提交后将重算 contradicts / qualifies，Verified 不会被 AI 自动赋予。",
                "verification_status": "unverified",
            })
        return {
            "ok": True,
            "case_id": case_id,
            "reasons": reasons,
            "kind": kind,
            "suggestions": suggestions,
            "note": "AI/规则建议不得自动升为 Verified；最终决策权属于用户。",
        }

    def _draft_after(self, scheme: str, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        if scheme == "qualify_scope":
            return {
                "a": (a.get("content") or "") + " 〔待补时间/条件〕",
                "b": (b.get("content") or "") + " 〔待补时间/条件〕",
            }
        if scheme == "merge":
            return {"merged": f"{a.get('content') or ''}；同时 {b.get('content') or ''}".strip("；同时 ")}
        if scheme == "invalidate_or_dismiss":
            return {"dismiss": True}
        return {"a": a.get("content"), "b": b.get("content")}

    def preview(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        case = self.get_case(case_id)
        if not case:
            return {"ok": False, "detail": "conflict_not_found"}
        ops = self._plan_ops(case, payload)
        a_id, b_id = case["from_id"], case["to_id"]
        related = self._impact(a_id, b_id)
        return {
            "ok": True,
            "case_id": case_id,
            "scheme": payload.get("scheme"),
            "confirm_required": True,
            "ops": ops,
            "impact": related,
            "version_guard": {
                "a": {"id": a_id, "expected_version": case["a"].get("version")},
                "b": {"id": b_id, "expected_version": case["b"].get("version")},
            },
            "note": "只有用户确认后才会写入 Memory Bank。",
        }

    def _plan_ops(self, case: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        scheme = str(payload.get("scheme") or "")
        if scheme not in SCHEMES:
            return [{"op": "error", "detail": "unknown_scheme"}]
        a_edit = payload.get("a") if isinstance(payload.get("a"), dict) else {}
        b_edit = payload.get("b") if isinstance(payload.get("b"), dict) else {}
        ops: list[dict[str, Any]] = []
        if scheme == "keep_a_edit_b":
            ops.append({"op": "update_memory", "id": case["to_id"], "fields": b_edit, "role": "B"})
            ops.append({"op": "retarget_relation", "relation_id": case["relation_id"], "relation": "qualifies"})
        elif scheme == "keep_b_edit_a":
            ops.append({"op": "update_memory", "id": case["from_id"], "fields": a_edit, "role": "A"})
            ops.append({"op": "retarget_relation", "relation_id": case["relation_id"], "relation": "qualifies"})
        elif scheme == "edit_both":
            ops.append({"op": "update_memory", "id": case["from_id"], "fields": a_edit, "role": "A"})
            ops.append({"op": "update_memory", "id": case["to_id"], "fields": b_edit, "role": "B"})
            ops.append({"op": "retarget_relation", "relation_id": case["relation_id"], "relation": "qualifies"})
        elif scheme == "qualify_scope":
            ops.append({"op": "update_memory", "id": case["from_id"], "fields": a_edit, "role": "A"})
            ops.append({"op": "update_memory", "id": case["to_id"], "fields": b_edit, "role": "B"})
            ops.append({"op": "retarget_relation", "relation_id": case["relation_id"], "relation": "qualifies"})
        elif scheme == "merge":
            merged = str(payload.get("merged_content") or "").strip()
            ops.append({"op": "create_derived", "from_ids": [case["from_id"], case["to_id"]], "content": merged})
            ops.append({"op": "retarget_relation", "relation_id": case["relation_id"], "relation": "qualifies"})
        elif scheme == "invalidate_or_dismiss":
            target = str(payload.get("invalidate") or "").strip()
            if target in {case["from_id"], case["to_id"]}:
                ops.append({"op": "invalidate_memory", "id": target})
                ops.append({"op": "dismiss_conflict", "relation_id": case["relation_id"]})
            elif payload.get("dismiss"):
                ops.append({"op": "dismiss_conflict", "relation_id": case["relation_id"]})
            else:
                ops.append({"op": "dismiss_conflict", "relation_id": case["relation_id"]})
        for link in payload.get("evidence_attach") or []:
            if isinstance(link, dict):
                ops.append({"op": "attach_evidence", **link})
        for link in payload.get("evidence_detach") or []:
            if isinstance(link, dict):
                ops.append({"op": "detach_evidence", **link})
        return ops

    def _impact(self, a_id: str, b_id: str) -> dict[str, Any]:
        related: list[dict[str, Any]] = []
        knowledge_revalidate: list[str] = []
        for mid in (a_id, b_id):
            rels = self.service.list_relations(mid) or []
            for rel in rels:
                related.append({"id": rel.get("id"), "relation": rel.get("relation"), "from": rel.get("from_id"), "to": rel.get("to_id")})
                other = rel.get("to_id") if rel.get("from_id") == mid else rel.get("from_id")
                if not other or str(other) in {a_id, b_id}:
                    continue
                mem = _safe_get(self.service, str(other))
                if not mem:
                    continue
                typ = mem.get("type")
                typ_s = str(getattr(typ, "value", typ) or "").lower()
                content = str(mem.get("content") or "")
                meta = _meta(mem)
                if (
                    "知识" in content
                    or meta.get("verification_status") == "verified"
                    or "knowledge" in typ_s
                ):
                    knowledge_revalidate.append(str(mem["id"]))
        tms = None
        try:
            from cognitive_memory.claims.store import ClaimStore

            store = ClaimStore(self.db)
            tms = {
                "a": store.tms.get_belief(a_id).model_dump(mode="json") if hasattr(store.tms, "get_belief") else None,
                "b": store.tms.get_belief(b_id).model_dump(mode="json") if hasattr(store.tms, "get_belief") else None,
            }
        except Exception:
            tms = {"available": False}
        return {
            "direct_relations": related[:40],
            "knowledge_revalidate": list(dict.fromkeys(knowledge_revalidate)),
            "and_or_dependencies": "recorded_via_tms_if_present",
            "new_conflict_rescan": True,
            "tms": tms,
        }

    def commit(self, case_id: str, payload: dict[str, Any], *, actor: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            return {"ok": False, "detail": "confirm_required"}
        preview = self.preview(case_id, payload)
        if not preview.get("ok"):
            return preview
        case = self.get_case(case_id)
        assert case is not None
        for side, key in (("a", "from_id"), ("b", "to_id")):
            expected = (preview.get("version_guard") or {}).get(side, {}).get("expected_version")
            live = _safe_get(self.service, case[key])
            if expected is not None and live and live.get("version") != expected:
                return {"ok": False, "detail": "version_conflict", "side": side, "expected": expected, "got": live.get("version")}
        applied: list[dict[str, Any]] = []
        tms_result: dict[str, Any] = {}
        try:
            for op in preview["ops"]:
                applied.append(self._apply_op(op, actor=actor))
            tms_result = self._touch_tms(case, payload, actor=actor)
            self._mark_knowledge(preview["impact"].get("knowledge_revalidate") or [])
            self.sync_from_relations()
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code == "version_conflict":
                return {"ok": False, "detail": "version_conflict", "error": str(exc), "applied": applied}
            return {"ok": False, "detail": "commit_failed", "error": str(exc), "applied": applied}
        now = _now()
        event_id = _new_id("cfev")
        scheme = str(payload.get("scheme") or "")
        new_status = STATUS_DISMISSED if scheme == "invalidate_or_dismiss" and payload.get("dismiss") else STATUS_RESOLVED
        with self.db.transaction() as tx:
            tx.execute(
                """
                INSERT INTO conflict_events(
                  id, case_id, actor, action, scheme, before_json, after_json,
                  tms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    case_id,
                    actor,
                    "commit",
                    scheme,
                    json.dumps({"a": case["a"], "b": case["b"]}, ensure_ascii=False, default=str),
                    json.dumps({"ops": preview["ops"], "applied": applied}, ensure_ascii=False, default=str),
                    json.dumps(tms_result, ensure_ascii=False, default=str),
                    now,
                ),
            )
            tx.execute(
                """
                UPDATE conflict_cases
                SET status=?, updated_at=?, last_resolution_json=?
                WHERE id=?
                """,
                (
                    new_status,
                    now,
                    json.dumps({"event_id": event_id, "scheme": scheme, "actor": actor}, ensure_ascii=False),
                    case_id,
                ),
            )
        return {
            "ok": True,
            "case_id": case_id,
            "status": new_status,
            "event_id": event_id,
            "applied": applied,
            "tms": tms_result,
            "verification_status": "unverified",
        }

    def _apply_op(self, op: dict[str, Any], *, actor: str) -> dict[str, Any]:
        kind = op.get("op")
        if kind == "update_memory":
            return self._update_side(str(op["id"]), op.get("fields") or {}, actor=actor)
        if kind == "invalidate_memory":
            eid = str(op["id"])
            live = _load_entity(self.service, eid)
            if live and live.get("entity_kind") == "claim":
                from cognitive_memory.claims.store import ClaimStore

                store = ClaimStore(self.db)
                rec = store.retract(eid, reason="conflict_user_invalidate", actor=actor)
                return {"op": kind, "id": eid, "status": "retracted", "claim": rec}
            body = MemoryUpdateIn(status=MemoryStatus.INVALIDATED, expected_version=None)
            mem = self.service.update_memory(eid, body)
            return {"op": kind, "id": eid, "status": mem.status.value, "version": mem.version}
        if kind == "retarget_relation":
            item = self.service.update_relation(int(op["relation_id"]), relation=str(op.get("relation") or "qualifies"))
            return {"op": kind, "item": item}
        if kind == "dismiss_conflict":
            item = self.service.update_relation(
                int(op["relation_id"]),
                note="conflict_dismissed_by_user",
                metadata={"dismissed": True, "actor": actor},
            )
            return {"op": kind, "item": item}
        if kind == "create_derived":
            from cognitive_memory.domain.models import MemoryProposeIn, MemoryType

            content = str(op.get("content") or "").strip()
            if not content:
                return {"op": kind, "ok": False, "detail": "merged_content_required"}
            sources = [str(x) for x in (op.get("from_ids") or [])]
            if sources and all(_load_entity(self.service, x) and _load_entity(self.service, x).get("entity_kind") == "claim" for x in sources):
                from cognitive_memory.claims.store import ClaimStore

                store = ClaimStore(self.db)
                bank = str((_load_entity(self.service, sources[0]) or {}).get("bank_id") or "personal")
                proposed = store.propose_from_text(content, bank_id=bank, persist_unresolved=True)
                new_id = proposed.get("claim_id") or (proposed.get("claim") or {}).get("id") or proposed.get("same_as")
                if not new_id:
                    return {"op": kind, "ok": False, "detail": proposed}
                for src in sources:
                    self.service.link_memories(from_id=str(new_id), to_id=src, relation="derives", note="conflict_merge", metadata={"source": "conflict_governance"})
                return {"op": kind, "id": new_id, "status": "proposed", "unresolved": proposed.get("unresolved")}
            mem = self.service.propose_memory(
                MemoryProposeIn(
                    content=content,
                    type=MemoryType.SEMANTIC,
                    status=MemoryStatus.CANDIDATE,
                    metadata={"source": "conflict_merge", "inferred": True, "verification_status": "unverified"},
                )
            )
            for src in sources:
                self.service.link_memories(from_id=mem.id, to_id=src, relation="derives", note="conflict_merge", metadata={"source": "conflict_governance"})
            return {"op": kind, "id": mem.id, "status": mem.status.value}
        if kind == "attach_evidence":
            mid = str(op["memory_id"])
            live = _load_entity(self.service, mid)
            if live and live.get("entity_kind") == "claim":
                from cognitive_memory.claims.store import ClaimStore

                store = ClaimStore(self.db)
                rec = store.attach_evidence(mid, str(op["observation_id"]), role=str(op.get("relation") or "support"))
                return {"op": kind, "link": rec}
            from cognitive_memory.evidence import EvidenceAttachIn, attach_evidence

            rec = attach_evidence(
                self.db,
                EvidenceAttachIn(
                    memory_id=mid,
                    observation_id=str(op["observation_id"]),
                    relation=str(op.get("relation") or "supports"),
                    note=str(op.get("note") or "conflict_workbench"),
                ),
            )
            return {"op": kind, "link": rec.model_dump(mode="json")}
        if kind == "detach_evidence":
            from cognitive_memory.evidence import detach_evidence

            return {"op": kind, **detach_evidence(self.db, str(op["memory_id"]), str(op["observation_id"]), str(op.get("relation") or "supports"))}
        return {"op": kind, "skipped": True}

    def _update_side(self, memory_id: str, fields: dict[str, Any], *, actor: str) -> dict[str, Any]:
        live = _load_entity(self.service, memory_id)
        if not live:
            raise RuntimeError(f"entity_not_found:{memory_id}")
        if live.get("entity_kind") == "claim":
            return self._update_claim(memory_id, live, fields, actor=actor)
        live = self.service.get_memory(memory_id)
        meta = _meta(live)
        edit = dict(meta.get("claim_edit") if isinstance(meta.get("claim_edit"), dict) else {})
        for key in ("polarity", "modality", "subject", "predicate", "object", "conditional"):
            if key in fields and fields[key] is not None:
                edit[key] = fields[key]
        if fields.get("temporal_start") is not None or fields.get("temporal_end") is not None:
            edit["temporal"] = {
                "start": fields.get("temporal_start") if fields.get("temporal_start") is not None else (edit.get("temporal") or {}).get("start"),
                "end": fields.get("temporal_end") if fields.get("temporal_end") is not None else (edit.get("temporal") or {}).get("end"),
            }
        if not (edit.get("subject") and edit.get("predicate")):
            edit["unresolved"] = True
        meta["claim_edit"] = edit
        meta["last_editor"] = actor
        content = fields["content"] if "content" in fields else live.get("content")
        status = None
        if fields.get("status") in {s.value for s in MemoryStatus}:
            status = MemoryStatus(fields["status"])
        body = MemoryUpdateIn(
            content=content,
            metadata=meta,
            status=status,
            expected_version=int(fields["expected_version"]) if fields.get("expected_version") is not None else live.get("version"),
        )
        mem = self.service.update_memory(memory_id, body)
        return {"op": "update_memory", "id": memory_id, "version": mem.version, "status": mem.status.value}

    def _update_claim(self, claim_id: str, live: dict[str, Any], fields: dict[str, Any], *, actor: str) -> dict[str, Any]:
        from cognitive_memory.claims.store import ClaimStore

        expected = fields.get("expected_version")
        if expected is not None and live.get("version") is not None and int(expected) != int(live.get("version") or 0):
            err = RuntimeError("version_conflict")
            err.code = "version_conflict"  # type: ignore[attr-defined]
            raise err
        content = str(fields["content"] if "content" in fields else live.get("content") or live.get("surface") or "").strip()
        if not content:
            return {"op": "update_memory", "id": claim_id, "ok": False, "detail": "empty_content"}
        scope: dict[str, Any] = {}
        if fields.get("conditional"):
            scope["condition"] = fields.get("conditional")
        if fields.get("subject") or fields.get("predicate"):
            scope["claim_edit"] = {
                "subject": fields.get("subject") or "",
                "predicate": fields.get("predicate") or "",
                "object": fields.get("object") or "",
                "polarity": fields.get("polarity") or "",
                "modality": fields.get("modality") or "",
            }
        vf = str(fields.get("temporal_start") or "") or None
        vt = str(fields.get("temporal_end") or "") or None
        store = ClaimStore(self.db)
        proposed = store.propose_from_text(
            content,
            bank_id=str(live.get("bank_id") or "personal"),
            source_memory_id=str(live.get("id") or claim_id),
            scope=scope or None,
            persist_unresolved=True,
            validity_from=vf,
            validity_to=vt,
        )
        new_id = proposed.get("claim_id") or (proposed.get("claim") or {}).get("id") or proposed.get("same_as")
        if new_id and str(new_id) != str(claim_id):
            store.supersede(claim_id, str(new_id), reason="conflict_user_edit", actor=actor)
        return {
            "op": "update_memory",
            "id": claim_id,
            "replacement_id": new_id,
            "unresolved": bool(proposed.get("unresolved")),
            "verification_status": "unverified",
            "persisted": proposed.get("persisted"),
        }

    def _touch_tms(self, case: dict[str, Any], payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "changed": []}
        try:
            from cognitive_memory.claims.store import ClaimStore

            store = ClaimStore(self.db)
            invalidate = str(payload.get("invalidate") or "")
            for nid in (case["from_id"], case["to_id"]):
                if invalidate == nid:
                    out["changed"].append(store.tms.invalidate_node(nid, reason="conflict_user_invalidate", actor=actor))
                else:
                    belief = store.tms.get_belief(nid)
                    store.tms.persist_belief(nid, belief.status, ["conflict_touch", actor])
        except Exception as exc:
            out = {"ok": False, "detail": str(exc), "degraded": True}
        return out

    def _mark_knowledge(self, ids: list[str]) -> None:
        for kid in ids:
            mem = _safe_get(self.service, kid)
            if not mem:
                continue
            meta = _meta(mem)
            if meta.get("needs_revalidation"):
                continue
            if mem.get("entity_kind") == "claim":
                continue
            meta["needs_revalidation"] = True
            meta["verified_retained"] = False
            self.service.update_memory(
                kid,
                MemoryUpdateIn(metadata=meta, status=MemoryStatus.CANDIDATE, expected_version=mem.get("version")),
            )

    def history(self, case_id: str) -> dict[str, Any]:
        rows = self._conn().execute(
            "SELECT * FROM conflict_events WHERE case_id=? ORDER BY created_at DESC",
            (case_id,),
        ).fetchall()
        items = []
        for row in rows:
            items.append({
                "id": row["id"],
                "case_id": row["case_id"],
                "actor": row["actor"],
                "action": row["action"],
                "scheme": row["scheme"],
                "before": json.loads(row["before_json"] or "{}"),
                "after": json.loads(row["after_json"] or "{}"),
                "tms": json.loads(row["tms_json"] or "{}"),
                "created_at": row["created_at"],
            })
        return {"ok": True, "items": items}

    def rollback_last(self, case_id: str, *, actor: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            return {"ok": False, "detail": "confirm_required"}
        hist = self.history(case_id)["items"]
        if not hist:
            return {"ok": False, "detail": "no_event"}
        last = hist[0]
        before = last.get("before") or {}
        restored = []
        for side, mid_key in (("a", "from_id"), ("b", "to_id")):
            snap = before.get(side) or {}
            mid = snap.get("id")
            if not mid:
                continue
            live = _safe_get(self.service, mid)
            if not live:
                continue
            meta = _meta(live)
            meta["claim_edit"] = {k: snap.get(k) for k in ("polarity", "modality", "subject", "predicate", "object", "conditional") if snap.get(k) is not None}
            st = None
            if snap.get("status") in {s.value for s in MemoryStatus}:
                st = MemoryStatus(snap["status"])
            body = MemoryUpdateIn(content=snap.get("content"), metadata=meta, status=st, expected_version=live.get("version"))
            mem = self.service.update_memory(mid, body)
            restored.append({"id": mid, "version": mem.version})
        now = _now()
        event_id = _new_id("cfev")
        with self.db.transaction() as tx:
            tx.execute(
                """
                INSERT INTO conflict_events(
                  id, case_id, actor, action, scheme, before_json, after_json, tms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, case_id, actor, "rollback", last.get("scheme") or "", json.dumps(last, ensure_ascii=False, default=str), json.dumps({"restored": restored}, ensure_ascii=False), "{}", now),
            )
            tx.execute(
                "UPDATE conflict_cases SET status=?, updated_at=? WHERE id=?",
                (STATUS_REVERTED, now, case_id),
            )
        return {"ok": True, "event_id": event_id, "restored": restored, "status": STATUS_REVERTED}
