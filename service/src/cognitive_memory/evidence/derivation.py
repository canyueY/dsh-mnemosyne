# -*- coding: utf-8 -*-
"""DerivationManifest：派生依赖、修订失效与提交时重查（E02）。

源变化先阻止旧产物作为当前证据，再异步重建；重建失败不恢复 current。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

FRESHNESS_CURRENT = "current"
FRESHNESS_STALE = "stale"
FRESHNESS_REBUILDING = "rebuilding"
FRESHNESS_FAILED = "failed"
_FRESH = {FRESHNESS_CURRENT, FRESHNESS_STALE, FRESHNESS_REBUILDING, FRESHNESS_FAILED}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "drv") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def config_hash_of(config: dict[str, Any] | None) -> str:
    raw = json.dumps(config or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class DerivationStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def register(
        self,
        *,
        artifact_kind: str,
        dependencies: list[dict[str, Any]],
        processor_version: str,
        config: dict[str, Any] | None = None,
        bank_id: str = "personal",
        cursor: str | None = None,
        derived_id: str | None = None,
        payload: dict[str, Any] | None = None,
        freshness: str = FRESHNESS_CURRENT,
    ) -> dict[str, Any]:
        did = str(derived_id or _new_id())
        deps = []
        for d in dependencies or []:
            if not isinstance(d, dict):
                continue
            deps.append(
                {
                    "id": str(d.get("id") or ""),
                    "revision": str(d.get("revision") or "0"),
                    "hash": str(d.get("hash") or d.get("content_hash") or ""),
                }
            )
        fr = freshness if freshness in _FRESH else FRESHNESS_CURRENT
        now = _now()
        ch = config_hash_of(config)
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO derivation_manifests(
                  derived_id, bank_id, artifact_kind, dependencies_json,
                  processor_version, config_hash, cursor, freshness,
                  reason_codes_json, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)
                ON CONFLICT(derived_id) DO UPDATE SET
                  dependencies_json=excluded.dependencies_json,
                  processor_version=excluded.processor_version,
                  config_hash=excluded.config_hash,
                  cursor=excluded.cursor,
                  freshness=excluded.freshness,
                  payload_json=excluded.payload_json,
                  updated_at=excluded.updated_at
                """,
                (
                    did,
                    bank_id,
                    str(artifact_kind or "derived"),
                    json.dumps(deps, ensure_ascii=False),
                    str(processor_version or "unknown"),
                    ch,
                    cursor,
                    fr,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get(did) or {"derived_id": did, "freshness": fr}

    def get(self, derived_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT * FROM derivation_manifests WHERE derived_id=?",
                (derived_id,),
            ).fetchone()
        return self._row(row) if row else None

    def list_by_dependency(self, dep_id: str, *, bank_id: str | None = None) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM derivation_manifests").fetchall()
        out = []
        for row in rows:
            item = self._row(row)
            if bank_id and item["bank_id"] != bank_id:
                continue
            if any(d.get("id") == dep_id for d in item["dependencies"]):
                out.append(item)
        return out

    def is_usable_current_evidence(self, derived_id: str) -> dict[str, Any]:
        item = self.get(derived_id)
        if not item:
            return {
                "ok": False,
                "usable": False,
                "freshness": "missing",
                "reason_codes": ["derived_missing"],
            }
        usable = item["freshness"] == FRESHNESS_CURRENT
        reasons = list(item.get("reason_codes") or [])
        if not usable and not reasons:
            reasons = [f"freshness_{item['freshness']}"]
        return {
            "ok": True,
            "usable": usable,
            "freshness": item["freshness"],
            "reason_codes": reasons,
            "manifest": item,
        }

    def mark_stale_for_source(
        self,
        source_id: str,
        *,
        new_revision: str,
        reason: str = "source_revised",
        bank_id: str | None = None,
    ) -> dict[str, Any]:
        """源修订：依赖该源的派生物立即不可作当前证据。"""
        touched = []
        now = _now()
        for item in self.list_by_dependency(source_id, bank_id=bank_id):
            reasons = list(item.get("reason_codes") or [])
            for code in (reason, "source_stale", f"new_revision:{new_revision}"):
                if code not in reasons:
                    reasons.append(code)
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE derivation_manifests
                    SET freshness=?, reason_codes_json=?, updated_at=?
                    WHERE derived_id=?
                    """,
                    (
                        FRESHNESS_STALE,
                        json.dumps(reasons, ensure_ascii=False),
                        now,
                        item["derived_id"],
                    ),
                )
            touched.append(item["derived_id"])
        return {"ok": True, "stale_ids": touched, "count": len(touched)}

    def begin_rebuild(self, derived_id: str) -> dict[str, Any]:
        item = self.get(derived_id)
        if not item:
            return {"ok": False, "detail": "not_found"}
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE derivation_manifests
                SET freshness=?, updated_at=?
                WHERE derived_id=?
                """,
                (FRESHNESS_REBUILDING, _now(), derived_id),
            )
        return {"ok": True, "manifest": self.get(derived_id)}

    def finish_rebuild(
        self,
        derived_id: str,
        *,
        success: bool,
        dependencies: list[dict[str, Any]] | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """重建成功 → current；失败 → failed（不恢复旧 current）。"""
        item = self.get(derived_id)
        if not item:
            return {"ok": False, "detail": "not_found"}
        now = _now()
        if success:
            deps = dependencies if dependencies is not None else item["dependencies"]
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE derivation_manifests
                    SET freshness=?, dependencies_json=?, reason_codes_json='[]',
                        payload_json=?, updated_at=?
                    WHERE derived_id=?
                    """,
                    (
                        FRESHNESS_CURRENT,
                        json.dumps(deps, ensure_ascii=False),
                        json.dumps(payload if payload is not None else item["payload"], ensure_ascii=False),
                        now,
                        derived_id,
                    ),
                )
        else:
            reasons = list(item.get("reason_codes") or [])
            for code in (reason or "rebuild_failed", "source_stale"):
                if code not in reasons:
                    reasons.append(code)
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE derivation_manifests
                    SET freshness=?, reason_codes_json=?, updated_at=?
                    WHERE derived_id=?
                    """,
                    (
                        FRESHNESS_FAILED,
                        json.dumps(reasons, ensure_ascii=False),
                        now,
                        derived_id,
                    ),
                )
        return {"ok": True, "manifest": self.get(derived_id)}

    def revise_source(
        self,
        source_id: str,
        *,
        new_revision: str,
        content_hash: str = "",
        bank_id: str = "personal",
        rebuild: bool = True,
        rebuild_succeeds: bool | None = None,
    ) -> dict[str, Any]:
        """原子：标记依赖 stale →（可选）重建；失败则仍过期。"""
        stale = self.mark_stale_for_source(
            source_id, new_revision=new_revision, bank_id=bank_id
        )
        rebuild_results = []
        if rebuild:
            for did in stale["stale_ids"]:
                self.begin_rebuild(did)
                ok = True if rebuild_succeeds is None else bool(rebuild_succeeds)
                if rebuild_succeeds is None:
                    # 默认尝试：无外部处理器时记为失败，避免假 current
                    ok = False
                deps = None
                if ok:
                    deps = [{"id": source_id, "revision": new_revision, "hash": content_hash}]
                rebuild_results.append(
                    self.finish_rebuild(
                        did,
                        success=ok,
                        dependencies=deps,
                        reason="rebuild_failed" if not ok else "",
                    )
                )
        return {"ok": True, "stale": stale, "rebuilds": rebuild_results}

    def record_tombstone(
        self,
        *,
        root_source_id: str,
        content_hash: str = "",
        bank_id: str = "personal",
        reason: str = "authorized_delete",
        actor: str = "system",
    ) -> dict[str, Any]:
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO source_revision_tombstones(
                  bank_id, root_source_id, content_hash, reason, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bank_id, root_source_id, content_hash) DO UPDATE SET
                  reason=excluded.reason,
                  actor=excluded.actor
                """,
                (bank_id, root_source_id, content_hash or "", reason, actor, now),
            )
        return {
            "ok": True,
            "bank_id": bank_id,
            "root_source_id": root_source_id,
            "content_hash": content_hash or "",
        }

    def is_tombstoned(
        self,
        *,
        root_source_id: str,
        content_hash: str = "",
        bank_id: str = "personal",
    ) -> dict[str, Any]:
        with self.db.exclusive() as conn:
            row = conn.execute(
                """
                SELECT * FROM source_revision_tombstones
                WHERE bank_id=? AND root_source_id=? AND content_hash=?
                """,
                (bank_id, root_source_id, content_hash or ""),
            ).fetchone()
            # 也匹配「整根来源」tombstone（空 hash）
            if row is None and content_hash:
                row = conn.execute(
                    """
                    SELECT * FROM source_revision_tombstones
                    WHERE bank_id=? AND root_source_id=? AND content_hash=''
                    """,
                    (bank_id, root_source_id),
                ).fetchone()
        if not row:
            return {"tombstoned": False, "unverifiable": False}
        return {
            "tombstoned": True,
            "unverifiable": True,
            "reason": row["reason"],
            "actor": row["actor"],
            "created_at": row["created_at"],
            "note": "source_unverifiable_due_to_authorized_deletion",
        }

    def guard_reimport(
        self,
        *,
        root_source_id: str,
        content_hash: str = "",
        bank_id: str = "personal",
    ) -> dict[str, Any]:
        hit = self.is_tombstoned(
            root_source_id=root_source_id, content_hash=content_hash, bank_id=bank_id
        )
        if hit.get("tombstoned"):
            return {
                "ok": False,
                "allowed": False,
                "code": "tombstone_blocks_reimport",
                "detail": hit,
            }
        return {"ok": True, "allowed": True}

    def recheck_revisions(
        self,
        expected: list[dict[str, Any]],
        *,
        current_revisions: dict[str, str],
    ) -> dict[str, Any]:
        """提交边界重查：处理中源变更 → 结果不可 chosen。"""
        mismatches = []
        for dep in expected or []:
            if not isinstance(dep, dict):
                continue
            did = str(dep.get("id") or "")
            want = str(dep.get("revision") or "0")
            have = str(current_revisions.get(did, ""))
            if have and have != want:
                mismatches.append({"id": did, "expected": want, "actual": have})
        stale_derived = []
        for dep in expected or []:
            if not isinstance(dep, dict):
                continue
            # 若 expected 指向 derived_id，检查 freshness
            mid = str(dep.get("derived_id") or dep.get("id") or "")
            item = self.get(mid)
            if item and item["freshness"] != FRESHNESS_CURRENT:
                stale_derived.append(mid)
        ok = not mismatches and not stale_derived
        return {
            "ok": ok,
            "chosen_allowed": ok,
            "status": "chosen" if ok else "unknown",
            "reason_codes": []
            if ok
            else (["source_revised_during_check"] if mismatches else [])
            + (["source_stale"] if stale_derived else []),
            "mismatches": mismatches,
            "stale_derived": stale_derived,
        }

    def _row(self, row: Any) -> dict[str, Any]:
        def _j(raw: Any, default: Any) -> Any:
            try:
                return json.loads(raw or ("{}" if isinstance(default, dict) else "[]"))
            except json.JSONDecodeError:
                return default

        return {
            "derived_id": row["derived_id"],
            "bank_id": row["bank_id"],
            "artifact_kind": row["artifact_kind"],
            "dependencies": _j(row["dependencies_json"], []),
            "processor_version": row["processor_version"],
            "config_hash": row["config_hash"],
            "cursor": row["cursor"],
            "freshness": row["freshness"],
            "reason_codes": _j(row["reason_codes_json"], []),
            "payload": _j(row["payload_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
