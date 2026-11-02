# -*- coding: utf-8 -*-
"""强化学习：Experience 落盘 + 门控离线统计拟合；部署走 Strategy ACTIVE Gate。"""

from __future__ import annotations

from typing import Any

from cognitive_memory.governance.capability_gate import (
    CapabilityLockedError,
    check_capability,
    require_capability,
)
from cognitive_memory.storage.db import Database


def record_feedback_sample(
    db: Database,
    sample: dict[str, Any],
) -> dict[str, Any]:
    """RL-A：写入 first-class Experience（不训练）。无 Gate 要求。"""
    from cognitive_memory.strategy.experience_store import ExperienceStore

    ctx = sample.get("context") if isinstance(sample.get("context"), dict) else {}
    action = sample.get("action") if isinstance(sample.get("action"), dict) else {}
    outcome = sample.get("outcome") if isinstance(sample.get("outcome"), dict) else {}
    memory_ids = sample.get("memory_ids") if isinstance(sample.get("memory_ids"), list) else []
    claim_ids = sample.get("claim_ids") if isinstance(sample.get("claim_ids"), list) else []
    reward = sample.get("reward")
    if reward is not None and not isinstance(reward, (int, float)):
        reward = None
    bank_id = str(sample.get("bank_id") or "personal")
    exp = ExperienceStore(db).create(
        context=ctx,
        action=action,
        outcome=outcome,
        memory_ids=[str(x) for x in memory_ids],
        claim_ids=[str(x) for x in claim_ids],
        reward=float(reward) if reward is not None else None,
        bank_id=bank_id,
        source=str(sample.get("source") or "rl_feedback"),
    )
    return {
        "ok": True,
        "phase": "RL-A",
        "recorded": True,
        "experience_id": exp.get("id"),
        "sample_keys": sorted(sample.keys()),
        "note": "Experience persisted; training still gated",
    }


def run_offline_training(db: Database, request: dict[str, Any] | None = None) -> dict[str, Any]:
    """RL-B：离线统计拟合（propensity）— 默认 LOCKED；不 ACTIVE。"""
    try:
        require_capability(db, "RL_OFFLINE_TRAINING")
    except CapabilityLockedError as exc:
        pack = check_capability(db, "RL_OFFLINE_TRAINING")
        return {
            "ok": False,
            "detail": "capability_locked",
            "capability": exc.capability,
            "gate": pack,
        }

    from cognitive_memory.strategy.experience_store import ExperienceStore
    from cognitive_memory.strategy.propensity import compute_propensity
    from cognitive_memory.strategy.strategy_store import StrategyStore

    req = dict(request or {})
    strategy_id = str(req.get("strategy_id") or "").strip()
    strategies = StrategyStore(db)
    experiences = ExperienceStore(db)

    if strategy_id:
        targets = [strategies.get(strategy_id)]
        if not targets[0]:
            return {"ok": False, "detail": "strategy_not_found", "strategy_id": strategy_id}
    else:
        targets = strategies.list(limit=50)

    fitted: list[dict[str, Any]] = []
    for strat in targets:
        if not strat:
            continue
        logs = strategies.list_logs(strat["id"], limit=500)
        exps: list[dict[str, Any]] = []
        for lg in logs:
            eid = lg.get("experience_id")
            if eid:
                row = experiences.get(str(eid))
                if row:
                    exps.append(row)
        if not exps:
            exps = experiences.list(limit=100)
        prop = compute_propensity(exps, strategy_scope=strat.get("scope") or {})
        strategies.update_propensity(
            strat["id"],
            {
                "score": prop["propensity_score"],
                "mean_reward": prop["mean_reward"],
                "success_rate": prop["success_rate"],
                "n": prop["n"],
                "trainer": "offline_propensity.v1",
            },
        )
        strategies.log(
            strat["id"],
            decision="offline_train",
            outcome="fitted",
            propensity=prop["propensity_score"],
            detail={"n": prop["n"]},
        )
        fitted.append({"strategy_id": strat["id"], "propensity": prop, "status": strat["status"]})

    return {
        "ok": True,
        "phase": "RL-B",
        "fitted": fitted,
        "count": len(fitted),
        "deploy": {
            "allowed": False,
            "detail": "use deploy_policy / strategy.activate with RL_POLICY_DEPLOYMENT",
        },
        "note": "statistical propensity fit only — no neural update, no ACTIVE",
        "request": req,
    }


def deploy_policy(db: Database, policy_id: str) -> dict[str, Any]:
    """RL-C：将 Strategy 置 ACTIVE — 独立 Gate。policy_id = strategy_id。"""
    try:
        require_capability(db, "RL_POLICY_DEPLOYMENT")
    except CapabilityLockedError as exc:
        return {
            "ok": False,
            "detail": "capability_locked",
            "capability": exc.capability,
            "policy_id": policy_id,
            "gate": check_capability(db, "RL_POLICY_DEPLOYMENT"),
        }

    from cognitive_memory.strategy.strategy_store import StrategyStore

    sid = str(policy_id or "").strip()
    if not sid:
        return {"ok": False, "detail": "policy_id_required"}
    store = StrategyStore(db)
    if not store.get(sid):
        return {"ok": False, "detail": "strategy_not_found", "policy_id": sid}
    out = store.activate(sid)
    return {
        **out,
        "phase": "RL-C",
        "policy_id": sid,
        "note": "ACTIVE strategy only; does not Verified Claims",
    }
