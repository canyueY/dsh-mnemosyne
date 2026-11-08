# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException

from cognitive_memory.autonomy.store import AutonomyStore
from cognitive_memory.core.service import MemoryService
from cognitive_memory.governance.access import AccessContext, make_access_dep


def register_autonomy_routes(app: FastAPI, service: MemoryService, pref: str) -> None:
    def store() -> AutonomyStore:
        if not hasattr(app.state, "autonomy_store"):
            app.state.autonomy_store = AutonomyStore(service.db)
        return app.state.autonomy_store

    @app.post(f"{pref}/goals")
    def goals_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="goals.create")),
    ) -> dict[str, Any]:
        return store().create_goal(
            title=str(body.get("title") or ""),
            owner=str(body.get("owner") or access.actor_id),
            origin=str(body.get("origin") or "user"),
            parent_id=body.get("parent_id"),
            bank_id=body.get("bank_id"),
            scenario_id=body.get("scenario_id"),
            authorization=body.get("authorization") if isinstance(body.get("authorization"), dict) else {},
        )

    @app.get(f"{pref}/goals")
    def goals_list(
        limit: int = 40,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="goals.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_goals(limit=limit)
        return {"ok": True, "items": items, "count": len(items), "note": "goal_is_not_bank_or_scenario"}

    @app.post(f"{pref}/goals/{{goal_id}}/status")
    def goals_status(
        goal_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="goals.status")),
    ) -> dict[str, Any]:
        actor = "user" if access.is_node_admin else str(body.get("actor_origin") or "agent")
        return store().set_goal_status(goal_id, str(body.get("status") or ""), actor_origin=actor)

    @app.post(f"{pref}/goals/{{goal_id}}/plans")
    def goals_plan(
        goal_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="goals.plan")),
    ) -> dict[str, Any]:
        _ = access
        return store().add_plan(goal_id, title=str(body.get("title") or ""), depends_on=body.get("depends_on") if isinstance(body.get("depends_on"), list) else [])

    @app.post(f"{pref}/goals/{{goal_id}}/actions")
    def goals_action(
        goal_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="goals.action")),
    ) -> dict[str, Any]:
        _ = access
        return store().add_action(
            goal_id,
            kind=str(body.get("kind") or "note"),
            authorization=str(body.get("authorization") or ""),
            receipt=body.get("receipt") if isinstance(body.get("receipt"), dict) else {},
            trace_id=body.get("trace_id"),
            plan_id=body.get("plan_id"),
        )

    @app.post(f"{pref}/env-events")
    def events_ingest(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="events.ingest")),
    ) -> dict[str, Any]:
        pack = store().ingest_event(
            source=str(body.get("source") or ""),
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            response_level=str(body.get("response_level") or "E0"),
        )
        ev = pack.get("event") if isinstance(pack, dict) else None
        if pack.get("ok") and ev and not pack.get("deduped"):
            from cognitive_memory.domain.models import ObservationIn, SourceKind
            from cognitive_memory.ingestion import ingest_observation
            from cognitive_memory.governance.ingestion import IngestionGovernanceError

            try:
                obs = ingest_observation(
                    service,
                    ObservationIn(
                        source_kind=SourceKind.SYSTEM,
                        content=f"环境事件 {ev.get('source') or ''} {ev.get('id')}",
                        producer=str(access.actor_id or "env-event"),
                        data_ref=str(ev.get("id") or ""),
                        metadata={
                            "env_event_id": ev.get("id"),
                            "response_level": ev.get("response_level"),
                            "inferred": True,
                            "not_knowledge": True,
                        },
                    ),
                )
                pack["observation_id"] = obs.id
            except IngestionGovernanceError as exc:
                pack["observation"] = {"ok": False, "detail": exc.decision}
        return pack

    @app.get(f"{pref}/env-events")
    def events_list(
        limit: int = 40,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="events.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_events(limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/env-events/{{event_id}}/stage")
    def events_stage(
        event_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="events.stage")),
    ) -> dict[str, Any]:
        actor = "user" if access.is_node_admin else "agent"
        return store().advance_event(event_id, stage=str(body.get("stage") or ""), actor=actor)

    @app.post(f"{pref}/attention")
    def attn_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="attention.create")),
    ) -> dict[str, Any]:
        _ = access
        return store().enqueue_attention(
            title=str(body.get("title") or ""),
            priority=int(body.get("priority") or 0),
            deadline=body.get("deadline"),
            resource_budget=int(body.get("resource_budget") or 0),
            disposition=str(body.get("disposition") or "Deferred"),
            goal_id=body.get("goal_id"),
            event_id=body.get("event_id"),
        )

    @app.get(f"{pref}/attention")
    def attn_list(
        limit: int = 40,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="attention.list")),
    ) -> dict[str, Any]:
        _ = access
        items = store().list_attention(limit=limit)
        return {"ok": True, "items": items, "count": len(items), "note": "attention_does_not_override_governance"}

    @app.post(f"{pref}/attention/{{item_id}}")
    def attn_user(
        item_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="attention.user")),
    ) -> dict[str, Any]:
        _ = access
        item = store().user_attention(
            item_id,
            disposition=body.get("disposition"),
            status=body.get("status"),
            checkpoint=body.get("checkpoint"),
        )
        if not item.get("ok"):
            raise HTTPException(status_code=404, detail=item.get("detail"))
        return item
