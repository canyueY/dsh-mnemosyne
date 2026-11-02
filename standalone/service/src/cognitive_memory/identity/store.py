# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.identity.models import Principal, PrincipalKind, PrincipalUpsert
from cognitive_memory.storage.ids import new_id


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_principal(row: Any) -> Principal:
    meta_raw = row["metadata_json"] if "metadata_json" in row.keys() else "{}"
    try:
        meta = json.loads(meta_raw or "{}")
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return Principal(
        id=row["id"],
        kind=PrincipalKind(row["kind"]),
        display_name=row["display_name"] or "",
        external_ref=row["external_ref"],
        metadata=meta,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def ensure_default_principals(db: Any, *, node_id: str = "local-node") -> list[Principal]:
    """幂等写入本 Node 默认主体：node / user / agent(dsh)。"""
    defaults = [
        PrincipalUpsert(
            id=node_id,
            kind=PrincipalKind.NODE,
            display_name="Local Mnemosyne Node",
            metadata={"role": "self"},
        ),
        PrincipalUpsert(
            id="user:local",
            kind=PrincipalKind.USER,
            display_name="Local User",
            metadata={"role": "owner"},
        ),
        PrincipalUpsert(
            id="agent:dsh",
            kind=PrincipalKind.AGENT,
            display_name="DSH Host Adapter",
            metadata={"role": "adapter", "optional": True},
        ),
        PrincipalUpsert(
            id="agent:standalone",
            kind=PrincipalKind.AGENT,
            display_name="Standalone Client",
            metadata={"role": "direct"},
        ),
    ]
    out: list[Principal] = []
    for item in defaults:
        out.append(upsert_principal(db, item))
    return out


def upsert_principal(db: Any, body: PrincipalUpsert) -> Principal:
    now = _now()
    pid = (body.id or "").strip() or new_id(body.kind.value)
    meta_json = json.dumps(body.metadata or {}, ensure_ascii=False)
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM identity_principals WHERE id=?",
            (pid,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE identity_principals
                SET kind=?, display_name=?, external_ref=?, metadata_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    body.kind.value,
                    body.display_name or "",
                    body.external_ref,
                    meta_json,
                    now,
                    pid,
                ),
            )
            created = existing["created_at"]
        else:
            created = now
            conn.execute(
                """
                INSERT INTO identity_principals(
                  id, kind, display_name, external_ref, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    body.kind.value,
                    body.display_name or "",
                    body.external_ref,
                    meta_json,
                    created,
                    now,
                ),
            )
        row = conn.execute(
            "SELECT * FROM identity_principals WHERE id=?",
            (pid,),
        ).fetchone()
    return _row_to_principal(row)


def get_principal(db: Any, principal_id: str) -> Principal | None:
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM identity_principals WHERE id=?",
        (principal_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_principal(row)


def list_principals(
    db: Any,
    *,
    kind: PrincipalKind | str | None = None,
    limit: int = 200,
) -> list[Principal]:
    conn = db.connect()
    lim = max(1, min(int(limit or 200), 1000))
    if kind:
        k = kind.value if isinstance(kind, PrincipalKind) else str(kind)
        rows = conn.execute(
            """
            SELECT * FROM identity_principals
            WHERE kind=?
            ORDER BY kind, id
            LIMIT ?
            """,
            (k, lim),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM identity_principals
            ORDER BY kind, id
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [_row_to_principal(r) for r in rows]
