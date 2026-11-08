# -*- coding: utf-8 -*-
"""MediaAsset / ParsedArtifact / SourceLocator. Parse output is Observation/Evidence candidate, never Knowledge."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

PARSERS = frozenset({"ocr", "asr", "video_frame", "code_diff", "structured", "interaction"})
LOCATOR_KINDS = frozenset({"page", "time_range", "frame", "region", "code_line", "table_cell", "unknown"})
LIFECYCLE = frozenset({"active", "archived", "deleted"})
PARSER_LOCATOR = {
    "ocr": "region",
    "asr": "time_range",
    "video_frame": "frame",
    "code_diff": "code_line",
    "structured": "table_cell",
    "interaction": "unknown",
}
PARSER_SOURCE_KIND = {
    "ocr": "video_observation",
    "asr": "dialog",
    "video_frame": "video_observation",
    "code_diff": "system",
    "structured": "system",
    "interaction": "system",
}
SENSITIVE = frozenset({"secret", "private", "high", "restricted"})


def extract_artifact_text(payload: dict[str, Any] | None) -> str:
    data = payload if isinstance(payload, dict) else {}
    for key in ("text", "transcript", "desc", "diff", "content"):
        val = data.get(key)
        if val:
            return str(val)
    extra = data.get("data")
    if extra is not None:
        return json.dumps(extra, ensure_ascii=False)[:2000]
    return ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _j(raw: Any, default: Any):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw or ("[]" if isinstance(default, list) else "{}"))
    except json.JSONDecodeError:
        return default


class MediaStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def register_asset(
        self,
        *,
        content: bytes | str,
        mime: str,
        owner: str,
        bank_id: str | None,
        uri: str = "",
        sensitivity: str = "unknown",
        scope: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content or b"")
        digest = hashlib.sha256(raw).hexdigest()
        now = _now()
        with self.db.transaction() as conn:
            hit = conn.execute(
                "SELECT * FROM media_assets WHERE content_hash=? AND bank_id=? AND deleted_at IS NULL",
                (digest, bank_id or "unknown"),
            ).fetchone()
            if hit:
                return {"ok": True, "asset": self._asset(hit), "deduped": True}
            aid = _id("med")
            conn.execute(
                """
                INSERT INTO media_assets(
                  id, content_hash, mime, owner, bank_id, scope, lifecycle, uri,
                  sensitivity, created_at, archived_at, deleted_at, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    aid, digest, mime or "", owner or "", bank_id or "unknown", scope,
                    "active", uri or "", sensitivity or "unknown", now, None, None,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        return {"ok": True, "asset": self.get_asset(aid), "deduped": False}

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
        return self._asset(row) if row else None

    def list_assets(self, *, limit: int = 40, bank_id: str | None = None) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            if bank_id:
                rows = conn.execute(
                    "SELECT * FROM media_assets WHERE bank_id=? AND (deleted_at IS NULL OR deleted_at='') ORDER BY created_at DESC LIMIT ?",
                    (bank_id, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM media_assets WHERE deleted_at IS NULL OR deleted_at='' ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [self._asset(r) for r in rows]

    def parse(
        self,
        asset_id: str,
        *,
        parser: str,
        parser_version: str = "1",
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        quality: str | None = None,
        uncertainty: str | None = None,
        lineage_parent_id: str | None = None,
    ) -> dict[str, Any]:
        kind = (parser or "").strip().lower()
        if kind not in PARSERS:
            return {"ok": False, "detail": "unknown_parser"}
        asset = self.get_asset(asset_id)
        if not asset or asset.get("lifecycle") == "deleted":
            return {"ok": False, "detail": "asset_not_found"}
        pid = _id("par")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO parsed_artifacts(
                  id, media_asset_id, parser, parser_version, params_json, quality,
                  uncertainty, created_at, lineage_parent_id, payload_json, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid, asset_id, kind, parser_version or "1",
                    json.dumps(params or {}, ensure_ascii=False), quality, uncertainty, now,
                    lineage_parent_id, json.dumps(payload or {}, ensure_ascii=False), "candidate",
                ),
            )
        artifact = self.get_artifact(pid)
        locator = None
        loc_kind = PARSER_LOCATOR.get(kind, "unknown")
        if isinstance(params, dict) and isinstance(params.get("locator"), dict):
            locator = self.add_locator(
                asset_id, kind=str(params.get("locator_kind") or loc_kind),
                locator=params.get("locator") or {}, parsed_artifact_id=pid,
            ).get("locator")
        elif loc_kind != "unknown":
            locator = self.add_locator(
                asset_id, kind=loc_kind, locator={"parser": kind, "parser_version": parser_version or "1"},
                parsed_artifact_id=pid,
            ).get("locator")
        return {
            "ok": True,
            "artifact": artifact,
            "locator": locator,
            "source_kind": PARSER_SOURCE_KIND.get(kind, "system"),
            "note": "parsed_artifact_is_observation_evidence_candidate_not_knowledge",
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM parsed_artifacts WHERE id=?", (artifact_id,)).fetchone()
        return self._art(row) if row else None

    def list_artifacts(self, asset_id: str) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            rows = conn.execute(
                "SELECT * FROM parsed_artifacts WHERE media_asset_id=? ORDER BY created_at",
                (asset_id,),
            ).fetchall()
        return [self._art(r) for r in rows]

    def add_locator(
        self,
        asset_id: str,
        *,
        kind: str,
        locator: dict[str, Any],
        parsed_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.get_asset(asset_id):
            return {"ok": False, "detail": "asset_not_found"}
        k = kind if kind in LOCATOR_KINDS else "unknown"
        lid = _id("loc")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO source_locators(id, media_asset_id, parsed_artifact_id, kind, locator_json, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (lid, asset_id, parsed_artifact_id, k, json.dumps(locator or {}, ensure_ascii=False), now),
            )
        return {"ok": True, "locator": self.get_locator(lid)}

    def get_locator(self, locator_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM source_locators WHERE id=?", (locator_id,)).fetchone()
        return self._loc(row) if row else None

    def list_locators(self, asset_id: str) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            rows = conn.execute(
                "SELECT * FROM source_locators WHERE media_asset_id=? ORDER BY created_at",
                (asset_id,),
            ).fetchall()
        return [self._loc(r) for r in rows]

    def correct(
        self,
        artifact_id: str,
        *,
        payload: dict[str, Any] | None = None,
        quality: str | None = None,
        uncertainty: str | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        art = self.get_artifact(artifact_id)
        if not art:
            return {"ok": False, "detail": "artifact_not_found"}
        merged = dict(art.get("payload") or {})
        if isinstance(payload, dict):
            merged.update(payload)
        merged["_correction"] = {"from": artifact_id, "actor": actor, "inferred": True}
        ver = str(int(str(art.get("parser_version") or "1").split(".")[0] or "1") + 1)
        return self.parse(
            art["media_asset_id"],
            parser=str(art.get("parser") or ""),
            parser_version=ver,
            params={"corrected_from": artifact_id},
            payload=merged,
            quality=quality or art.get("quality"),
            uncertainty=uncertainty or art.get("uncertainty"),
            lineage_parent_id=artifact_id,
        )

    def attach_refs(self, artifact_id: str, *, observation_id: str, evidence_id: str | None = None) -> dict[str, Any]:
        art = self.get_artifact(artifact_id)
        if not art:
            return {"ok": False, "detail": "artifact_not_found"}
        payload = dict(art.get("payload") or {})
        payload["_observation_id"] = observation_id
        if evidence_id:
            payload["_evidence_id"] = evidence_id
        payload["_not_knowledge"] = True
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE parsed_artifacts SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), artifact_id),
            )
        return {"ok": True, "artifact": self.get_artifact(artifact_id)}

    def redact_asset(self, asset: dict[str, Any] | None, *, is_node_admin: bool) -> dict[str, Any] | None:
        if not asset:
            return None
        out = dict(asset)
        if str(out.get("sensitivity") or "") in SENSITIVE and not is_node_admin:
            out["uri"] = ""
            out["redacted"] = True
            out["note"] = "sensitive_media_hidden_non_admin"
        return out

    def independence(self, artifact_a: str, artifact_b: str) -> dict[str, Any]:
        a, b = self.get_artifact(artifact_a), self.get_artifact(artifact_b)
        if not a or not b:
            return {"ok": False, "detail": "artifact_not_found"}
        same = a["media_asset_id"] == b["media_asset_id"]
        return {
            "ok": True,
            "independent": (not same),
            "reason": "same_media_asset" if same else "distinct_media_asset",
            "root_source_id": a["media_asset_id"] if same else None,
            "note": "同源解析不增加虚假可信度",
        }

    def delete_preview(self, asset_id: str) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if not asset:
            return {"ok": False, "detail": "asset_not_found"}
        arts = self.list_artifacts(asset_id)
        locs = self.list_locators(asset_id)
        with self.db.exclusive() as conn:
            ev_rows = conn.execute(
                "SELECT id FROM evidences WHERE metadata_json LIKE ? LIMIT 50",
                (f"%{asset_id}%",),
            ).fetchall()
        return {
            "ok": True,
            "asset_id": asset_id,
            "derived": {
                "parsed_artifacts": [a["id"] for a in arts],
                "locators": [x["id"] for x in locs],
                "evidence_candidates": [r["id"] for r in ev_rows],
            },
            "note": "preview_only_no_delete",
        }

    def archive_or_delete(self, asset_id: str, *, action: str) -> dict[str, Any]:
        if action not in {"archive", "delete"}:
            return {"ok": False, "detail": "bad_action"}
        asset = self.get_asset(asset_id)
        if not asset:
            return {"ok": False, "detail": "asset_not_found"}
        now = _now()
        with self.db.transaction() as conn:
            if action == "archive":
                conn.execute(
                    "UPDATE media_assets SET lifecycle='archived', archived_at=? WHERE id=?",
                    (now, asset_id),
                )
            else:
                conn.execute(
                    "UPDATE media_assets SET lifecycle='deleted', deleted_at=? WHERE id=?",
                    (now, asset_id),
                )
        return {"ok": True, "asset": self.get_asset(asset_id), "preview": self.delete_preview(asset_id)}

    def _asset(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "content_hash": row["content_hash"],
            "mime": row["mime"],
            "owner": row["owner"],
            "bank_id": row["bank_id"],
            "scope": row["scope"],
            "lifecycle": row["lifecycle"],
            "uri": row["uri"],
            "sensitivity": row["sensitivity"],
            "created_at": row["created_at"],
            "archived_at": row["archived_at"],
            "deleted_at": row["deleted_at"],
            "metadata": _j(row["metadata_json"], {}),
        }

    def _art(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "media_asset_id": row["media_asset_id"],
            "parser": row["parser"],
            "parser_version": row["parser_version"],
            "params": _j(row["params_json"], {}),
            "quality": row["quality"],
            "uncertainty": row["uncertainty"],
            "created_at": row["created_at"],
            "lineage_parent_id": row["lineage_parent_id"],
            "payload": _j(row["payload_json"], {}),
            "status": row["status"],
            "kind": "ParsedArtifact",
        }

    def _loc(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "media_asset_id": row["media_asset_id"],
            "parsed_artifact_id": row["parsed_artifact_id"],
            "kind": row["kind"],
            "locator": _j(row["locator_json"], {}),
            "created_at": row["created_at"],
        }
