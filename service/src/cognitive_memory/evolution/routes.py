# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, FastAPI

from cognitive_memory.core.service import MemoryService
from cognitive_memory.evolution.store import FROZEN, EvolutionStore
from cognitive_memory.governance.access import AccessContext, make_access_dep


def register_evolution_routes(app: FastAPI, service: MemoryService, pref: str) -> None:
    def store() -> EvolutionStore:
        if not hasattr(app.state, "evolution_store"):
            app.state.evolution_store = EvolutionStore(service.db)
        return app.state.evolution_store

    @app.post(f"{pref}/repair-proposals")
    def rpr_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="repair.create")),
    ) -> dict[str, Any]:
        _ = access
        return store().propose_repair(
            fault_id=body.get("fault_id"),
            evidence=body.get("evidence") if isinstance(body.get("evidence"), list) else [],
            impact=body.get("impact") if isinstance(body.get("impact"), dict) else {},
            risk_level=str(body.get("risk_level") or "high"),
            change_set=body.get("change_set") if isinstance(body.get("change_set"), list) else [],
            tests=body.get("tests") if isinstance(body.get("tests"), list) else [],
            rollback=body.get("rollback") if isinstance(body.get("rollback"), dict) else None,
            sandbox=body.get("sandbox") if isinstance(body.get("sandbox"), dict) else {},
            expires_at=body.get("expires_at"),
        )

    @app.get(f"{pref}/repair-proposals")
    def rpr_list(
        limit: int = 40,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="repair.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_proposals(limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/repair-proposals/{{proposal_id}}/apply")
    def rpr_apply(
        proposal_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="repair.apply")),
    ) -> dict[str, Any]:
        _ = access
        return store().apply_proposal(proposal_id, confirm=bool(body.get("confirm")))

    @app.get(f"{pref}/tech-debt")
    def debt_list(
        limit: int = 40,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="debt.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_debt(limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/tech-debt")
    def debt_add(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="debt.add")),
    ) -> dict[str, Any]:
        _ = access
        return store().add_debt(title=str(body.get("title") or ""), area=str(body.get("area") or "unknown"), notes=str(body.get("notes") or ""))

    @app.get(f"{pref}/evolution-experiments")
    def exp_list(
        limit: int = 40,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="experiment.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_experiments(limit=limit)
        return {"ok": True, "items": items, "count": len(items), "note": "failure_must_not_pollute_production"}

    @app.post(f"{pref}/evolution-experiments")
    def exp_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experiment.create")),
    ) -> dict[str, Any]:
        _ = access
        return store().create_experiment(
            title=str(body.get("title") or ""),
            control=body.get("control") if isinstance(body.get("control"), dict) else {},
            bank_id=str(body.get("bank_id") or "sandbox"),
            version=str(body.get("version") or "1"),
        )

    @app.post(f"{pref}/evolution-experiments/{{experiment_id}}/result")
    def exp_result(
        experiment_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experiment.result")),
    ) -> dict[str, Any]:
        _ = access
        return store().record_experiment_result(
            experiment_id,
            result=body.get("result") if isinstance(body.get("result"), dict) else {},
            status=str(body.get("status") or "completed"),
        )

    @app.get(f"{pref}/capability-gates")
    def gates_list(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="gates.list")),
    ) -> dict[str, Any]:
        _ = access
        return {"ok": True, "items": store().list_gates(), "frozen": sorted(FROZEN)}

    @app.post(f"{pref}/capability-gates/{{gate_id}}")
    def gates_set(
        gate_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="gates.set")),
    ) -> dict[str, Any]:
        return store().set_gate(gate_id, autonomy_level=str(body.get("autonomy_level") or "none"), actor=access.actor_id)
