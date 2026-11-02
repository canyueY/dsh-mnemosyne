# -*- coding: utf-8 -*-
"""
Task-book P7 pipeline:

Invariant Violation → Earliest Deviation → Differential Trace → Minimal Reproduction
→ Candidate Root Cause → Patch → Targeted/Regression/Replay/Rollback → Governance
"""

from __future__ import annotations

from typing import Any, Callable

from cognitive_memory.fault.invariants import check_claim_invariants
from cognitive_memory.fault.models import FaultStage, FaultStatus
from cognitive_memory.fault.store import FaultStore
from cognitive_memory.storage.db import Database


def _lifecycle_events(db: Database, claim_id: str) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT * FROM claim_lifecycle_events
            WHERE claim_id=? ORDER BY created_at ASC
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


def run_fault_pipeline(
    db: Database,
    *,
    claim_id: str,
    patch_fn: Callable[[], dict[str, Any]] | None = None,
    auto_repair: bool = False,
    run_regression: bool = True,
    execute_rollback: bool = False,
) -> dict[str, Any]:
    """Localize a Claim invariant break and optionally validate a repair patch."""
    from cognitive_memory.claims.store import ClaimStore
    from cognitive_memory.fault.repair import repair_claim_invariant
    from cognitive_memory.fault.rollback import rollback_fault_repair

    store = ClaimStore(db)
    faults = FaultStore(db)
    claim = store.get(claim_id, follow_redirect=False)
    evidence = store.list_evidence(claim_id) if claim else []
    inv = check_claim_invariants(claim, evidence_n=len(evidence))
    if inv.get("ok"):
        return {
            "ok": True,
            "detail": "no_invariant_violation",
            "claim_id": claim_id,
            "invariant": inv,
        }

    code = inv["violations"][0]["code"]
    case = faults.create(
        invariant_code=code,
        subject_refs=[claim_id],
        detail={"violations": inv["violations"]},
    )
    fid = case["id"]
    faults.append_stage(
        fid,
        FaultStage.INVARIANT.value,
        payload={"violations": inv["violations"]},
        ok=False,
    )

    events = _lifecycle_events(db, claim_id)
    earliest = None
    for ev in events:
        reason = str(ev.get("reason") or "")
        # First transition into a bad combo is the earliest deviation candidate
        if code == "verified_without_evidence" and (
            ev.get("to_status") == "verified" or "verified_without_evidence" in reason
        ):
            earliest = ev
            break
        if code == "silent_verified_on_proposed" and (
            "silent_verified" in reason or ev.get("to_status") == "proposed"
        ):
            earliest = ev
            break
        if code == "redirect_without_tombstone" and "redirect" in reason:
            earliest = ev
            break
        if code == "verified_status_lifecycle_mismatch" and (
            "lifecycle_mismatch" in reason
            or ev.get("to_status") in ("verified", "supported")
        ):
            earliest = ev
            break
    if earliest is None and events:
        earliest = events[-1]
    faults.append_stage(
        fid,
        FaultStage.EARLIEST.value,
        payload={"event": earliest, "events_n": len(events)},
        ok=earliest is not None,
    )

    # Differential trace: before/after lifecycle snapshot around earliest
    diff = {
        "before": (earliest or {}).get("from_status"),
        "after": (earliest or {}).get("to_status"),
        "reason": (earliest or {}).get("reason"),
        "claim_now": {
            "lifecycle": (claim or {}).get("lifecycle"),
            "verification_status": (claim or {}).get("verification_status"),
            "redirects_to": (claim or {}).get("redirects_to"),
        },
    }
    faults.append_stage(fid, FaultStage.DIFF_TRACE.value, payload=diff, ok=True)

    from cognitive_memory.fault.validation import snapshot_database, reproduce, replay_repair, regression_suite
    snapshot = snapshot_database(db)
    reproduction = reproduce(snapshot, claim_id, code)
    repro = {
        **reproduction,
        "steps": [
            f"load_claim:{claim_id}",
            f"expect_invariant_ok",
            f"observed:{code}",
        ],
        "minimal": bool(reproduction["ok"]),
        "fixture_claim_id": claim_id,
    }
    faults.append_stage(fid, FaultStage.MIN_REPRO.value, payload=repro, ok=reproduction["ok"])

    root = {
        "candidate": code,
        "hypothesis": {
            "verified_without_evidence": "submit_verified bypassed evidence gate",
            "silent_verified_on_proposed": "propose path wrote verified",
            "redirect_without_tombstone": "redirect missed superseded lifecycle",
            "verified_status_lifecycle_mismatch": "verification_status updated without lifecycle",
        }.get(code, "unknown"),
    }
    faults.append_stage(fid, FaultStage.ROOT_CAUSE.value, payload=root, ok=True)
    faults.set_status(fid, FaultStatus.LOCALIZED.value)

    patch_result: dict[str, Any] | None = None
    effective_patch = patch_fn
    if effective_patch is None and auto_repair:
        effective_patch = lambda: repair_claim_invariant(db, claim_id, code=code)

    if effective_patch is not None:
        patch_result = effective_patch()
        faults.append_stage(
            fid,
            FaultStage.PATCH.value,
            payload={"result": patch_result, "auto_repair": auto_repair and patch_fn is None},
            ok=bool(patch_result and patch_result.get("ok")),
        )
        faults.set_status(fid, FaultStatus.PATCHED.value)

        # Targeted re-check
        claim2 = store.get(claim_id, follow_redirect=False)
        evidence2 = store.list_evidence(claim_id) if claim2 else []
        inv2 = check_claim_invariants(claim2, evidence_n=len(evidence2))
        faults.append_stage(
            fid,
            FaultStage.TARGETED_TEST.value,
            payload={"invariant": inv2},
            ok=bool(inv2.get("ok")),
        )

        if run_regression:
            regression = regression_suite()
            faults.append_stage(
                fid,
                FaultStage.REGRESSION.value,
                payload=regression,
                ok=regression["passed"],
            )
            replay = replay_repair(snapshot, claim_id, code, claim2 or {})
            faults.append_stage(
                fid,
                FaultStage.REPLAY.value,
                payload=replay,
                ok=replay["ok"],
            )
            # Rollback drill: plan always; execute when execute_rollback=True
            rb = rollback_fault_repair(
                db, claim_id, execute=bool(execute_rollback)
            )
            rb["earliest"] = earliest
            faults.append_stage(
                fid,
                FaultStage.ROLLBACK.value,
                payload=rb,
                ok=bool(rb.get("ok")),
            )
            if execute_rollback and rb.get("executed"):
                faults.set_status(fid, FaultStatus.ROLLED_BACK.value)

        faults.append_stage(
            fid,
            FaultStage.GOVERNANCE.value,
            payload={
                "confirm_not_authorization": True,
                "no_silent_verified": True,
                "status": "review_required" if not inv2.get("ok") else "cleared",
                "rollback_executed": bool(execute_rollback),
            },
            ok=bool(inv2.get("ok")),
        )
        if execute_rollback:
            # Drill ended in rollback; keep ROLLED_BACK even if patch had validated.
            faults.set_status(fid, FaultStatus.ROLLED_BACK.value)
        elif inv2.get("ok") and reproduction["ok"] and run_regression and regression["passed"] and replay["ok"] and rb.get("ok"):
            faults.set_status(fid, FaultStatus.VALIDATED.value)

    snapshot.close()
    return {
        "ok": True,
        "fault_id": fid,
        "fault": faults.get(fid),
        "invariant": inv,
        "earliest_deviation": earliest,
        "differential_trace": diff,
        "minimal_reproduction": repro,
        "root_cause": root,
        "patch": patch_result,
    }
