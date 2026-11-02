# -*- coding: utf-8 -*-
"""Offline Strategy evaluation — report only; deploy stays gated."""

from __future__ import annotations

from typing import Any

from cognitive_memory.governance.capability_gate import (
    CapabilityLockedError,
    check_capability,
    require_capability,
)
from cognitive_memory.storage.db import Database
from cognitive_memory.strategy.experience_store import ExperienceStore
from cognitive_memory.strategy.propensity import compute_overlap, compute_propensity
from cognitive_memory.strategy.strategy_store import StrategyStore


def _gate_unlocked(db: Database) -> dict[str, Any] | None:
    """Accept either BANDIT_EXPERIMENT or RL_OFFLINE_TRAINING."""
    for cap in ("BANDIT_EXPERIMENT", "RL_OFFLINE_TRAINING"):
        try:
            require_capability(db, cap)
            return {"capability": cap, "ok": True}
        except CapabilityLockedError:
            continue
    return None


def run_offline_eval(
    db: Database,
    strategy_id: str,
    *,
    persist_propensity: bool = True,
    log_result: bool = True,
) -> dict[str, Any]:
    unlocked = _gate_unlocked(db)
    if unlocked is None:
        return {
            "ok": False,
            "detail": "capability_locked",
            "required_any_of": ["BANDIT_EXPERIMENT", "RL_OFFLINE_TRAINING"],
            "gates": {
                "BANDIT_EXPERIMENT": check_capability(db, "BANDIT_EXPERIMENT"),
                "RL_OFFLINE_TRAINING": check_capability(db, "RL_OFFLINE_TRAINING"),
            },
            "note": "offline eval locked; propensity helper remains available ungated",
        }

    strategies = StrategyStore(db)
    experiences = ExperienceStore(db)
    strat = strategies.get(strategy_id)
    if not strat:
        return {"ok": False, "detail": "strategy_not_found", "strategy_id": strategy_id}

    logs = strategies.list_logs(strategy_id, limit=500)
    exp_ids = [lg["experience_id"] for lg in logs if lg.get("experience_id")]
    exps: list[dict[str, Any]] = []
    for eid in exp_ids:
        row = experiences.get(str(eid))
        if row:
            exps.append(row)
    # If no linked experiences, fall back to recent bank samples (material only)
    if not exps:
        exps = experiences.list(limit=100)

    prop = compute_propensity(exps, strategy_scope=strat.get("scope") or {})
    from cognitive_memory.strategy.evaluation import evaluate_policy
    by_id = {exp["id"]: exp for exp in exps}
    evaluation_rows = []
    for log in logs:
        if log.get("decision") == "offline_eval":
            continue
        detail = log.get("detail") or {}
        exp = by_id.get(log.get("experience_id")) or {}
        evaluation_rows.append({
            **detail, "propensity":log.get("propensity"),
            "reward":exp.get("reward", (exp.get("outcome") or {}).get("reward")),
        })
    evaluation = evaluate_policy(evaluation_rows)
    pairwise: list[dict[str, Any]] = []
    for i in range(min(len(exps), 10)):
        for j in range(i + 1, min(len(exps), 10)):
            ov = compute_overlap(exps[i].get("context"), exps[j].get("context"))
            pairwise.append(
                {
                    "a": exps[i]["id"],
                    "b": exps[j]["id"],
                    "overlap": ov["overlap"],
                }
            )

    if persist_propensity:
        strategies.update_propensity(
            strategy_id,
            {
                "score": prop["propensity_score"],
                "mean_reward": prop["mean_reward"],
                "success_rate": prop["success_rate"],
                "mean_scope_overlap": prop["mean_scope_overlap"],
                "n": prop["n"],
                "eval_capability": unlocked["capability"],
            },
        )

    if log_result:
        strategies.log(
            strategy_id,
            decision="offline_eval",
            outcome="report",
            propensity=None,
            overlap=prop.get("mean_scope_overlap"),
            detail={"n": prop["n"], "pairwise_n": len(pairwise)},
        )

    return {
        "ok": True,
        "strategy_id": strategy_id,
        "status_unchanged": strat["status"],
        "gate": unlocked,
        "propensity": prop,
        "evaluation": evaluation,
        "status": evaluation["status"],
        "pairwise_overlap_sample": pairwise[:20],
        "deploy": {
            "allowed": False,
            "detail": "use /strategy/activate with RL_POLICY_DEPLOYMENT",
        },
        "note": "evaluation only — does not set status=active",
    }
