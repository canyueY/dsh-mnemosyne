# -*- coding: utf-8 -*-
"""P9 small-scale trial migration — scratch DB only; never touches production.

Pipeline:
  dry-run plan → (optional) write proposed Claims into scratch DB
  → consistency check → rollback scratch writes

Provenance gate still applies: write_blocked plans refuse scratch writes.
Synthetic `provenance_status=complete` fixtures may pass the gate for drills.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.migration.claim_dry_run import (
    DryRunPlan,
    _classify_action,
    assert_write_allowed,
)
from cognitive_memory.claims.parser import parse_memory_text
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def assess_provenance_strict(sample: dict[str, Any]) -> dict[str, Any]:
    """Stricter complete predicate for trial fixtures (never invent sources)."""
    if sample.get("provenance_status") == "complete" and sample.get(
        "independent_evidence_ids"
    ):
        ids = sample.get("independent_evidence_ids") or []
        if (
            isinstance(ids, list)
            and len(ids) >= 1
            and sample.get("source_type")
            and sample.get("fingerprint")
        ):
            return {
                "provenance_status": "complete",
                "provenance_flags": ["trial_fixture_complete"],
            }
    # Fall back to spike assessor
    from cognitive_memory.migration_spike.runner import assess_provenance

    return assess_provenance(sample)


def run_scratch_trial(
    samples: list[dict[str, Any]],
    *,
    scratch_db_path: str | Path,
    apply_writes: bool = False,
    rollback: bool = True,
) -> dict[str, Any]:
    """Execute a bounded trial against an ephemeral SQLite file."""
    # Annotate samples with strict provenance before planning
    enriched: list[dict[str, Any]] = []
    for s in samples:
        row = dict(s)
        prov = assess_provenance_strict(row)
        row["provenance_status"] = prov.get("provenance_status")
        row["_strict_provenance"] = prov
        # Feed assess_provenance via evidence fields already on sample
        enriched.append(row)

    actions: list[dict[str, Any]] = []
    complete_n = 0
    for sample in enriched:
        mid = str(
            sample.get("id")
            or sample.get("memory_id")
            or sample.get("source_memory_id")
            or ""
        )
        text = str(
            sample.get("content")
            or sample.get("text")
            or sample.get("source_text")
            or ""
        )
        pstatus = str(sample.get("provenance_status") or "unknown")
        if pstatus == "complete":
            complete_n += 1
        parse_status = "unresolved"
        multi = False
        if text.strip():
            parsed = parse_memory_text(text, metadata=sample.get("metadata") or {})
            parse_status = str(parsed.status)
            multi = bool(parsed.multi_claim)
        decision = _classify_action(
            parse_status=parse_status,
            provenance_status=pstatus,
            multi_claim=multi,
            false_merge_risk=False,
        )
        actions.append(
            {
                "memory_id": mid,
                "text": text,
                "parse_status": parse_status,
                "provenance_status": pstatus,
                "decision": decision,
                "forbid_verified": True,
            }
        )

    n = len(enriched) or 1
    complete_rate = complete_n / n
    write_blocked = complete_rate < 0.01
    plan = DryRunPlan(
        generated_at=_now(),
        sample_count=len(enriched),
        provenance_complete_rate=complete_rate,
        write_blocked=write_blocked,
        block_reason=(
            f"provenance_complete_rate={complete_rate:.4f} < gate=0.01"
            if write_blocked
            else ""
        ),
        strategy="scratch_trial_unresolved_first",
        actions=actions,
        stats={
            "complete_n": complete_n,
            "candidate_propose_n": sum(
                1 for a in actions if a["decision"]["action"] == "candidate_propose"
            ),
            "hold_n": sum(
                1 for a in actions if a["decision"]["action"] != "candidate_propose"
            ),
        },
        notes=["scratch_db_only", "no_production_writes", "no_silent_verified"],
    )

    report: dict[str, Any] = {
        "ok": True,
        "generated_at": _now(),
        "plan": plan.to_dict(),
        "scratch_db": str(scratch_db_path),
        "applied": False,
        "written_claim_ids": [],
        "rolled_back": False,
        "consistency": None,
    }

    guard = assert_write_allowed(plan)
    if write_blocked or not apply_writes:
        report["write_guard"] = guard if write_blocked else {
            "ok": False,
            "blocked": True,
            "reason": "apply_writes_false",
            "written": 0,
        }
        return report

    path = Path(scratch_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    db.ensure_schema()
    store = ClaimStore(db)
    written: list[str] = []
    try:
        for action in actions:
            if action["decision"]["action"] != "candidate_propose":
                continue
            text = action.get("text") or ""
            if not text.strip():
                continue
            out = store.propose_from_text(
                text,
                bank_id="personal",
                source_memory_id=action.get("memory_id") or None,
            )
            cid = out.get("claim_id")
            if cid:
                written.append(str(cid))
                # Force unverified / proposed — never Verified
                with db.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE claims
                        SET lifecycle='proposed', verification_status='unverified'
                        WHERE id=?
                        """,
                        (cid,),
                    )
        report["applied"] = True
        report["written_claim_ids"] = written

        # Consistency: all written claims proposed+unverified
        bad: list[str] = []
        for cid in written:
            row = store.get(cid, follow_redirect=False)
            if not row:
                bad.append(cid)
                continue
            if row.get("lifecycle") != "proposed" or row.get("verification_status") == "verified":
                bad.append(cid)
        report["consistency"] = {
            "ok": not bad,
            "checked": len(written),
            "bad": bad,
        }

        if rollback:
            with db.transaction() as conn:
                for cid in written:
                    conn.execute("DELETE FROM claim_lifecycle_events WHERE claim_id=?", (cid,))
                    conn.execute("DELETE FROM claim_evidence WHERE claim_id=?", (cid,))
                    conn.execute("DELETE FROM claims WHERE id=?", (cid,))
            report["rolled_back"] = True
            report["written_after_rollback"] = [
                store.get(cid, follow_redirect=False) for cid in written
            ]
            report["rollback_ok"] = all(
                x is None for x in report["written_after_rollback"]
            )
    finally:
        db.close()

    return report


def build_synthetic_complete_samples(n: int = 3) -> list[dict[str, Any]]:
    """Fixtures that satisfy strict complete provenance for scratch trials."""
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "id": f"mem_trial_{i}",
                "content": "我喜欢夜航星",
                "bank_id": "personal",
                "provenance_status": "complete",
                "independent_evidence_ids": [f"evd_trial_{i}"],
                "source_type": "observation",
                "fingerprint": f"fp_trial_{i}",
                "evidence_count": 1,
            }
        )
    return out
