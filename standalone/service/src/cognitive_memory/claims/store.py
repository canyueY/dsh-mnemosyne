# -*- coding: utf-8 -*-
"""P2 Claim persistence — first-class rows (not Memory projections)."""

from __future__ import annotations

import json
import uuid
from functools import wraps
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.claims.ast import ClaimAST
from cognitive_memory.claims.lifecycle import ClaimLifecycle, ClaimLifecycleEngine
from cognitive_memory.claims.normalize import normalized_hash, NORMALIZATION_VERSION
from cognitive_memory.claims.parser import parse_memory_text, PARSER_VERSION
from cognitive_memory.security.context import SecurityContext
from cognitive_memory.storage.db import Database
from cognitive_memory.tracing.service import JudgmentTraceService
from cognitive_memory.tms.store import TmsStore
from cognitive_memory.verification.engine import VerificationEngine
from cognitive_memory.verification.models import VerificationStatus
from cognitive_memory.verification.persist import PersistentVerificationStore
from cognitive_memory.visibility.models import VisibilityDecision
from cognitive_memory.visibility.service import evaluate_visibility


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "clm") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class _RejectedMutation(Exception):
    def __init__(self, result):
        self.result = result


def atomic_mutation(fn):
    """Serialize read/check/write; a rejected operation cannot partially commit."""
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        try:
            with self.db.transaction():
                result = fn(self, *args, **kwargs)
                if isinstance(result, dict) and result.get("ok") is False:
                    raise _RejectedMutation(result)
                return result
        except _RejectedMutation as exc:
            return exc.result
    return wrapped


class ClaimStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()
        self._life = ClaimLifecycleEngine()
        self._ver_engine = VerificationEngine()
        self._ver_store = PersistentVerificationStore(db)
        self.traces = JudgmentTraceService(db)
        self.tms = TmsStore(db)

    def _record_trace(
        self,
        judgment_type: str,
        *,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        reason_codes: list[str] | None = None,
        governance_critical: bool = False,
    ) -> str | None:
        tr = self.traces.record(
            judgment_type,
            input_refs=input_refs,
            output_refs=output_refs,
            evidence_refs=evidence_refs,
            reason_codes=reason_codes,
            algorithm_version="claim.store.v1",
            rule_version="claim.lifecycle.v1",
            governance_critical=governance_critical,
        )
        return tr.trace_id if tr else None

    def _record_decision(
        self,
        *,
        claim_id: str,
        operation: str,
        previous_status: str,
        next_status: str,
        reason: str,
        actor: str,
        evidence_refs: list[str] | None,
        replacement_claim_id: str | None,
        trace_id: str | None,
        confidence: float | None = None,
        decision_at: str | None = None,
    ) -> str:
        did = _new_id("cdec")
        now = decision_at or _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO claim_decision_records(
                  id, claim_id, operation, previous_status, next_status,
                  trigger_evidence_json, replacement_claim_id, reason,
                  decision_at, actor, confidence, trace_id, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    did,
                    claim_id,
                    operation,
                    previous_status,
                    next_status,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    replacement_claim_id,
                    reason,
                    now,
                    actor,
                    confidence,
                    trace_id,
                    "claim.decision.v1",
                    now,
                ),
            )
        return did

    @atomic_mutation
    def propose_from_text(
        self,
        text: str,
        *,
        bank_id: str = "personal",
        source_memory_id: str | None = None,
        scope: dict[str, Any] | None = None,
        persist_unresolved: bool = True,
        validity_from: str | None = None,
        validity_to: str | None = None,
    ) -> dict[str, Any]:
        """Parse → normalize → persist Claim (lifecycle=proposed). Never verified."""
        parsed = parse_memory_text(text)
        ast: ClaimAST = parsed.ast
        if validity_from or validity_to:
            ast = ast.model_copy(update={"scope": {**ast.scope, "validity": {"from": validity_from, "to": validity_to}}})
        if scope:
            merged_scope = {**(ast.scope or {}), **dict(scope)}
            if validity_from or validity_to:
                merged_scope["validity"] = {"from": validity_from, "to": validity_to}
            ast = ast.model_copy(update={"scope": merged_scope})
        if ast.is_unresolved() and not persist_unresolved:
            return {
                "ok": True,
                "persisted": False,
                "unresolved": True,
                "ast": ast.model_dump(),
                "parse": {"confidence": parsed.confidence, "notes": parsed.notes},
            }
        nh = normalized_hash(ast)
        # same_as: reuse existing non-redirected claim with same hash in bank
        existing = self.find_by_hash(nh, bank_id=bank_id)
        if existing and not existing.get("redirects_to"):
            tid = self._record_trace(
                "claim.same_as",
                input_refs=[source_memory_id or "text"],
                output_refs=[existing["id"]],
                reason_codes=["normalized_hash_match"],
            )
            return {
                "ok": True,
                "persisted": False,
                "same_as": existing["id"],
                "claim": existing,
                "ast": ast.model_dump(),
                "trace_id": tid,
            }
        cid = _new_id()
        now = _now()
        row = {
            "id": cid,
            "bank_id": bank_id,
            "ast_version": ast.ast_version,
            "ast_json": json.dumps(ast.model_dump(), ensure_ascii=False),
            "surface": ast.surface or text,
            "normalized_hash": nh,
            "lifecycle": ClaimLifecycle.PROPOSED.value,
            "verification_status": "unverified",
            "scope_json": json.dumps(ast.scope or {}, ensure_ascii=False),
            "source_memory_id": source_memory_id,
            "redirects_to": None,
            "validity_from": validity_from,
            "validity_to": validity_to,
            "created_at": now,
            "updated_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO claims(
                  id, bank_id, ast_version, ast_json, surface, normalized_hash,
                  lifecycle, verification_status, scope_json, source_memory_id,
                  redirects_to, created_at, updated_at, validity_from, validity_to, normalization_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["bank_id"],
                    row["ast_version"],
                    row["ast_json"],
                    row["surface"],
                    row["normalized_hash"],
                    row["lifecycle"],
                    row["verification_status"],
                    row["scope_json"],
                    row["source_memory_id"],
                    row["redirects_to"],
                    row["created_at"],
                    row["updated_at"],
                    row["validity_from"],
                    row["validity_to"],
                    NORMALIZATION_VERSION,
                ),
            )
            conn.execute("UPDATE claims SET parser_version=? WHERE id=?", (PARSER_VERSION, cid))
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("cle"),
                    cid,
                    ClaimLifecycle.PROPOSED.value,
                    ClaimLifecycle.PROPOSED.value,
                    "propose_from_text",
                    "system",
                    "claim.lifecycle.v1",
                    "[]",
                    now,
                ),
            )
        self._life._status[cid] = ClaimLifecycle.PROPOSED
        tid = self._record_trace(
            "claim.propose",
            input_refs=[source_memory_id or "text"],
            output_refs=[cid],
            reason_codes=["unresolved"] if ast.is_unresolved() else ["parsed"],
        )
        did = self._record_decision(
            claim_id=cid,
            operation="propose",
            previous_status=ClaimLifecycle.PROPOSED.value,
            next_status=ClaimLifecycle.PROPOSED.value,
            reason="parse_and_persist",
            actor="system",
            evidence_refs=[],
            replacement_claim_id=None,
            trace_id=tid,
            decision_at=now,
        )
        return {
            "ok": True,
            "persisted": True,
            "claim_id": cid,
            "claim": self.get(cid),
            "ast": ast.model_dump(),
            "unresolved": ast.is_unresolved(),
            "trace_id": tid,
            "decision_id": did,
        }

    def get(
        self,
        claim_id: str,
        *,
        follow_redirect: bool = True,
        principal: SecurityContext | None = None,
        evidence_visible: bool = True,
        _visited: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        if claim_id in _visited or len(_visited)>=32:
            return None
        _visited = _visited | {claim_id}
        with self.db.exclusive() as conn:
            if principal is not None:
                meta=conn.execute("SELECT bank_id FROM claims WHERE id=?",(claim_id,)).fetchone()
                if meta is None or not principal.allows_bank(meta[0]) or not principal.allows_purpose("retrieve"):
                    return None
            row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not row:
            return None
        pack = self._row_to_dict(row)
        if principal is not None and pack["verification_status"] == "verified":
            rec = self._ver_store.latest_for_claim(claim_id)
            from cognitive_memory.verification.assessment import POLICY_VERSION
            if not rec or rec.policy_version != POLICY_VERSION or rec.status != VerificationStatus.VERIFIED:
                pack["verification_status"] = "revalidate_required"
                if pack["lifecycle"] == "verified":
                    pack["lifecycle"] = "supported"
            elif rec.valid_until and rec.valid_until <= _now():
                pack["verification_status"] = "expired"
                pack["lifecycle"] = "expired"
        if principal is not None:
            from cognitive_memory.evidence.object_store import EvidenceObjectStore
            refs = [e["evidence_ref"] for e in self.list_evidence(claim_id)]
            rec = self._ver_store.latest_for_claim(claim_id)
            if rec:
                refs.extend(rec.evidence_refs)
            for ref in set(refs):
                ev = EvidenceObjectStore(self.db).get(ref)
                sec = (ev or {}).get("security") or {}
                if (not ev or not principal.allows_bank(ev["bank_id"])
                        or (sec.get("owner_id") and sec["owner_id"] != principal.owner_id
                            and not set(sec.get("authorized_grants", [])) & set(principal.authorized_grants))
                        or (sec.get("purpose_constraints") and "retrieve" not in sec["purpose_constraints"])):
                    evidence_visible = False
                    break
            vis = evaluate_visibility(
                principal,
                object_ref=claim_id,
                object_bank=str(pack.get("bank_id") or "personal"),
                requested_fields=[
                    "claim_id",
                    "lifecycle_status",
                    "ast_version",
                    "ast",
                    "verification_status",
                    "evidence_refs",
                ],
                evidence_visible=evidence_visible,
            )
            if vis.decision == VisibilityDecision.DENY:
                return None  # fail-closed: no existence disclosure
            pack = self._apply_visibility(pack, vis.visible_fields, vis.denied_fields)
            pack["visibility"] = vis.model_dump(mode="json")
        if follow_redirect and pack.get("redirects_to"):
            target = self.get(
                str(pack["redirects_to"]),
                follow_redirect=True,
                principal=principal,
                evidence_visible=evidence_visible,
                _visited=_visited,
            )
            if target:
                pack["redirect_resolved"] = target
        return pack

    @staticmethod
    def _apply_visibility(
        pack: dict[str, Any],
        visible_fields: list[str],
        denied_fields: list[str],
    ) -> dict[str, Any]:
        out = dict(pack)
        # Map contract field names → stored keys
        field_map = {
            "ast": "ast",
            "verification_status": "verification_status",
            "lifecycle_status": "lifecycle",
            "ast_version": "ast_version",
            "claim_id": "id",
            "evidence_refs": "_evidence_refs",
        }
        for f in denied_fields:
            key = field_map.get(f, f)
            if key in out:
                out.pop(key, None)
            if f == "evidence_refs":
                out.pop("evidence", None)
        # Always keep id if claim_id visible or as opaque handle when PARTIAL
        if "claim_id" in visible_fields or "id" in visible_fields:
            out["id"] = pack.get("id")
        return out

    def find_by_hash(self, normalized_hash_value: str, *, bank_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute(
                """
                SELECT * FROM claims
                WHERE normalized_hash=? AND bank_id=? AND redirects_to IS NULL
                ORDER BY created_at ASC LIMIT 1
                """,
                (normalized_hash_value, bank_id),
            ).fetchone()
        if row:
            return self._row_to_dict(row)
        # Compare immutable legacy ASTs under the new rules without rewriting old hashes/IDs.
        with self.db.exclusive() as conn:
            legacy=conn.execute(
                "SELECT * FROM claims WHERE bank_id=? AND redirects_to IS NULL AND normalization_version!=?",
                (bank_id,NORMALIZATION_VERSION),
            ).fetchall()
        for old in legacy:
            ast=ClaimAST.model_validate_json(old["ast_json"])
            if normalized_hash(ast)==normalized_hash_value:
                return self._row_to_dict(old)
        return None

    @atomic_mutation
    def attach_evidence(
        self,
        claim_id: str,
        evidence_ref: str,
        *,
        role: str = "support",
        independent: bool = False,
        materialize_evidence_object: bool = True,
        bank_id: str | None = None,
    ) -> dict[str, Any]:
        claim = self.get(claim_id, follow_redirect=False)
        if not claim:
            return {"ok": False, "detail": "claim_not_found"}
        if role not in {"support","counter","contradicts","qualifies"}:
            return {"ok":False,"detail":"invalid_evidence_role"}
        evidence_object_id: str | None = None
        if materialize_evidence_object and evidence_ref:
            from cognitive_memory.evidence.object_store import EvidenceObjectStore

            estore = EvidenceObjectStore(self.db)
            # Prefer explicit Evidence id; else treat ref as observation source
            if str(evidence_ref).startswith("evd_"):
                existing = estore.get(str(evidence_ref))
                if existing is None:
                    return {"ok":False,"detail":"evidence_missing"}
                if existing["bank_id"]!=claim["bank_id"]:
                    return {"ok":False,"detail":"evidence_scope_denied"}
                evidence_object_id = existing["id"] if existing else None
            else:
                created = estore.create_from_observation(
                    str(evidence_ref),
                    bank_id=bank_id or str(claim.get("bank_id") or "personal"),
                    role_hint=role,
                    legacy_memory_id=claim.get("source_memory_id"),
                )
                if created.get("ok") and created.get("evidence"):
                    evidence_object_id = created["evidence"]["id"]
                    # Link uses first-class evidence id when available
                    evidence_ref = evidence_object_id
        eid = _new_id("cev")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO claim_evidence(
                  id, claim_id, evidence_ref, role, independent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (eid, claim_id, evidence_ref, role, 1 if independent else 0, now),
            )
        tid = self._record_trace(
            "claim.attach_evidence",
            input_refs=[claim_id],
            output_refs=[eid],
            evidence_refs=[evidence_ref],
            reason_codes=[role],
        )
        if role in {"counter","contradicts"} and claim["lifecycle"] in {"verified","supported","qualified"}:
            self.transition(claim_id,"disputed",reason="counterevidence_attached",actor="evidence_service",
                            evidence_refs=[evidence_ref])
        return {
            "ok": True,
            "link_id": eid,
            "evidence_id": evidence_object_id or eid,
            "evidence_ref": evidence_ref,
            "claim_id": claim_id,
            "role": role,
            "trace_id": tid,
        }

    def list_evidence(self, claim_id: str) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            rows = conn.execute(
                "SELECT * FROM claim_evidence WHERE claim_id=? ORDER BY created_at",
                (claim_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "claim_id": r["claim_id"],
                "evidence_ref": r["evidence_ref"],
                "role": r["role"],
                "independent": bool(r["independent"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    @atomic_mutation
    def transition(
        self,
        claim_id: str,
        target_status: str | ClaimLifecycle,
        *,
        reason: str,
        evidence_refs: list[str] | None = None,
        actor: str = "",
        replacement_claim_id: str | None = None,
        confidence: float | None = None,
        decision_at: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if str(getattr(target_status, "value", target_status)) == "verified":
            return self.submit_verified(claim_id, evidence_refs=list(evidence_refs or []), actor=actor,
                                        expected_version=expected_version)
        claim = self.get(claim_id, follow_redirect=False)
        if not claim:
            return {"ok": False, "detail": "claim_not_found"}
        if expected_version is not None and expected_version!=claim["version"]:
            return {"ok":False,"detail":"version_conflict"}
        if str(getattr(target_status,"value",target_status))==claim["lifecycle"]:
            return {"ok":True,"claim_id":claim_id,"idempotent":True,"claim":claim,
                    "from_status":claim["lifecycle"],"to_status":claim["lifecycle"]}
        if claim.get("redirects_to"):
            return {"ok": False, "detail": "claim_redirected", "redirects_to": claim["redirects_to"]}
        if replacement_claim_id:
            replacement = self.get(replacement_claim_id, follow_redirect=True)
            if not replacement:
                return {"ok": False, "detail": "replacement_claim_not_found"}
        # sync in-memory engine from DB
        self._life._status[claim_id] = ClaimLifecycle(str(claim.get("lifecycle") or "proposed"))
        result = self._life.transition_claim(
            claim_id,
            target_status,
            reason=reason,
            evidence_refs=evidence_refs,
            actor=actor,
        )
        if not result.ok:
            return {
                "ok": False,
                "detail": result.error or "transition_failed",
                "from_status": result.from_status.value,
                "result": result.model_dump(),
            }
        assert result.to_status is not None
        now = decision_at or _now()
        tid = self._record_trace(
            "claim.transition",
            input_refs=[claim_id],
            output_refs=[claim_id],
            evidence_refs=list(evidence_refs or []),
            reason_codes=[result.from_status.value, result.to_status.value, reason],
            governance_critical=result.to_status
            in (ClaimLifecycle.VERIFIED, ClaimLifecycle.REJECTED, ClaimLifecycle.SUPERSEDED,
                ClaimLifecycle.RETRACTED, ClaimLifecycle.EXPIRED),
        )
        did = _new_id("cdec")
        target = result.to_status
        sets = ["lifecycle=?", "updated_at=?", "version=version+1"]
        params: list[Any] = [target.value, now]
        if target in (ClaimLifecycle.SUPERSEDED, ClaimLifecycle.RETRACTED, ClaimLifecycle.EXPIRED):
            sets.append("validity_to=COALESCE(validity_to, ?)")
            params.append(now)
        if target == ClaimLifecycle.SUPERSEDED:
            sets.append("superseded_at=?")
            params.append(now)
            if replacement_claim_id:
                sets.append("superseded_by=?")
                params.append(replacement_claim_id)
        elif target == ClaimLifecycle.RETRACTED:
            sets.append("retracted_at=?")
            params.append(now)
        elif target == ClaimLifecycle.EXPIRED:
            sets.append("expired_at=?")
            params.append(now)
        elif target == ClaimLifecycle.VERIFIED:
            sets.append("last_confirmed_at=?")
            params.append(now)
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE claims SET {', '.join(sets)} WHERE id=?",
                (*params, claim_id),
            )
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at, trace_id, decision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("cle"),
                    claim_id,
                    result.from_status.value,
                    result.to_status.value,
                    reason,
                    actor,
                    result.policy_version,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    now,
                    tid,
                    did,
                ),
            )
            conn.execute(
                """
                INSERT INTO claim_decision_records(
                  id, claim_id, operation, previous_status, next_status,
                  trigger_evidence_json, replacement_claim_id, reason,
                  decision_at, actor, confidence, trace_id, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    did,
                    claim_id,
                    target.value,
                    result.from_status.value,
                    target.value,
                    json.dumps(list(evidence_refs or []), ensure_ascii=False),
                    replacement_claim_id,
                    reason,
                    now,
                    actor,
                    confidence,
                    tid,
                    "claim.decision.v1",
                    now,
                ),
            )
        tms_pack: dict[str, Any] | None = None
        impact: dict[str, Any] | None = None
        if target in (
            ClaimLifecycle.DISPUTED,
            ClaimLifecycle.REJECTED,
            ClaimLifecycle.SUPERSEDED,
            ClaimLifecycle.RETRACTED,
            ClaimLifecycle.EXPIRED,
        ):
            tms_pack = self.tms.invalidate_node(
                claim_id, reason=f"lifecycle:{target.value}", actor=actor
            )
            try:
                impact = self.tms.engine.impact(claim_id, max_nodes=32)
            except Exception:
                impact = None
        return {
            "ok": True,
            "claim_id": claim_id,
            "from_status": result.from_status.value,
            "to_status": target.value,
            "claim": self.get(claim_id),
            "trace_id": tid,
            "decision_id": did,
            "decision": {
                "decision_id": did,
                "operation": target.value,
                "previous_status": result.from_status.value,
                "next_status": target.value,
                "trigger_evidence": list(evidence_refs or []),
                "replacement_claim_id": replacement_claim_id,
                "reason": reason,
                "decision_at": now,
                "actor": actor,
                "confidence": confidence,
                "trace_id": tid,
                "schema_version": "claim.decision.v1",
            },
            "tms": tms_pack,
            "impact": impact,
        }

    def supersede(
        self,
        claim_id: str,
        replacement_claim_id: str,
        *,
        reason: str = "",
        evidence_refs: list[str] | None = None,
        actor: str = "",
        confidence: float | None = None,
    ) -> dict[str, Any]:
        return self.transition(
            claim_id,
            ClaimLifecycle.SUPERSEDED,
            reason=reason or "superseded_by_new_claim",
            evidence_refs=evidence_refs,
            actor=actor,
            replacement_claim_id=replacement_claim_id,
            confidence=confidence,
        )

    def retract(self, claim_id: str, *, reason: str, evidence_refs: list[str] | None = None,
                actor: str = "", confidence: float | None = None) -> dict[str, Any]:
        return self.transition(
            claim_id,
            ClaimLifecycle.RETRACTED,
            reason=reason or "retracted",
            evidence_refs=evidence_refs,
            actor=actor,
            confidence=confidence,
        )

    def expire(self, claim_id: str, *, reason: str, evidence_refs: list[str] | None = None,
               actor: str = "", confidence: float | None = None) -> dict[str, Any]:
        return self.transition(
            claim_id,
            ClaimLifecycle.EXPIRED,
            reason=reason or "expired",
            evidence_refs=evidence_refs,
            actor=actor,
            confidence=confidence,
        )

    def list_decisions(self, claim_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit or 50)))
        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT * FROM claim_decision_records
                WHERE claim_id=? ORDER BY decision_at DESC, rowid DESC LIMIT ?
                """,
                (claim_id, lim),
            ).fetchall()
        out = []
        for r in rows:
            try:
                refs = json.loads(r["trigger_evidence_json"] or "[]")
            except json.JSONDecodeError:
                refs = []
            out.append({
                "decision_id": r["id"],
                "claim_id": r["claim_id"],
                "operation": r["operation"],
                "previous_status": r["previous_status"],
                "next_status": r["next_status"],
                "trigger_evidence": refs,
                "replacement_claim_id": r["replacement_claim_id"],
                "reason": r["reason"],
                "decision_at": r["decision_at"],
                "actor": r["actor"],
                "confidence": r["confidence"],
                "trace_id": r["trace_id"],
                "schema_version": r["schema_version"],
            })
        return out

    @atomic_mutation
    def submit_verified(
        self,
        claim_id: str,
        *,
        evidence_refs: list[str],
        actor: str,
        authorized: bool = True,
        evidence_still_valid: bool = True,
        principal: SecurityContext | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Sole path to Verified: engine re-check + persist record + lifecycle."""
        claim = self.get(claim_id, follow_redirect=False)
        if not claim:
            return {"ok": False, "detail": "claim_not_found"}
        if claim.get("redirects_to"):
            return {"ok": False, "detail": "claim_redirected"}
        if not authorized:
            return {"ok": False, "detail": "not_authorized"}
        if expected_version is not None and claim["version"] != expected_version:
            return {"ok": False, "detail": "version_conflict"}
        if claim["lifecycle"] in {"rejected", "retracted", "superseded", "disputed"}:
            return {"ok": False, "detail": "illegal_transition"}
        if claim["ast"].get("root", {}).get("kind") == "unresolved":
            return {"ok": False, "detail": "unresolved_claim"}
        from cognitive_memory.verification.assessment import assess_evidence
        assessment = assess_evidence(self.db, claim, evidence_refs, principal=principal)
        if not assessment["ok"]:
            return assessment
        evidence_refs = assessment["evidence_refs"]
        if not evidence_still_valid:  # legacy caller may veto, never authorize
            return {"ok": False, "detail": "evidence_revoked_before_commit"}
        previous = self._ver_store.latest_for_claim(claim_id)
        if (claim["verification_status"] == "verified" and previous
                and previous.evidence_refs == evidence_refs
                and previous.status == VerificationStatus.VERIFIED
                and (not previous.valid_until or previous.valid_until > _now())
                and previous.policy_version == assessment["policy_version"]):
            return {"ok": True, "claim_id":claim_id, "claim":claim,
                    "record":previous.model_dump(mode="json"), "idempotent":True}
        # Must be supported/qualified before verified
        life = str(claim.get("lifecycle") or "proposed")
        if life not in ("supported", "qualified", "expired"):
            if life == "proposed":
                bump = self.transition(claim_id, "supported", reason="pre_verify", actor=actor)
                if not bump.get("ok"):
                    return {"ok": False, "detail": "cannot_reach_supported", "prior": bump}
                life = "supported"
                claim = self.get(claim_id, follow_redirect=False)
        eng = self._ver_engine.submit_verified(
            claim_id=claim_id,
            evidence_refs=evidence_refs,
            actor=actor,
            authorized=authorized,
            evidence_still_valid=evidence_still_valid,
            claim_version=claim["version"],
            policy_version=assessment["policy_version"],
            security_context={
                "bank_id":claim["bank_id"],"scope":claim["scope"],
                "verification_policy":assessment["policy_version"],
                "evidence_snapshots":[self.db.connect().execute(
                    "SELECT fingerprint,validation_state,provenance_status FROM evidences WHERE id=?",(ref,)
                ).fetchone()[:] for ref in evidence_refs],
            },
        )
        if not eng.get("ok"):
            return {"ok": False, "detail": eng.get("error") or "verify_denied", "engine": eng}
        from cognitive_memory.verification.models import VerificationRecord

        rec_data = eng["record"]
        record = VerificationRecord.model_validate(rec_data)
        self._ver_store.insert(record)
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE claims
                SET verification_status=?, lifecycle=?, last_confirmed_at=?, updated_at=?, version=version+1
                WHERE id=?
                """,
                (
                    VerificationStatus.VERIFIED.value,
                    ClaimLifecycle.VERIFIED.value,
                    now,
                    now,
                    claim_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("cle"),
                    claim_id,
                    life,
                    ClaimLifecycle.VERIFIED.value,
                    "submit_verified",
                    actor,
                    "claim.lifecycle.v1",
                    json.dumps(list(evidence_refs), ensure_ascii=False),
                    now,
                ),
            )
        self._life._status[claim_id] = ClaimLifecycle.VERIFIED
        tms_pack = self.tms.add_and_persist(
            claim_id,
            list(evidence_refs),
            kind="support",
            reason="submit_verified",
            actor=actor,
        )
        if not tms_pack.get("ok"):
            return {"ok":False,"detail":"budget_exceeded","tms":tms_pack}
        tid = self._record_trace(
            "claim.verify",
            input_refs=[claim_id],
            output_refs=[claim_id, record.record_id],
            evidence_refs=list(evidence_refs),
            reason_codes=["verified", "tms_justification"],
            governance_critical=True,
        )
        did = self._record_decision(
            claim_id=claim_id,
            operation="verified",
            previous_status=life,
            next_status=ClaimLifecycle.VERIFIED.value,
            reason="submit_verified",
            actor=actor,
            evidence_refs=list(evidence_refs),
            replacement_claim_id=None,
            trace_id=tid,
            decision_at=now,
        )
        return {
            "ok": True,
            "claim_id": claim_id,
            "record": record.model_dump(mode="json"),
            "claim": self.get(claim_id),
            "trace_id": tid,
            "decision_id": did,
            "tms": tms_pack,
        }

    @atomic_mutation
    def redirect(self, source_id: str, target_id: str, *, reason: str = "merge") -> dict[str, Any]:
        src = self.get(source_id, follow_redirect=False)
        tgt = self.get(target_id, follow_redirect=True)
        if not src or not tgt:
            return {"ok": False, "detail": "claim_not_found"}
        if src["bank_id"] != (tgt.get("redirect_resolved") or tgt)["bank_id"]:
            return {"ok": False, "detail": "cross_bank_redirect_denied"}
        if tgt.get("redirect_resolved"):
            resolved_target = str(tgt["redirect_resolved"]["id"])
        else:
            resolved_target = str(tgt["id"])
        if source_id == resolved_target:
            return {"ok": False, "detail": "self_redirect"}
        if src.get("redirects_to")==resolved_target and src["lifecycle"]=="superseded":
            return {"ok":True,"source_id":source_id,"redirects_to":resolved_target,
                    "claim":src,"idempotent":True}
        now = _now()
        tid = self._record_trace(
            "claim.redirect",
            input_refs=[source_id],
            output_refs=[resolved_target],
            reason_codes=[reason, "superseded", "tms_invalidate"],
            governance_critical=True,
        )
        did = _new_id("cdec")
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE claims SET redirects_to=?, lifecycle=?, updated_at=?,
                  version=version+1, superseded_at=COALESCE(superseded_at, ?),
                  superseded_by=?,
                  validity_to=COALESCE(validity_to, ?)
                WHERE id=?
                """,
                (resolved_target, ClaimLifecycle.SUPERSEDED.value, now,
                 now, resolved_target, now, source_id),
            )
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at, trace_id, decision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("cle"),
                    source_id,
                    str(src.get("lifecycle") or "proposed"),
                    ClaimLifecycle.SUPERSEDED.value,
                    reason,
                    "system",
                    "claim.lifecycle.v1",
                    json.dumps([resolved_target], ensure_ascii=False),
                    now,
                    tid,
                    did,
                ),
            )
            conn.execute(
                """
                INSERT INTO claim_decision_records(
                  id, claim_id, operation, previous_status, next_status,
                  trigger_evidence_json, replacement_claim_id, reason,
                  decision_at, actor, confidence, trace_id, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    did, source_id, ClaimLifecycle.SUPERSEDED.value,
                    str(src.get("lifecycle") or "proposed"),
                    ClaimLifecycle.SUPERSEDED.value,
                    json.dumps([resolved_target], ensure_ascii=False),
                    resolved_target, reason, now, "system", None, tid,
                    "claim.decision.v1", now,
                ),
            )
        tms_pack = self.tms.invalidate_node(
            source_id, reason=f"redirect:{resolved_target}", actor="system"
        )
        try:
            impact = self.tms.engine.impact(source_id, max_nodes=32)
        except Exception:
            impact = None
        return {
            "ok": True,
            "source_id": source_id,
            "redirects_to": resolved_target,
            "claim": self.get(source_id, follow_redirect=False),
            "trace_id": tid,
            "decision_id": did,
            "impact": impact,
            "tms": tms_pack,
        }

    @atomic_mutation
    def merge(
        self,
        source_id: str,
        target_id: str,
        *,
        reason: str = "claim_merge",
        move_evidence: bool = True,
    ) -> dict[str, Any]:
        """Merge source into target: redirect + optional evidence reassignment."""
        if source_id == target_id:
            return {"ok": False, "detail": "self_merge"}
        src = self.get(source_id, follow_redirect=False)
        tgt = self.get(target_id, follow_redirect=True)
        if not src or not tgt:
            return {"ok": False, "detail": "claim_not_found"}
        if src["bank_id"] != (tgt.get("redirect_resolved") or tgt)["bank_id"]:
            return {"ok": False, "detail": "cross_bank_merge_denied"}
        resolved_target = str(
            (tgt.get("redirect_resolved") or tgt).get("id") or target_id
        )
        moved = 0
        if move_evidence:
            now = _now()
            with self.db.transaction() as conn:
                cur = conn.execute(
                    """
                    UPDATE claim_evidence
                    SET claim_id=?
                    WHERE claim_id=?
                    """,
                    (resolved_target, source_id),
                )
                moved = int(cur.rowcount or 0)
                # touch target
                conn.execute(
                    "UPDATE claims SET updated_at=? WHERE id=?",
                    (now, resolved_target),
                )
        redir = self.redirect(source_id, resolved_target, reason=reason)
        if not redir.get("ok"):
            return redir
        tid = self._record_trace(
            "claim.merge",
            input_refs=[source_id, resolved_target],
            output_refs=[resolved_target],
            evidence_refs=[],
            reason_codes=[reason, f"evidence_moved:{moved}"],
            governance_critical=True,
        )
        return {
            "ok": True,
            "source_id": source_id,
            "target_id": resolved_target,
            "evidence_moved": moved,
            "redirect": redir,
            "claim": self.get(resolved_target),
            "trace_id": tid,
            "note": "merge_does_not_verify",
        }

    @atomic_mutation
    def split(
        self,
        claim_id: str,
        parts: list[str],
        *,
        reason: str = "claim_split",
        bank_id: str | None = None,
    ) -> dict[str, Any]:
        """Split one Claim into ≥2 new Claims; original is redirected to the first part."""
        texts = [str(p).strip() for p in (parts or []) if str(p).strip()]
        if len(texts) < 2:
            return {"ok": False, "detail": "split_requires_two_parts"}
        src = self.get(claim_id, follow_redirect=False)
        if not src:
            return {"ok": False, "detail": "claim_not_found"}
        if src.get("redirects_to"):
            return {"ok": False, "detail": "claim_redirected"}
        bank = bank_id or str(src.get("bank_id") or "personal")
        if bank != src["bank_id"]:
            return {"ok": False, "detail": "cross_bank_split_denied"}
        created: list[dict[str, Any]] = []
        for i, text in enumerate(texts):
            # Distinct scope so split parts never collide with source same_as hash
            pack = self.propose_from_text(
                text,
                bank_id=bank,
                scope={"split_from": claim_id, "split_part": i},
            )
            if not pack.get("ok"):
                return {"ok": False, "detail": "part_propose_failed", "part": pack}
            cid = pack.get("claim_id") or (pack.get("same_as") or (pack.get("claim") or {}).get("id"))
            if not cid:
                return {"ok": False, "detail": "part_missing_id", "part": pack}
            if str(cid) == str(claim_id):
                return {"ok": False, "detail": "split_part_collapsed_to_source", "part": pack}
            created.append({"claim_id": str(cid), "propose": pack})
        primary = str(created[0]["claim_id"])
        redir = self.redirect(claim_id, primary, reason=reason)
        if not redir.get("ok"):
            return {"ok": False, "detail": "redirect_failed", "redirect": redir, "created": created}
        tid = self._record_trace(
            "claim.split",
            input_refs=[claim_id],
            output_refs=[str(c["claim_id"]) for c in created],
            reason_codes=[reason],
            governance_critical=True,
        )
        return {
            "ok": True,
            "source_id": claim_id,
            "parts": created,
            "primary_id": primary,
            "redirect": redir,
            "trace_id": tid,
            "note": "split_does_not_verify",
        }

    def relate(self, claim_id_a: str, claim_id_b: str) -> dict[str, Any]:
        """Evaluate typed relation between two persisted Claims."""
        from cognitive_memory.claims.ast import ClaimAST
        from cognitive_memory.judgment.relations import evaluate_relation

        a = self.get(claim_id_a, follow_redirect=True)
        b = self.get(claim_id_b, follow_redirect=True)
        if not a or not b:
            return {"ok": False, "detail": "claim_not_found"}
        try:
            ast_a = ClaimAST.model_validate((a.get("redirect_resolved") or a).get("ast") or a.get("ast"))
            ast_b = ClaimAST.model_validate((b.get("redirect_resolved") or b).get("ast") or b.get("ast"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": "ast_invalid", "error": str(exc)}
        result = evaluate_relation(ast_a, ast_b)
        tid = self._record_trace(
            "claim.relate",
            input_refs=[claim_id_a, claim_id_b],
            output_refs=[],
            reason_codes=list(result.reason_codes),
        )
        return {
            "ok": True,
            "a": claim_id_a,
            "b": claim_id_b,
            "relation": result.model_dump(mode="json"),
            "trace_id": tid,
        }

    def as_of(self, claim_id: str, timestamp: str, *, principal=None):
        """Reconstruct recorded lifecycle at a time; never infer historical evidence validity."""
        from datetime import datetime
        try:
            instant=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
            if instant.tzinfo is None:
                raise ValueError("timezone_required")
        except (ValueError,TypeError):
            return {"ok":False,"detail":"invalid_timestamp"}
        claim=self.get(claim_id,follow_redirect=False,principal=principal)
        if not claim:
            return None
        if principal and (claim.get("visibility") or {}).get("decision") != "allow":
            return None
        with self.db.exclusive() as conn:
            row=conn.execute(
                """SELECT * FROM claim_decision_records WHERE claim_id=?
                AND julianday(decision_at)<=julianday(?)
                ORDER BY julianday(decision_at) DESC,rowid DESC LIMIT 1""",
                (claim_id,timestamp),
            ).fetchone()
        if row is None:
            return None
        latest=dict(row)
        # This is a recorded-state view, not a new Verification decision.
        return {"ok":True,"claim_id":claim_id,"as_of":timestamp,
                "recorded_lifecycle":latest["next_status"],"decision":latest,
                "verification_status":"historical_unverified",
                "scope":"recorded_decision_reconstruction"}

    def replay(self, claim_id: str):
        """Rerun the deterministic parser/normalizer and compare to the immutable AST."""
        claim=self.get(claim_id,follow_redirect=False)
        if not claim:
            return {"ok":False,"detail":"claim_not_found"}
        if claim["normalization_version"]!=NORMALIZATION_VERSION:
            return {"ok":False,"detail":"historical_algorithm_unavailable",
                    "required":claim["normalization_version"],"verification_status":"unverified"}
        if claim["parser_version"] != PARSER_VERSION:
            return {"ok":False,"detail":"historical_parser_unavailable", "required":claim["parser_version"], "verification_status":"unverified"}
        parsed=parse_memory_text(claim["surface"])
        ast=parsed.ast.model_copy(update={"scope":claim["scope"]})
        current=normalized_hash(ast)
        return {"ok":current==claim["normalized_hash"],"claim_id":claim_id,
                "recorded_hash":claim["normalized_hash"],"replayed_hash":current,
                "algorithm_version":NORMALIZATION_VERSION,"executed":True,
                "parser_version":PARSER_VERSION,
                "verification_status":"unverified",
                "scope":"deterministic parser/AST replay; no model or evidence truth assertion"}

    def semantic_suggest(
        self,
        query: str,
        *,
        bank_id: str = "personal",
        limit: int = 5,
        min_score: float = 0.35,
    ) -> dict[str, Any]:
        """Assistive Claim recall by surface similarity — never Verified."""
        from cognitive_memory.retrieval.semantic_sidepath import pool_suggest

        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT id, surface, lifecycle, verification_status, bank_id
                FROM claims
                WHERE bank_id=? AND redirects_to IS NULL
                ORDER BY updated_at DESC LIMIT 200
                """,
                (bank_id,),
            ).fetchall()
        corpus = [
            {
                "id": r["id"],
                "surface": r["surface"],
                "lifecycle": r["lifecycle"],
                "verification_status": r["verification_status"],
            }
            for r in rows
        ]
        pack = pool_suggest(
            query,
            corpus,
            limit=limit,
            min_score=min_score,
            content_key="surface",
        )
        pack["verification_status"] = "unverified"
        pack["note"] = "semantic_claim_suggest_never_verified"
        tid = self._record_trace(
            "claim.semantic_suggest",
            input_refs=[query[:64]],
            output_refs=[str(x.get("id")) for x in pack.get("items") or []],
            reason_codes=["assistive_only"],
        )
        pack["trace_id"] = tid
        return pack

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        try:
            ast = json.loads(row["ast_json"] or "{}")
        except json.JSONDecodeError:
            ast = {}
        try:
            scope = json.loads(row["scope_json"] or "{}")
        except json.JSONDecodeError:
            scope = {}
        return {
            "id": row["id"],
            "version": row["version"],
            "normalization_version": row["normalization_version"],
            "parser_version": row["parser_version"],
            "bank_id": row["bank_id"],
            "ast_version": row["ast_version"],
            "ast": ast,
            "surface": row["surface"],
            "normalized_hash": row["normalized_hash"],
            "lifecycle": row["lifecycle"],
            "verification_status": row["verification_status"],
            "scope": scope,
            "source_memory_id": row["source_memory_id"],
            "redirects_to": row["redirects_to"],
            "validity_from": row["validity_from"] if "validity_from" in row.keys() else None,
            "validity_to": row["validity_to"] if "validity_to" in row.keys() else None,
            "superseded_at": row["superseded_at"] if "superseded_at" in row.keys() else None,
            "superseded_by": row["superseded_by"] if "superseded_by" in row.keys() else None,
            "retracted_at": row["retracted_at"] if "retracted_at" in row.keys() else None,
            "expired_at": row["expired_at"] if "expired_at" in row.keys() else None,
            "last_confirmed_at": row["last_confirmed_at"] if "last_confirmed_at" in row.keys() else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
