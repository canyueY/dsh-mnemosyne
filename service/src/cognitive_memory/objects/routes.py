# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from cognitive_memory.core.service import MemoryService
from cognitive_memory.governance.access import AccessContext, make_access_dep
from cognitive_memory.objects.read_model import CognitiveObjectStore, ObjectUnavailable


def register_object_routes(app: FastAPI, service: MemoryService, pref: str) -> None:
    def store() -> CognitiveObjectStore:
        return CognitiveObjectStore(service.db)

    def _hide() -> HTTPException:
        return HTTPException(status_code=404, detail={"code": "object_unavailable"})

    def _gate_nodes(request: Request, access: AccessContext, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        from cognitive_memory.scenario.store import ScenarioStore

        tid = (request.headers.get("x-task-context-id") or "").strip()
        stamped: list[dict[str, Any]] = []
        for node in nodes:
            oid = str((node.get("ref") or {}).get("authority_id") or node.get("id") or "")
            row = dict(node)
            row["id"] = oid
            stamped.append(row)
        return ScenarioStore(service.db).apply_access_gate(
            access=access, task_context_id=tid, items=stamped
        )

    @app.get(f"{pref}/cognitive-objects")
    def object_list(
        request: Request,
        kind: str = "",
        bank_id: str | None = None,
        cursor: str = "",
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.list")),
    ) -> dict[str, Any]:
        pack = store().list(access, kind=kind, bank_id=bank_id, cursor=cursor, limit=limit)
        from cognitive_memory.scenario.store import ScenarioStore

        tid = (request.headers.get("x-task-context-id") or "").strip()
        gated = ScenarioStore(service.db).apply_access_gate(
            access=access, task_context_id=tid, items=pack.get("items") or []
        )
        pack["items"] = gated.get("items") or []
        pack["scenario_gate"] = {k: gated[k] for k in gated if k != "items"}
        if pack.get("disclosure"):
            pack["disclosure"]["scenario"] = gated.get("scope") or gated.get("detail") or "task_context"
        if gated.get("ok") is False:
            pack["ok"] = False
            pack["detail"] = gated.get("detail")
            pack["note"] = gated.get("note")
        return pack

    @app.get(f"{pref}/cognitive-objects/aggregates")
    def object_aggregates(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.aggregates")),
    ) -> dict[str, Any]:
        return store().aggregates(access)

    @app.get(f"{pref}/cognitive-objects/{{object_id}}")
    def object_get(
        object_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.get")),
    ) -> dict[str, Any]:
        try:
            return store().get(object_id, access)
        except ObjectUnavailable:
            raise _hide() from None

    @app.get(f"{pref}/cognitive-objects/{{object_id}}/relations")
    def object_relations(
        object_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.relations")),
    ) -> dict[str, Any]:
        try:
            return store().relations(object_id, access)
        except ObjectUnavailable:
            raise _hide() from None

    @app.get(f"{pref}/cognitive-objects/{{object_id}}/neighborhood")
    def object_neighborhood(
        object_id: str,
        request: Request,
        hops: int = Query(default=1, ge=1, le=2),
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.neighborhood")),
    ) -> dict[str, Any]:
        try:
            pack = store().neighborhood(object_id, access, hops=hops)
        except ObjectUnavailable:
            raise _hide() from None
        gated = _gate_nodes(request, access, pack.get("nodes") or [])
        allowed = {str(x.get("id") or "") for x in (gated.get("items") or [])}
        pack["nodes"] = [n for n in (pack.get("nodes") or []) if str((n.get("ref") or {}).get("authority_id") or "") in allowed]
        pack["edges"] = [
            e
            for e in (pack.get("edges") or [])
            if str(e.get("from") or "") in allowed and str(e.get("to") or "") in allowed
        ]
        pack["scenario_gate"] = {k: gated[k] for k in gated if k != "items"}
        if gated.get("ok") is False:
            pack["ok"] = False
            pack["detail"] = gated.get("detail")
            pack["nodes"] = []
            pack["edges"] = []
        return pack

    @app.get(f"{pref}/cognitive-objects/{{object_id}}/lineage")
    def object_lineage(
        object_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.lineage")),
    ) -> dict[str, Any]:
        try:
            return store().lineage(object_id, access)
        except ObjectUnavailable:
            raise _hide() from None

    @app.get(f"{pref}/cognitive-objects/{{object_id}}/timeline")
    def object_timeline(
        object_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="cognitive_object.timeline")),
    ) -> dict[str, Any]:
        try:
            return store().timeline(object_id, access)
        except ObjectUnavailable:
            raise _hide() from None
