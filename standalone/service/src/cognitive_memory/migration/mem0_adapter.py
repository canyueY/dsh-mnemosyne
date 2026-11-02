# -*- coding: utf-8 -*-
"""Mem0 Adapter（E4.4）：经 Interchange，禁止直写 SQLite。"""

from __future__ import annotations

from typing import Any, Iterator

from cognitive_memory.migration.interchange import InterchangeRecord


def iter_mem0_payloads(
    items: list[dict[str, Any]] | None,
    *,
    source_system: str = "mem0",
) -> Iterator[InterchangeRecord]:
    """将 Mem0 风格导出条目转为 Interchange（不触达宿主 mem0_memory 写路径）。"""
    for i, obj in enumerate(items or []):
        if not isinstance(obj, dict):
            continue
        content = obj.get("memory") or obj.get("content") or obj.get("text") or ""
        if not str(content).strip():
            continue
        sid = str(obj.get("id") or obj.get("memory_id") or f"mem0-{i}")
        meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        yield InterchangeRecord(
            source_system=source_system,
            source_record_id=sid,
            content={"text": str(content), "format": "mem0"},
            provenance={"importer": "mem0_adapter", "via": "interchange"},
            time={
                k: obj.get(k)
                for k in ("created_at", "updated_at", "occurred_at")
                if obj.get(k)
            },
            extensions={"mem0_metadata": meta},
            raw_payload=dict(obj),
            tags=[str(t) for t in (obj.get("categories") or obj.get("tags") or [])],
        ).with_checksum()
