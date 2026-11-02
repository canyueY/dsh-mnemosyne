# -*- coding: utf-8 -*-
"""SQLite 连接与事务辅助（单连接 + 锁，便于 Windows 下干净关闭）。"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .migrations import apply_migrations, MIGRATIONS


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._initialized = False

    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA temp_store=MEMORY")
            return self._conn

    def ensure_schema(self) -> list[str]:
        with self._lock:
            if self._initialized:
                return []
            conn = self.connect()
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            applied = {r[0] for r in conn.execute("SELECT id FROM schema_migrations")} if "schema_migrations" in tables else set()
            pending = [mid for mid, _ in MIGRATIONS if mid not in applied]
            if pending and tables:
                if conn.in_transaction:
                    raise RuntimeError("schema_upgrade_requires_standalone_transaction")
                backups = self.path.parent / "migration-backups"
                backups.mkdir(exist_ok=True)
                dest = sqlite3.connect(backups / f"before-{pending[0]}-{uuid.uuid4().hex}.db")
                try:
                    conn.backup(dest)
                    if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("pre_migration_backup_invalid")
                finally:
                    dest.close()
            done = apply_migrations(conn)
            self._initialized = True
            return done

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    @contextmanager
    def exclusive(self) -> Iterator[sqlite3.Connection]:
        """持锁访问共享连接（dispatcher 等跨语句读写必须走这里）。"""
        with self._lock:
            yield self.connect()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.connect()
            nested = conn.in_transaction
            savepoint = "sp_" + uuid.uuid4().hex
            conn.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            try:
                yield conn
                if nested:
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    conn.commit()
            except Exception:
                if nested:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    conn.rollback()
                raise
