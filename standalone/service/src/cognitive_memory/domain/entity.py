# -*- coding: utf-8 -*-
"""Entity 最小面：统一命名实体，供检索/关联挂靠（非完整图谱）。"""

from __future__ import annotations

import json
from typing import Any

from cognitive_memory.storage.ids import new_id


def ensure_entity_table(db: Any) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entities (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL DEFAULT 'concept',
              name TEXT NOT NULL DEFAULT '',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"
        )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_entity(
    db: Any,
    *,
    entity_id: str | None = None,
    kind: str = "concept",
    name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_entity_table(db)
    eid = str(entity_id or "").strip() or new_id("ent")
    now = _now()
    meta = dict(metadata or {})
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM entities WHERE id=?", (eid,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE entities
                SET kind=?, name=?, metadata_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(kind or "concept"),
                    str(name or ""),
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    eid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO entities(id, kind, name, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    str(kind or "concept"),
                    str(name or ""),
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    now,
                ),
            )
    return {
        "id": eid,
        "kind": str(kind or "concept"),
        "name": str(name or ""),
        "metadata": meta,
        "updated_at": now,
    }


def list_entities(db: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    ensure_entity_table(db)
    lim = max(1, min(200, int(limit or 50)))
    with db.exclusive() as conn:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ?",
            (lim,),
        ).fetchall()
        if conn.in_transaction:
            conn.commit()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        out.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "name": r["name"],
                "metadata": meta,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return out
