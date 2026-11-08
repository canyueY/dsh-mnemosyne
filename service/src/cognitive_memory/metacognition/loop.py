# -*- coding: utf-8 -*-
"""Three-level reflection. Never auto-activates strategy or writes Knowledge."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cognitive_memory.metacognition.store import MetacogStore


def level1_after_outcome(store: MetacogStore, experience_id: str, *, actor: str) -> dict[str, Any]:
    """Non-blocking structured review after a task. Inconclusive when evidence is missing."""
    exp = store.get_experience(experience_id)
    if not exp:
        return {"ok": False, "code": "experience_not_found"}
    outcome = store.get_latest_outcome(experience_id)
    supporting = list(exp.get("evidence_refs") or [])
    ev = store.create_evaluation(
        {
            "experience_id": experience_id,
            "evaluator_id": actor,
            "evaluator_kind": "level1_rubric",
            "supporting_evidence_ids": supporting,
            "user_feedback_state": (outcome or {}).get("user_feedback_state") or "unknown",
            "scores": {},
        },
        actor=actor,
        evaluator_failed=False,
    )
    evaluation = ev["evaluation"]
    needs_review = evaluation.get("result") == "inconclusive" or (outcome or {}).get("outcome_status") == "failed"
    return {
        "ok": True,
        "level": 1,
        "evaluation": evaluation,
        "needs_human_review": bool(needs_review),
        "enter_cycle_review": bool((outcome or {}).get("outcome_status") == "failed"),
        "blocks_response": False,
        "note": "Level 1 does not promote Knowledge or publish strategy",
    }


def cycle_review(store: MetacogStore, *, actor: str, min_cluster: int = 2, limit: int = 80) -> dict[str, Any]:
    """Cluster repeated failures; propose Hypothesis only. One failure ≠ global strategy."""
    experiences = store.list_experiences(limit=limit)
    failed = [e for e in experiences if e.get("outcome_status") in {"failed", "partially_succeeded"}]
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in failed:
        key = (item.get("goal") or "unknown").strip().lower()[:80] or "unknown"
        clusters[key].append(item)
    hypotheses: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key, group in clusters.items():
        if len(group) < min_cluster:
            skipped.append(
                {
                    "goal": key,
                    "n": len(group),
                    "reason": "insufficient_for_global_strategy",
                }
            )
            continue
        hyp = store.create_hypothesis(
            {
                "statement": f"repeated failure pattern on goal cluster: {key}",
                "source_experience_ids": [g["id"] for g in group],
                "modality": "possible",
                "scope": group[0].get("scope") or "unknown",
            },
            actor=actor,
        )["hypothesis"]
        hypotheses.append(hyp)
    return {
        "ok": True,
        "level": 2,
        "failed_count": len(failed),
        "clusters": {k: len(v) for k, v in clusters.items()},
        "hypotheses": hypotheses,
        "skipped_singleton_failures": skipped,
        "strategy_activated": False,
        "note": "Level 2 proposes Candidate hypotheses only; no Active strategy",
    }


def canary_observe(store: MetacogStore, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Gray-release watch. May draft a rollback ChangePlan; never auto-publishes."""
    bank = str(body.get("bank_id") or "personal")
    kind = str(body.get("kind") or "strategy")
    current = store.current_snapshot(kind, bank)
    worse = bool(body.get("candidate_worse") or body.get("metrics_worsened") or body.get("safety_fail"))
    plan: dict[str, Any] | None = None
    if worse and current and current.get("id"):
        plan = {
            "status": "draft",
            "approval": "human",
            "executed": False,
            "actions": [{"op": "rollback_snapshot", "snapshot_id": current["id"]}],
        }
        sid = str(body.get("strategy_id") or "")
        if sid:
            try:
                attached = store.attach_change_plan(
                    sid,
                    {"actions": plan["actions"], "risk": "high"},
                    actor=actor,
                )
                plan = attached.get("change_plan") or plan
            except Exception:
                pass
    after = store.current_snapshot(kind, bank)
    return {
        "ok": True,
        "level": 3,
        "metrics_worsened": worse,
        "rollback_proposed": bool(worse),
        "rollback_executed": False,
        "change_plan": plan,
        "current_snapshot_id": (after or {}).get("id"),
        "unchanged": (after or {}).get("id") == (current or {}).get("id"),
        "note": "high-impact rollback requires publisher approval",
    }
