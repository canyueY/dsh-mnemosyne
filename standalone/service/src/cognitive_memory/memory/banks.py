# -*- coding: utf-8 -*-
"""Memory Bank 逻辑空间（E1.5）：Personal 默认；Shared/Public 不得污染 Personal。"""

from __future__ import annotations

from enum import Enum
from typing import Any


class MemoryBankId(str, Enum):
    PERSONAL = "personal"
    PROJECTS = "projects"
    KNOWLEDGE = "knowledge"
    SHARED = "shared"
    PUBLIC = "public"
    SYSTEM = "system"


DEFAULT_BANK = MemoryBankId.PERSONAL.value

# 默认检索范围：不含 shared/public（硬隔离）
DEFAULT_SEARCH_BANKS = (MemoryBankId.PERSONAL.value,)

CROSS_POLLUTION_BANKS = frozenset(
    {MemoryBankId.SHARED.value, MemoryBankId.PUBLIC.value}
)


BANK_CATALOG: dict[str, dict[str, Any]] = {
    MemoryBankId.PERSONAL.value: {
        "id": "personal",
        "name": "Personal",
        "description": "个人长期记忆（默认权威）",
        "default_authority": True,
    },
    MemoryBankId.PROJECTS.value: {
        "id": "projects",
        "name": "Projects",
        "description": "项目上下文",
        "default_authority": False,
    },
    MemoryBankId.KNOWLEDGE.value: {
        "id": "knowledge",
        "name": "Knowledge",
        "description": "结构化知识",
        "default_authority": False,
    },
    MemoryBankId.SHARED.value: {
        "id": "shared",
        "name": "Shared",
        "description": "显式共享；不得自动进入 Personal 检索",
        "default_authority": False,
        "no_auto_personal": True,
    },
    MemoryBankId.PUBLIC.value: {
        "id": "public",
        "name": "Public",
        "description": "公开可分发；不得自动进入 Personal 检索",
        "default_authority": False,
        "no_auto_personal": True,
    },
    MemoryBankId.SYSTEM.value: {
        "id": "system",
        "name": "System",
        "description": "系统/运维",
        "default_authority": False,
    },
}


def normalize_bank_id(raw: str | None) -> str:
    v = (raw or "").strip().lower() or DEFAULT_BANK
    if v not in BANK_CATALOG:
        return DEFAULT_BANK
    return v


def resolve_search_banks(requested: list[str] | None) -> list[str]:
    """None/空 → 仅 personal。显式传入才可包含 shared/public。"""
    if not requested:
        return list(DEFAULT_SEARCH_BANKS)
    out: list[str] = []
    for b in requested:
        nb = normalize_bank_id(b)
        if nb not in out:
            out.append(nb)
    return out or list(DEFAULT_SEARCH_BANKS)


def ensure_default_banks(db: Any) -> list[dict[str, Any]]:
    from datetime import datetime, timezone
    import json

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[dict[str, Any]] = []
    with db.transaction() as conn:
        for bid, meta in BANK_CATALOG.items():
            conn.execute(
                """
                INSERT INTO memory_banks(id, name, description, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  description=excluded.description,
                  updated_at=excluded.updated_at
                """,
                (
                    bid,
                    meta["name"],
                    meta.get("description") or "",
                    json.dumps(
                        {k: v for k, v in meta.items() if k not in ("id", "name", "description")},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            items.append(dict(meta))
    return items


def list_banks(db: Any) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, name, description, metadata_json FROM memory_banks ORDER BY id"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"] or "",
        }
        try:
            import json

            meta = json.loads(r["metadata_json"] or "{}")
            if isinstance(meta, dict):
                item.update(meta)
        except Exception:
            pass
        out.append(item)
    return out
