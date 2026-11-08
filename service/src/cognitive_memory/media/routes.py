# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.models import ObservationIn, SourceKind
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.governance.access import AccessContext, make_access_dep
from cognitive_memory.ingestion import ingest_observation
from cognitive_memory.media.store import (
    PARSER_SOURCE_KIND,
    MediaStore,
    extract_artifact_text,
)


def register_media_routes(app: FastAPI, service: MemoryService, pref: str) -> None:
    def store() -> MediaStore:
        if not hasattr(app.state, "media_store"):
            app.state.media_store = MediaStore(service.db)
        return app.state.media_store

    @app.post(f"{pref}/media/assets")
    def media_create(
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="media.create")),
    ) -> dict[str, Any]:
        return store().register_asset(
            content=str(body.get("content") or body.get("uri") or ""),
            mime=str(body.get("mime") or "application/octet-stream"),
            owner=str(body.get("owner") or access.actor_id),
            bank_id=body.get("bank_id"),
            uri=str(body.get("uri") or ""),
            sensitivity=str(body.get("sensitivity") or "unknown"),
            scope=body.get("scope"),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        )

    @app.get(f"{pref}/media/assets")
    def media_list(
        limit: int = 40,
        bank_id: str | None = None,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="media.list")),
    ) -> dict[str, Any]:
        items = [
            store().redact_asset(it, is_node_admin=access.is_node_admin) or it
            for it in store().list_assets(limit=limit, bank_id=bank_id)
        ]
        return {"ok": True, "items": items, "count": len(items), "note": "parse_is_not_knowledge"}

    @app.get(f"{pref}/media/assets/{{asset_id}}")
    def media_get(
        asset_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="media.get")),
    ) -> dict[str, Any]:
        item = store().redact_asset(store().get_asset(asset_id), is_node_admin=access.is_node_admin)
        if not item:
            raise HTTPException(status_code=404, detail="asset_not_found")
        item["artifacts"] = store().list_artifacts(asset_id)
        item["locators"] = store().list_locators(asset_id)
        return {"ok": True, "asset": item, "note": "parse_is_not_knowledge"}

    @app.post(f"{pref}/media/assets/{{asset_id}}/parse")
    def media_parse(
        asset_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="media.parse")),
    ) -> dict[str, Any]:
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        if isinstance(body.get("locator"), dict):
            params = dict(params)
            params["locator"] = body.get("locator")
            if body.get("locator_kind"):
                params["locator_kind"] = body.get("locator_kind")
        return store().parse(
            asset_id,
            parser=str(body.get("parser") or ""),
            parser_version=str(body.get("parser_version") or "1"),
            params=params,
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            quality=body.get("quality"),
            uncertainty=body.get("uncertainty"),
            lineage_parent_id=body.get("lineage_parent_id"),
        )

    @app.post(f"{pref}/media/assets/{{asset_id}}/locators")
    def media_locator(
        asset_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="media.locator")),
    ) -> dict[str, Any]:
        _ = access
        return store().add_locator(
            asset_id,
            kind=str(body.get("kind") or "unknown"),
            locator=body.get("locator") if isinstance(body.get("locator"), dict) else {},
            parsed_artifact_id=body.get("parsed_artifact_id"),
        )

    @app.get(f"{pref}/media/independence")
    def media_independence(
        a: str,
        b: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="media.independence")),
    ) -> dict[str, Any]:
        _ = access
        return store().independence(a, b)

    @app.get(f"{pref}/media/assets/{{asset_id}}/delete-preview")
    def media_delete_preview(
        asset_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="media.delete_preview")),
    ) -> dict[str, Any]:
        _ = access
        return store().delete_preview(asset_id)

    @app.post(f"{pref}/media/assets/{{asset_id}}/lifecycle")
    def media_lifecycle(
        asset_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="media.lifecycle")),
    ) -> dict[str, Any]:
        _ = access
        return store().archive_or_delete(asset_id, action=str(body.get("action") or ""))

    @app.post(f"{pref}/media/artifacts/{{artifact_id}}/correct")
    def media_correct(
        artifact_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="media.correct")),
    ) -> dict[str, Any]:
        return store().correct(
            artifact_id,
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            quality=body.get("quality"),
            uncertainty=body.get("uncertainty"),
            actor=str(access.actor_id or ""),
        )

    @app.post(f"{pref}/media/artifacts/{{artifact_id}}/cite")
    def media_cite(
        artifact_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="media.cite")),
    ) -> dict[str, Any]:
        art = store().get_artifact(artifact_id)
        if not art:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        asset = store().get_asset(str(art.get("media_asset_id") or ""))
        if not asset:
            raise HTTPException(status_code=404, detail="asset_not_found")
        text = extract_artifact_text(art.get("payload") if isinstance(art.get("payload"), dict) else {})
        kind = PARSER_SOURCE_KIND.get(str(art.get("parser") or ""), "system")
        try:
            source_kind = SourceKind(kind)
        except ValueError:
            source_kind = SourceKind.SYSTEM
        locs = [x for x in store().list_locators(asset["id"]) if x.get("parsed_artifact_id") == artifact_id]
        locator = (locs[0].get("locator") if locs else {}) or {}
        from cognitive_memory.governance.ingestion import IngestionGovernanceError

        try:
            obs = ingest_observation(
            service,
            ObservationIn(
                source_kind=source_kind,
                content=text or f"parsed {art.get('parser')} {artifact_id}",
                producer=str(access.actor_id or "media.parse"),
                data_ref=artifact_id,
                metadata={
                    "media_asset_id": asset["id"],
                    "parsed_artifact_id": artifact_id,
                    "inferred": True,
                    "not_knowledge": True,
                    "root_source_id": asset["id"],
                    "source_ref": {
                        "source_id": artifact_id,
                        "root_source_id": asset["id"],
                        "locator": locator,
                    },
                    "bank_id": asset.get("bank_id") or "personal",
                },
            ),
            )
        except IngestionGovernanceError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "ingestion_denied", "decision": exc.decision, "note": "media_cite_not_knowledge"},
            ) from exc
        ev = EvidenceObjectStore(service.db).create(
            source_type="parsed_artifact",
            source_ref=artifact_id,
            bank_id=str(asset.get("bank_id") or "personal"),
            content_for_fingerprint=text,
            producer=str(access.actor_id or "media.parse"),
            root_source_id=asset["id"],
            validation_state="unverified",
            apply_source_contract=True,
            metadata={
                "media_asset_id": asset["id"],
                "parsed_artifact_id": artifact_id,
                "observation_id": obs.id,
                "locator": locator,
                "not_knowledge": True,
                "inferred": True,
            },
        )
        evid = (ev.get("evidence") or {}).get("id") if isinstance(ev, dict) else None
        store().attach_refs(artifact_id, observation_id=obs.id, evidence_id=evid)
        return {
            "ok": True,
            "observation_id": obs.id,
            "evidence": ev,
            "knowledge_written": False,
            "note": "observation_evidence_candidate_not_knowledge",
            "requested_claim": bool(body.get("as_claim")),
        }
