# -*- coding: utf-8 -*-
"""P7 rollback drill — restore Claim state from lifecycle event log.

Only reverses known fault_repair / fault_inject mutations. Never invents Verified.
"""

from __future__ import annotations

from typing import Any

from cognitive_memory.storage.db import Database


def _lifecycle_events(db: Database, claim_id: str) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT * FROM claim_lifecycle_events
            WHERE claim_id=? ORDER BY created_at ASC, rowid ASC
            """,
            (claim_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        out.append(
            {
                "id": d["id"],
                "from_status": d.get("from_status"),
                "to_status": d.get("to_status"),
                "reason": d.get("reason"),
                "actor": d.get("actor"),
                "created_at": d.get("created_at"),
            }
        )
    return out


def rollback_fault_repair(
    db: Database,
    claim_id: str,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Plan (and optionally execute) rollback to pre-repair lifecycle.

    execute=False → dry plan only.
    execute=True → restore from_status of the latest fault_repair_* event,
    and force verification_status=unverified (never re-assert Verified).
    """
    events = _lifecycle_events(db, claim_id)
    repair_ev = None
    for ev in reversed(events):
        reason = str(ev.get("reason") or "")
        if reason.startswith("fault_repair") or "fault_repair" in reason:
            repair_ev = ev
            break

    plan = {
        "claim_id": claim_id,
        "strategy": "restore_prior_lifecycle_from_event_log",
        "repair_event": repair_ev,
        "events_n": len(events),
        "target_lifecycle": (repair_ev or {}).get("from_status"),
        "verification_after": "unverified",
        "executed": False,
        "ok": True,
    }
    if not repair_ev:
        plan["ok"] = False
        plan["error"] = "no_fault_repair_event"
        return plan
    if not execute:
        plan["note"] = "rollback_drill_planned_not_applied"
        return plan

    target = str(repair_ev.get("from_status") or "proposed")
    # Never roll back into verified without evidence path
    if target == "verified":
        target = "proposed"
        plan["downgraded_from_verified"] = True

    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE claims
            SET lifecycle=?,
                verification_status='unverified',
                updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id=?
            """,
            (target, claim_id),
        )
        conn.execute(
            """
            INSERT INTO claim_lifecycle_events(
              id, claim_id, from_status, to_status, reason, actor,
              policy_version, evidence_refs_json, created_at
            ) VALUES (
              lower(hex(randomblob(6))), ?, ?, ?,
              'fault_rollback_drill', 'fault_rollback',
              'claim.lifecycle.v1', '[]',
              strftime('%Y-%m-%dT%H:%M:%SZ','now')
            )
            """,
            (claim_id, repair_ev.get("to_status") or "", target),
        )
    plan["executed"] = True
    plan["target_lifecycle"] = target
    plan["note"] = "rollback_applied_verification_forced_unverified"
    return plan
