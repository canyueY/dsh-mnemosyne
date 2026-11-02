# -*- coding: utf-8 -*-
"""Safe, reversible-by-tombstone repairs for known Claim invariant breaks."""

from __future__ import annotations

from typing import Any

from cognitive_memory.claims.lifecycle import ClaimLifecycle
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.storage.db import Database


def repair_claim_invariant(db: Database, claim_id: str, *, code: str) -> dict[str, Any]:
    from cognitive_memory.tracing.service import JudgmentTraceService
    with db.transaction() as conn:
        before = ClaimStore(db).get(claim_id, follow_redirect=False)
        result = _repair_claim_invariant(db, claim_id, code=code)
        if result.get("ok") and before:
            after = ClaimStore(db).get(claim_id, follow_redirect=False)
            conn.execute(
                """INSERT INTO claim_lifecycle_events(
                  id,claim_id,from_status,to_status,reason,actor,policy_version,evidence_refs_json,created_at
                ) VALUES (lower(hex(randomblob(8))),?,?,?,?, 'fault_repair','claim.lifecycle.v1','[]',
                          strftime('%Y-%m-%dT%H:%M:%SZ','now'))""",
                (claim_id,before["lifecycle"],after["lifecycle"],f"fault_repair:{code}"),
            )
            JudgmentTraceService(db).record("fault.repair",input_refs=[claim_id],
                                            reason_codes=[code],governance_critical=True)
        return result


def _repair_claim_invariant(db: Database, claim_id: str, *, code: str) -> dict[str, Any]:
    """Apply the minimal governance-safe patch for a known invariant code."""
    store = ClaimStore(db)
    claim = store.get(claim_id, follow_redirect=False)
    if not claim:
        return {"ok": False, "error": "claim_missing"}

    if code == "redirect_without_tombstone":
        target = claim.get("redirects_to")
        if not target:
            return {"ok": False, "error": "no_redirect_target"}
        return store.redirect(claim_id, str(target), reason="fault_repair_tombstone")

    if code == "verified_without_evidence":
        # Downgrade: verified without evidence → proposed + unverified (no silent keep)
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE claims
                SET lifecycle='proposed', verification_status='unverified',
                    updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE id=?
                """,
                (claim_id,),
            )
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at
                ) VALUES (
                  lower(hex(randomblob(6))), ?, 'verified', 'proposed',
                  'fault_repair_verified_without_evidence', 'fault_repair',
                  'claim.lifecycle.v1', '[]',
                  strftime('%Y-%m-%dT%H:%M:%SZ','now')
                )
                """,
                (claim_id,),
            )
        return {"ok": True, "repaired": code, "lifecycle": "proposed"}

    if code == "silent_verified_on_proposed":
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE claims
                SET verification_status='unverified',
                    updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE id=?
                """,
                (claim_id,),
            )
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at
                ) VALUES (
                  lower(hex(randomblob(6))), ?, 'proposed', 'proposed',
                  'fault_repair_silent_verified', 'fault_repair',
                  'claim.lifecycle.v1', '[]',
                  strftime('%Y-%m-%dT%H:%M:%SZ','now')
                )
                """,
                (claim_id,),
            )
        return {"ok": True, "repaired": code, "verification_status": "unverified"}

    if code == "verified_status_lifecycle_mismatch":
        # Prefer lifecycle truth when evidence missing: strip verified mark
        evidence_n = len(store.list_evidence(claim_id))
        if evidence_n < 1:
            with db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE claims
                    SET verification_status='unverified',
                        updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE id=?
                    """,
                    (claim_id,),
                )
            return {
                "ok": True,
                "repaired": code,
                "action": "strip_verified_mark",
                "evidence_n": evidence_n,
            }
        # Evidence present: align lifecycle upward via proper transition path if legal
        life = str(claim.get("lifecycle") or "")
        if life == "supported":
            out = store.transition(
                claim_id,
                ClaimLifecycle.VERIFIED,
                reason="fault_repair_align_lifecycle",
                evidence_refs=[e["evidence_ref"] for e in store.list_evidence(claim_id)],
                actor="fault_repair",
            )
            return {"ok": bool(out.get("ok")), "repaired": code, "transition": out}
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE claims
                SET verification_status='unverified',
                    updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE id=?
                """,
                (claim_id,),
            )
        return {"ok": True, "repaired": code, "action": "strip_verified_mark_fallback"}

    return {"ok": False, "error": "unknown_code", "code": code}
