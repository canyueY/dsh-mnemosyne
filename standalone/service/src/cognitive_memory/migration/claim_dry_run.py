# -*- coding: utf-8 -*-
"""P9 Claim migration dry-run — provenance-gated, read-only by default.

Hard gate: when provenance_complete_rate == 0 (P0.5 finding), production writes
are blocked. Dry-run still emits a plan that routes incomplete provenance to
Unresolved / needs_review and never proposes Verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from cognitive_memory.claims.normalize import normalized_hash
from cognitive_memory.claims.parser import parse_memory_text
from cognitive_memory.migration_spike.runner import assess_provenance, assess_security


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# P0.5 proved complete_rate=0 → require redesign before any write path.
MIN_PROVENANCE_COMPLETE_RATE = 0.01


@dataclass
class DryRunPlan:
    generated_at: str
    sample_count: int
    provenance_complete_rate: float
    write_blocked: bool
    block_reason: str = ""
    strategy: str = "provenance_gated_unresolved_first"
    actions: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "sample_count": self.sample_count,
            "provenance_complete_rate": self.provenance_complete_rate,
            "write_blocked": self.write_blocked,
            "block_reason": self.block_reason,
            "strategy": self.strategy,
            "stats": self.stats,
            "actions": self.actions,
            "notes": self.notes,
            "allow_write": False,
            "note": "dry_run_only; no Claim/Memory writes performed",
        }


def _classify_action(
    *,
    parse_status: str,
    provenance_status: str,
    multi_claim: bool,
    false_merge_risk: bool,
) -> dict[str, Any]:
    if provenance_status == "unknown" or provenance_status == "partial":
        return {
            "action": "hold_unresolved",
            "lifecycle_if_written": "proposed",
            "verification_if_written": "unverified",
            "reason": f"provenance_{provenance_status}",
            "requires_manual_review": True,
        }
    if false_merge_risk:
        return {
            "action": "hold_false_merge_review",
            "lifecycle_if_written": "proposed",
            "verification_if_written": "unverified",
            "reason": "false_merge_candidate",
            "requires_manual_review": True,
        }
    if parse_status == "unresolved" or multi_claim:
        return {
            "action": "route_unresolved_or_split_review",
            "lifecycle_if_written": "proposed",
            "verification_if_written": "unverified",
            "reason": parse_status if parse_status != "parsed" else "multi_claim",
            "requires_manual_review": True,
        }
    if parse_status == "parsed" and provenance_status == "complete":
        return {
            "action": "candidate_propose",
            "lifecycle_if_written": "proposed",
            "verification_if_written": "unverified",
            "reason": "provenance_complete_and_parsed",
            "requires_manual_review": False,
        }
    return {
        "action": "hold_needs_review",
        "lifecycle_if_written": "proposed",
        "verification_if_written": "unverified",
        "reason": "default_fail_closed",
        "requires_manual_review": True,
    }


def plan_claim_migration_dry_run(
    samples: Iterable[dict[str, Any]],
    *,
    min_provenance_complete_rate: float = MIN_PROVENANCE_COMPLETE_RATE,
    hash_groups: dict[str, list[str]] | None = None,
) -> DryRunPlan:
    """Build a read-only migration plan. Never writes Claim rows."""
    rows = list(samples)
    actions: list[dict[str, Any]] = []
    complete_n = 0
    unknown_n = 0
    proposed_n = 0
    hold_n = 0
    groups = hash_groups or {}

    for sample in rows:
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
        prov = assess_provenance(sample)
        sec = assess_security(sample)
        parse_status = "unresolved"
        multi = False
        nhash = ""
        if text.strip():
            parsed = parse_memory_text(text, metadata=sample.get("metadata") or {})
            parse_status = str(parsed.status)
            multi = bool(parsed.multi_claim)
            try:
                nhash = normalized_hash(parsed.ast)
            except Exception:  # noqa: BLE001
                nhash = ""

        pstatus = str(prov.get("provenance_status") or "unknown")
        if pstatus == "complete":
            complete_n += 1
        if pstatus == "unknown":
            unknown_n += 1

        false_merge = bool(nhash and len(groups.get(nhash) or []) > 1)
        decision = _classify_action(
            parse_status=parse_status,
            provenance_status=pstatus,
            multi_claim=multi,
            false_merge_risk=false_merge,
        )
        if decision["action"] == "candidate_propose":
            proposed_n += 1
        else:
            hold_n += 1

        actions.append(
            {
                "memory_id": mid,
                "parse_status": parse_status,
                "provenance": prov,
                "security": sec,
                "decision": decision,
                # Explicit: migration never auto-verifies
                "forbid_verified": True,
            }
        )

    n = len(rows) or 1
    complete_rate = complete_n / n
    write_blocked = complete_rate < float(min_provenance_complete_rate)
    block_reason = ""
    notes = [
        "dry_run_only",
        "no_silent_verified",
        "confirm_not_authorization",
        "historical_unverified_is_not_verified",
    ]
    if write_blocked:
        block_reason = (
            f"provenance_complete_rate={complete_rate:.4f} "
            f"< gate={min_provenance_complete_rate}"
        )
        notes.append(
            "P0.5 gate: redesign provenance before any production Claim write path"
        )
        notes.append(
            "strategy: keep holding Unresolved/needs_review until provenance rebuilt"
        )

    return DryRunPlan(
        generated_at=_now(),
        sample_count=len(rows),
        provenance_complete_rate=complete_rate,
        write_blocked=write_blocked,
        block_reason=block_reason,
        strategy="provenance_gated_unresolved_first",
        actions=actions,
        stats={
            "complete_n": complete_n,
            "unknown_n": unknown_n,
            "candidate_propose_n": proposed_n,
            "hold_n": hold_n,
            "would_write_n": 0 if write_blocked else proposed_n,
        },
        notes=notes,
    )


def assert_write_allowed(plan: DryRunPlan) -> dict[str, Any]:
    """Guard for any future write executor — always respects dry-run gate."""
    if plan.write_blocked:
        return {
            "ok": False,
            "blocked": True,
            "reason": plan.block_reason or "provenance_gate",
            "written": 0,
        }
    return {
        "ok": False,
        "blocked": True,
        "reason": "write_executor_not_enabled_use_dry_run_only",
        "written": 0,
    }
