# -*- coding: utf-8 -*-
"""Storage Provider 边界：组件/适配器只应经此接口访问持久化，禁止直连 sqlite3。

当前实现：SqliteStorageProvider 包装 Database（仍为单库；抽象为以后换后端留口）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cognitive_memory.storage.db import Database
from cognitive_memory.storage.paths import ensure_layout


@runtime_checkable
class StorageProvider(Protocol):
    """组件可见的最小存储面。"""

    @property
    def data_dir(self) -> Path: ...

    @property
    def db_path(self) -> Path: ...

    def connect(self) -> sqlite3.Connection: ...

    def health(self) -> dict[str, Any]: ...

    def backup_db(self, dest: Path) -> dict[str, Any]: ...


class SqliteStorageProvider:
    """默认 Provider：委托现有 Database，不暴露 raw path 给业务写逻辑以外的组件。"""

    def __init__(self, db: Database, *, data_dir: Path | None = None):
        self._db = db
        root = Path(data_dir).resolve() if data_dir else Path(db.path).resolve().parent.parent
        self._paths = ensure_layout(root)

    @classmethod
    def from_data_dir(cls, data_dir: Path | str, *, token: str = "provider") -> "SqliteStorageProvider":
        _ = token
        paths = ensure_layout(Path(data_dir))
        db = Database(paths["db"])
        db.ensure_schema()  # 创建 memory.db，保证 health.ok
        return cls(db, data_dir=paths["root"])

    @property
    def database(self) -> Database:
        """仅 Node 内核可用；组件代码应避免依赖此属性。"""
        return self._db

    @property
    def data_dir(self) -> Path:
        return self._paths["root"]

    @property
    def db_path(self) -> Path:
        return self._paths["db"]

    def connect(self) -> sqlite3.Connection:
        return self._db.connect()

    def health(self) -> dict[str, Any]:
        p = self.db_path
        return {
            "ok": p.is_file(),
            "provider": "sqlite",
            "db_path": str(p),
            "bytes": p.stat().st_size if p.is_file() else 0,
        }

    def backup_db(self, dest: Path) -> dict[str, Any]:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(self.db_path)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return {"ok": True, "backup": str(dest), "bytes": dest.stat().st_size}


def assert_component_uses_provider(obj: Any) -> StorageProvider:
    if not isinstance(obj, StorageProvider):
        raise TypeError(
            "components must use StorageProvider; direct sqlite3/Database access is forbidden at the boundary"
        )
    return obj
