# -*- coding: utf-8 -*-
"""迁移源：JSONL / A-MEM 桥接。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

from cognitive_memory.migration.models import SourceRecord


def iter_jsonl(path: Path) -> Iterator[SourceRecord]:
    # utf-8-sig：兼容 Windows 记事本 / PowerShell Set-Content 写出的 BOM
    text = path.read_text(encoding="utf-8-sig")
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"JSONL row must be object at {path}:{line_no}")
        yield source_record_from_dict(obj, fallback_id=f"{path.name}:{line_no}")


def source_record_from_dict(obj: dict, *, fallback_id: str = "") -> SourceRecord:
    content = obj.get("content")
    if content is None:
        content = obj.get("text") or obj.get("note") or ""
    tags = obj.get("tags") or obj.get("keywords") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        tags = []
    sid = str(obj.get("id") or obj.get("amem_id") or obj.get("source_id") or fallback_id or "")
    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    return SourceRecord(
        source_id=sid,
        content=str(content or ""),
        tags=[str(t) for t in tags],
        metadata=dict(meta),
        occurred_at=obj.get("occurred_at") or obj.get("created_at") or obj.get("timestamp"),
    )


def iter_amem_bridge(project_root: Path) -> Iterator[SourceRecord]:
    """经桌宠 `Murasame.a_mem_bridge` 读取运行中/本地 A-MEM。"""
    root = project_root.resolve()
    sys.path.insert(0, str(root))
    from Murasame.a_mem_bridge import get_a_mem_engine  # type: ignore
    from Murasame.utils import get_config  # type: ignore

    engine = get_a_mem_engine(get_config())
    if engine is None:
        raise RuntimeError("A-MEM engine unavailable; pass --jsonl export")
    memories = getattr(engine, "memories", None) or {}
    for mid, note in memories.items():
        if isinstance(note, dict):
            content = note.get("content") or ""
            tags = note.get("tags") or []
            meta = {k: v for k, v in note.items() if k not in ("content", "tags", "id")}
            occurred = note.get("occurred_at") or note.get("created_at")
        else:
            content = getattr(note, "content", None) or ""
            tags = getattr(note, "tags", None) or []
            meta = {}
            occurred = getattr(note, "created_at", None) or getattr(note, "timestamp", None)
        yield SourceRecord(
            source_id=str(mid),
            content=str(content or ""),
            tags=list(tags or []),
            metadata=meta if isinstance(meta, dict) else {},
            occurred_at=str(occurred) if occurred else None,
        )


def load_source(
    *,
    jsonl: str | Path | None = None,
    project_root: str | Path | None = None,
) -> list[SourceRecord]:
    if jsonl:
        return list(iter_jsonl(Path(jsonl)))
    if not project_root:
        raise ValueError("either jsonl or project_root is required")
    return list(iter_amem_bridge(Path(project_root)))
