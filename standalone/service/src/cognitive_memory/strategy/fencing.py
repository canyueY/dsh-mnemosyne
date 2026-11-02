# -*- coding: utf-8 -*-
"""Worker 租约 fencing（E11）。

真实数据写入必须检查租约代次；过期 worker 即使处理器仍运行也不能写。
最终 task UPDATE 成功 ≠ 写入已受保护。
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database


class FencingError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WorkerLeaseManager:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def acquire(self, *, worker_id: str | None = None, resource: str = "consolidation") -> dict[str, Any]:
        wid = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        token = f"lease_{secrets.token_hex(8)}"
        now = _now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT generation FROM worker_leases WHERE resource=?",
                (resource,),
            ).fetchone()
            gen = int(row["generation"]) + 1 if row else 1
            conn.execute(
                """
                INSERT INTO worker_leases(resource, worker_id, fencing_token, generation, status, acquired_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(resource) DO UPDATE SET
                  worker_id=excluded.worker_id,
                  fencing_token=excluded.fencing_token,
                  generation=excluded.generation,
                  status='active',
                  acquired_at=excluded.acquired_at,
                  updated_at=excluded.updated_at
                """,
                (resource, wid, token, gen, now, now),
            )
        return {
            "ok": True,
            "resource": resource,
            "worker_id": wid,
            "fencing_token": token,
            "generation": gen,
        }

    def get(self, resource: str = "consolidation") -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT * FROM worker_leases WHERE resource=?", (resource,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def commit_artifact(
        self,
        *,
        fencing_token: str,
        expected_generation: int,
        artifact: dict[str, Any],
        resource: str = "consolidation",
    ) -> dict[str, Any]:
        """真实产物提交：写层 fencing，不只最终 task UPDATE。"""
        assert_lease_writable(
            self.db,
            fencing_token=fencing_token,
            expected_generation=expected_generation,
            resource=resource,
        )
        now = _now()
        aid = f"art_{uuid.uuid4().hex[:10]}"
        with self.db.transaction() as conn:
            # 再次检查（防 TOCTOU）
            row = conn.execute(
                "SELECT fencing_token, generation, status FROM worker_leases WHERE resource=?",
                (resource,),
            ).fetchone()
            if not row or row["status"] != "active":
                raise FencingError("lease_inactive", "lease not active at write")
            if row["fencing_token"] != fencing_token:
                raise FencingError("fencing_token_mismatch", "stale worker token rejected at write layer")
            if int(row["generation"]) != int(expected_generation):
                raise FencingError("generation_mismatch", "stale generation rejected at write layer")
            conn.execute(
                """
                INSERT INTO worker_artifacts(id, resource, fencing_token, generation, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    resource,
                    fencing_token,
                    int(expected_generation),
                    __import__("json").dumps(artifact, ensure_ascii=False),
                    now,
                ),
            )
        return {"ok": True, "artifact_id": aid, "fenced": True}


def assert_lease_writable(
    db: Database,
    *,
    fencing_token: str,
    expected_generation: int | None,
    resource: str = "consolidation",
) -> None:
    db.ensure_schema()
    with db.exclusive() as conn:
        row = conn.execute(
            "SELECT fencing_token, generation, status FROM worker_leases WHERE resource=?",
            (resource,),
        ).fetchone()
    if not row or row["status"] != "active":
        raise FencingError("lease_inactive", "no active lease for write")
    if fencing_token and row["fencing_token"] != fencing_token:
        raise FencingError("fencing_token_mismatch", "stale worker token rejected at write layer")
    if expected_generation is not None and int(row["generation"]) != int(expected_generation):
        raise FencingError("generation_mismatch", "stale generation rejected at write layer")
