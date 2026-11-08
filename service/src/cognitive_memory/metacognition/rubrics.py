# -*- coding: utf-8 -*-
"""Versioned evaluation rubrics. Missing evidence → inconclusive, never forced scores."""

from __future__ import annotations

from typing import Any

RUBRIC_ID = "rubric.metacog.v1"
RUBRIC_VERSION = 1

DIMENSIONS = [
    "factual_correctness",
    "source_completeness",
    "task_completion",
    "policy_compliance",
    "permission_compliance",
    "uncertainty_calibration",
    "tool_execution_success",
    "user_intent_alignment",
    "memory_usage_quality",
    "contradiction_handling",
    "response_efficiency",
    "recovery_quality",
]

HIGH_IMPACT_GATES = (
    "policy_compliance",
    "permission_compliance",
    "source_completeness",
)

SEED_RUBRIC: dict[str, Any] = {
    "rubric_id": RUBRIC_ID,
    "version": RUBRIC_VERSION,
    "applicable_task_types": ["general", "memory_write", "retrieval", "tool_use"],
    "required_evidence": ["outcome", "trace"],
    "scoring_method": "pass_fail_inconclusive",
    "pass_fail_inconclusive": {
        "pass": "all scored dimensions pass and no high-impact fail",
        "fail": "any high-impact gate fail",
        "inconclusive": "missing required evidence, evaluator error, or silence-only feedback",
    },
    "high_impact_gate": list(HIGH_IMPACT_GATES),
    "created_by": "system",
    "status": "active",
    "audit_ref": "metacog.rubric.v1",
}


def evaluate_with_rubric(
    *,
    scores: dict[str, Any] | None,
    supporting_evidence_ids: list[str],
    counter_evidence_ids: list[str],
    user_feedback_state: str,
    outcome_status: str | None,
    evaluator_failed: bool = False,
) -> dict[str, Any]:
    """Return result + normalized scores. Nulls stay null."""
    if evaluator_failed:
        return {
            "result": "inconclusive",
            "scores": {d: None for d in DIMENSIONS},
            "limitations": ["evaluator_failed"],
            "user_feedback_state": user_feedback_state or "unknown",
        }
    fb = (user_feedback_state or "unknown").strip().lower()
    if fb in {"", "silence", "silent", "none"}:
        fb = "unknown"
    if fb == "success":
        fb = "success"
    evidence_ok = bool(supporting_evidence_ids or counter_evidence_ids)
    if not evidence_ok:
        return {
            "result": "inconclusive",
            "scores": {d: None for d in DIMENSIONS},
            "limitations": ["insufficient_evidence"],
            "user_feedback_state": fb,
        }
    raw = dict(scores or {})
    normalized: dict[str, Any] = {}
    for dim in DIMENSIONS:
        val = raw.get(dim)
        if val in (None, "", "unknown"):
            normalized[dim] = None
        else:
            normalized[dim] = val
    if all(v is None for v in normalized.values()):
        return {
            "result": "inconclusive",
            "scores": normalized,
            "limitations": ["no_scored_dimensions"],
            "user_feedback_state": fb,
        }
    fails = []
    for gate in HIGH_IMPACT_GATES:
        if str(normalized.get(gate) or "").lower() in {"fail", "failed", "false", "0"}:
            fails.append(gate)
    if fails:
        result = "fail"
    elif outcome_status == "failed":
        result = "fail"
    else:
        result = "pass"
    return {
        "result": result,
        "scores": normalized,
        "limitations": [],
        "user_feedback_state": fb,
        "high_impact_fails": fails,
    }
