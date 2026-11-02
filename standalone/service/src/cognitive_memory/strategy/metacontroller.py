# -*- coding: utf-8 -*-
"""Bounded MetaController — schedule / recommend only.

Never silent-activates Strategy. activate_if_authorized still hits RL_POLICY_DEPLOYMENT.
"""

from __future__ import annotations

from typing import Any

from cognitive_memory.governance.ab_experiment import STRATEGY_EXPERIMENT, assign_arm
from cognitive_memory.storage.db import Database
from cognitive_memory.strategy.offline_eval import run_offline_eval
from cognitive_memory.strategy.propensity import compute_propensity
from cognitive_memory.strategy.experience_store import ExperienceStore
from cognitive_memory.strategy.strategy_store import StrategyStore


def schedule(
    db: Database,
    *,
    action: str = "reflect",
    strategy_id: str | None = None,
    ab_experiment: bool = False,
) -> dict[str, Any]:
    """
    Actions:
    - reflect: list proposed/experimental
    - reevaluate: gated offline_eval for one strategy
    - reevaluate_all: offline_eval each experimental (still gated per call)
    - propose_retire: retire a non-active strategy
    - recommend_activate: propensity-based recommendation (no status change)
    - activate_if_authorized: StrategyStore.activate (Gate required)
    - ab_assign: optional strategy A/B exposure (BANDIT gate)
    """
    act = (action or "reflect").strip().lower()
    store = StrategyStore(db)

    if act == "reflect":
        items = store.list(limit=50)
        focus = [s for s in items if s.get("status") in {"proposed", "experimental"}]
        return {
            "ok": True,
            "action": "reflect",
            "focus_count": len(focus),
            "focus": focus[:20],
            "active_count": sum(1 for s in items if s.get("status") == "active"),
            "note": "scheduling only; no status mutation",
        }

    if act == "reevaluate":
        if not strategy_id:
            return {"ok": False, "detail": "strategy_id_required", "action": act}
        report = run_offline_eval(db, strategy_id)
        return {
            "ok": bool(report.get("ok")),
            "action": "reevaluate",
            "report": report,
            "note": "reevaluate does not deploy",
        }

    if act == "reevaluate_all":
        experimental = store.list(status="experimental", limit=50)
        proposed = store.list(status="proposed", limit=50)
        targets = experimental + proposed
        reports: list[dict[str, Any]] = []
        for s in targets[:20]:
            reports.append(run_offline_eval(db, s["id"]))
        ok_n = sum(1 for r in reports if r.get("ok"))
        return {
            "ok": True,
            "action": "reevaluate_all",
            "targets": len(targets),
            "evaluated_ok": ok_n,
            "reports": reports,
            "note": "batch eval does not activate",
        }

    if act == "propose_retire":
        if not strategy_id:
            return {"ok": False, "detail": "strategy_id_required", "action": act}
        row = store.get(strategy_id)
        if not row:
            return {"ok": False, "detail": "not_found", "strategy_id": strategy_id}
        if row.get("status") == "active":
            # Retiring active is allowed (de-escalate) without deploy gate
            pass
        return store.retire(strategy_id) | {"action": "propose_retire"}

    if act == "recommend_activate":
        if not strategy_id:
            return {"ok": False, "detail": "strategy_id_required", "action": act}
        row = store.get(strategy_id)
        if not row:
            return {"ok": False, "detail": "not_found", "strategy_id": strategy_id}
        logs = store.list_logs(strategy_id, limit=200)
        exp_store = ExperienceStore(db)
        exps: list[dict[str, Any]] = []
        for lg in logs:
            eid = lg.get("experience_id")
            if eid:
                got = exp_store.get(str(eid))
                if got:
                    exps.append(got)
        if not exps:
            exps = exp_store.list(limit=50)
        prop = compute_propensity(exps, strategy_scope=row.get("scope") or {})
        score = float(prop.get("propensity_score") or 0.0)
        recommend = score >= 0.55 and (prop.get("n") or 0) >= 3
        return {
            "ok": True,
            "action": "recommend_activate",
            "strategy_id": strategy_id,
            "recommend": recommend,
            "propensity": prop,
            "note": "recommendation only — call activate_if_authorized separately",
        }

    if act == "rank_candidates":
        # P6: score proposed/experimental strategies by propensity (no deploy)
        candidates = store.list(status="proposed", limit=50) + store.list(
            status="experimental", limit=50
        )
        exp_store = ExperienceStore(db)
        ranked: list[dict[str, Any]] = []
        for row in candidates:
            logs = store.list_logs(row["id"], limit=200)
            exps: list[dict[str, Any]] = []
            for lg in logs:
                eid = lg.get("experience_id")
                if eid:
                    got = exp_store.get(str(eid))
                    if got:
                        exps.append(got)
            prop = compute_propensity(exps, strategy_scope=row.get("scope") or {})
            ranked.append(
                {
                    "strategy_id": row["id"],
                    "name": row.get("name"),
                    "status": row.get("status"),
                    "propensity_score": prop.get("propensity_score"),
                    "n": prop.get("n"),
                    "mean_scope_overlap": prop.get("mean_scope_overlap"),
                    "recommend": bool(
                        (prop.get("propensity_score") or 0) >= 0.55
                        and (prop.get("n") or 0) >= 3
                    ),
                }
            )
        ranked.sort(key=lambda x: float(x.get("propensity_score") or 0.0), reverse=True)
        return {
            "ok": True,
            "action": "rank_candidates",
            "count": len(ranked),
            "ranked": ranked,
            "note": "ranking only — no activate / no Verified",
        }

    if act == "activate_if_authorized":
        if not strategy_id:
            return {"ok": False, "detail": "strategy_id_required", "action": act}
        out = store.activate(strategy_id)
        return {**out, "action": "activate_if_authorized"}

    if act == "ab_assign":
        if not ab_experiment and not strategy_id:
            return {
                "ok": False,
                "detail": "ab_experiment_or_strategy_id_required",
                "action": act,
            }
        return {
            "ok": True,
            "action": "ab_assign",
            "assignment": assign_arm(
                db,
                STRATEGY_EXPERIMENT,
                subject_key=str(strategy_id or ""),
                context={"strategy_id": strategy_id},
                arms=("control", "treatment"),
            ),
            "note": "A/B exposure only",
        }

    return {"ok": False, "detail": "unknown_action", "action": act}
