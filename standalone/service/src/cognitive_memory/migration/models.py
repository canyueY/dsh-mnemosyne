# -*- coding: utf-8 -*-
"""迁移引擎数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceRecord:
    """上游一条记忆（通常来自 A-MEM / JSONL）。"""

    source_id: str
    content: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    occurred_at: str | None = None


@dataclass
class MappedRecord:
    """映射后的观察 + 候选记忆载荷（尚未写入）。"""

    source_id: str
    content: str
    memory_type: str
    memory_status: str
    tags: list[str]
    observation_idempotency_key: str
    memory_idempotency_key: str
    observation_metadata: dict[str, Any]
    memory_metadata: dict[str, Any]
    occurred_at: str | None = None
    skip_reason: str | None = None


@dataclass
class WriteResult:
    source_id: str
    ok: bool
    skipped: bool = False
    observation_id: str | None = None
    memory_id: str | None = None
    error: str | None = None
    dry_run: bool = False
    created: bool = True
