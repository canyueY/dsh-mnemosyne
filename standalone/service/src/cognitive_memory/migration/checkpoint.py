# -*- coding: utf-8 -*-
"""迁移断点：按 source_id 记录已处理项，支持续跑。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CheckpointStore:
    path: Path
    run_id: str = ""
    completed: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed: dict[str, dict[str, Any]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> CheckpointStore:
        if not path.is_file():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            run_id=str(raw.get("run_id") or ""),
            completed=dict(raw.get("completed") or {}),
            failed=dict(raw.get("failed") or {}),
            meta=dict(raw.get("meta") or {}),
        )

    def is_done(self, source_id: str) -> bool:
        return source_id in self.completed

    def mark_done(
        self,
        source_id: str,
        *,
        observation_id: str | None = None,
        memory_id: str | None = None,
        skipped: bool = False,
    ) -> None:
        self.completed[source_id] = {
            "at": _now(),
            "observation_id": observation_id,
            "memory_id": memory_id,
            "skipped": skipped,
        }
        self.failed.pop(source_id, None)

    def mark_failed(self, source_id: str, error: str) -> None:
        self.failed[source_id] = {"at": _now(), "error": error}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "updated_at": _now(),
            "completed": self.completed,
            "failed": self.failed,
            "meta": self.meta,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
