# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request

from cognitive_memory.core.service import MemoryService
from cognitive_memory.governance.access import AccessContext, make_access_dep
from cognitive_memory.scenario.store import ScenarioStore


def register_scenario_routes(app: FastAPI, service: MemoryService, pref: str) -> None:
    def store() -> ScenarioStore:
        if not hasattr(app.state, "scenario_store"):
            app.state.scenario_store = ScenarioStore(service.db)
        return app.state.scenario_store

    def _ctx_from_header(task_context_id: str | None) -> dict[str, Any] | None:
        tid = (task_context_id or "").strip()
        if not tid:
            return None
        return store().get_task_context(tid)

    @app.post(f"{pref}/scenarios")
    def scenarios_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="scenarios.create")),
    ) -> dict[str, Any]:
        return store().create_scenario(
            name=str(body.get("name") or ""),
            owner=str(body.get("owner") or access.actor_id),
            parent_id=body.get("parent_id"),
            policy=body.get("policy") if isinstance(body.get("policy"), dict) else {},
            status=str(body.get("status") or "candidate"),
            audit_ref=body.get("audit_ref"),
        )

    @app.get(f"{pref}/scenarios")
    def scenarios_list(
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="scenarios.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_scenarios(limit=limit)
        return {"ok": True, "items": items, "count": len(items), "note": "scenario_is_not_bank"}

    @app.post(f"{pref}/scenarios/{{scenario_id}}/status")
    def scenarios_status(
        scenario_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="scenarios.status")),
    ) -> dict[str, Any]:
        _ = access
        return store().set_status(
            scenario_id,
            str(body.get("status") or ""),
            confirm=bool(body.get("confirm")),
            audit_ref=body.get("audit_ref"),
        )

    @app.get(f"{pref}/scenarios/{{scenario_id}}")
    def scenarios_get(
        scenario_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="scenarios.get")),
    ) -> dict[str, Any]:
        _ = access
        item = store().get_scenario(scenario_id)
        if not item:
            raise HTTPException(status_code=404, detail="scenario_not_found")
        item["memberships"] = store().list_memberships(scenario_id)
        return {"ok": True, "scenario": item}

    @app.post(f"{pref}/scenarios/{{scenario_id}}/memberships")
    def scenarios_membership(
        scenario_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="scenarios.membership")),
    ) -> dict[str, Any]:
        _ = access
        return store().add_membership(
            scenario_id,
            str(body.get("object_id") or ""),
            object_kind=str(body.get("object_kind") or ""),
            source=str(body.get("source") or "manual"),
            status=str(body.get("status") or "candidate"),
            valid_from=body.get("valid_from"),
            valid_to=body.get("valid_to"),
        )

    @app.post(f"{pref}/scenarios/{{scenario_id}}/suggest")
    def scenarios_suggest(
        scenario_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="scenarios.suggest")),
    ) -> dict[str, Any]:
        _ = access
        return store().suggest_candidate(
            scenario_id,
            str(body.get("object_id") or ""),
            fingerprint=str(body.get("fingerprint") or ""),
        )

    @app.post(f"{pref}/scenarios/candidates/reject")
    def scenarios_reject(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="scenarios.reject")),
    ) -> dict[str, Any]:
        _ = access
        fp = str(body.get("fingerprint") or "")
        if not fp:
            raise HTTPException(status_code=400, detail="fingerprint_required")
        return store().reject_candidate(fp, scenario_id=body.get("scenario_id"), reason=str(body.get("reason") or ""))

    @app.post(f"{pref}/scenarios/{{scenario_id}}/relations")
    def scenarios_relate(
        scenario_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="scenarios.relate")),
    ) -> dict[str, Any]:
        _ = access
        return store().relate_scenarios(scenario_id, str(body.get("to_id") or ""), str(body.get("kind") or ""))

    @app.post(f"{pref}/task-contexts")
    def task_context_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="task_context.create")),
    ) -> dict[str, Any]:
        return store().create_task_context(
            principal=str(body.get("principal") or access.actor_id),
            scenario_id=body.get("scenario_id"),
            project_id=body.get("project_id"),
            role_id=body.get("role_id"),
            allowed_banks=body.get("allowed_banks") if isinstance(body.get("allowed_banks"), list) else None,
            allowed_scopes=body.get("allowed_scopes") if isinstance(body.get("allowed_scopes"), list) else None,
            memory_allowlist=body.get("memory_allowlist") if isinstance(body.get("memory_allowlist"), list) else None,
            memory_denylist=body.get("memory_denylist") if isinstance(body.get("memory_denylist"), list) else None,
            sharing_policy=str(body.get("sharing_policy") or "isolated"),
            authorization_level=str(body.get("authorization_level") or "minimal"),
            context_budget=int(body.get("context_budget") or 32),
            expires_at=body.get("expires_at"),
        )

    @app.get(f"{pref}/task-contexts")
    def task_contexts_list(
        scenario_id: str | None = None,
        principal: str | None = None,
        sharing_policy: str | None = None,
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="task_context.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_task_contexts(
            scenario_id=scenario_id,
            principal=principal,
            sharing_policy=sharing_policy,
            limit=limit,
        )
        return {"ok": True, "items": items, "count": len(items), "note": "task_context_is_not_bank"}

    @app.get(f"{pref}/task-contexts/{{task_id}}")
    def task_context_get(
        task_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="task_context.get")),
    ) -> dict[str, Any]:
        _ = access
        item = store().get_task_context(task_id)
        if not item:
            raise HTTPException(status_code=404, detail="task_context_not_found")
        return {"ok": True, "task_context": item}

    @app.get(f"{pref}/task-contexts/{{task_id}}/scope")
    def task_context_scope(
        task_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="task_context.scope")),
    ) -> dict[str, Any]:
        _ = access
        item = store().get_task_context(task_id)
        if not item:
            raise HTTPException(status_code=404, detail="task_context_not_found")
        ids = store().allowed_ids(item)
        return {
            "ok": True,
            "task_context": item,
            "allowed_ids": sorted(ids) if ids is not None else [],
            "empty_membership_is_not_global": True,
        }

    app.state.resolve_task_context = _ctx_from_header
    _ = Request
    _ = Header
