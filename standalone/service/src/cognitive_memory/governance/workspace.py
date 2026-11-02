# -*- coding: utf-8 -*-
"""工作区读取授权（E01 AC04）。

未登记 allow_read 的 workspace 不得采集 / 注入 / 巩固。
授权读取 ≠ 自动 verified。
"""

from __future__ import annotations

import json
from typing import Any

from cognitive_memory.governance.policy import PolicyDeniedError


_WORKSPACE_ACTIONS = frozenset({"capture", "ingest", "consolidate", "export", "train"})


def ensure_workspace_tables(db: Any) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_grants (
              workspace_id TEXT PRIMARY KEY,
              allow_read INTEGER NOT NULL DEFAULT 0,
              allow_store INTEGER NOT NULL DEFAULT 0,
              allow_consolidate INTEGER NOT NULL DEFAULT 0,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_workspace_id(metadata: dict[str, Any] | None) -> str | None:
    meta = dict(metadata or {})
    wid = meta.get("workspace_id") or meta.get("workspace")
    if isinstance(wid, dict):
        wid = wid.get("id") or wid.get("workspace_id")
    text = str(wid or "").strip()
    return text or None


def get_workspace_grant(db: Any, workspace_id: str) -> dict[str, Any] | None:
    ensure_workspace_tables(db)
    with db.exclusive() as conn:
        row = conn.execute(
            "SELECT * FROM workspace_grants WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
    if not row:
        return None
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        meta = {}
    return {
        "workspace_id": row["workspace_id"],
        "allow_read": bool(row["allow_read"]),
        "allow_store": bool(row["allow_store"]),
        "allow_consolidate": bool(row["allow_consolidate"]),
        "metadata": meta,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def grant_workspace_read(
    db: Any,
    workspace_id: str,
    *,
    allow_store: bool = True,
    allow_consolidate: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_workspace_tables(db)
    wid = str(workspace_id or "").strip()
    if not wid:
        raise PolicyDeniedError("workspace_id_required", "workspace_id required for grant")
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO workspace_grants(
              workspace_id, allow_read, allow_store, allow_consolidate,
              metadata_json, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
              allow_read=1,
              allow_store=excluded.allow_store,
              allow_consolidate=excluded.allow_consolidate,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                wid,
                1 if allow_store else 0,
                1 if allow_consolidate else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
    return get_workspace_grant(db, wid) or {"workspace_id": wid, "allow_read": True}


def assert_workspace_authorized(
    db: Any,
    metadata: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any] | None:
    """若 metadata 声明了 workspace_id，则必须已登记相应授权。

    无 workspace_id 时保持兼容（不强制）；测试 AC04 通过显式 workspace_id 触发。
    """
    act = str(action or "").strip().lower()
    if act not in _WORKSPACE_ACTIONS:
        act = "ingest"
    wid = extract_workspace_id(metadata)
    if not wid:
        return None
    grant = get_workspace_grant(db, wid)
    if not grant or not grant.get("allow_read"):
        raise PolicyDeniedError(
            "workspace_read_denied",
            f"workspace {wid!r} has no allow_read registration; refuse {act}",
            policy={"workspace_id": wid, "action": act},
        )
    if act in {"capture", "ingest"} and not grant.get("allow_store", True):
        raise PolicyDeniedError(
            "workspace_store_denied",
            f"workspace {wid!r} refuse store for {act}",
            policy={"workspace_id": wid, "action": act},
        )
    if act == "consolidate" and not grant.get("allow_consolidate", True):
        raise PolicyDeniedError(
            "workspace_consolidate_denied",
            f"workspace {wid!r} refuse consolidate",
            policy={"workspace_id": wid, "action": act},
        )
    return grant
