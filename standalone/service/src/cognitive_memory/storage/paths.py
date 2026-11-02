# -*- coding: utf-8 -*-
"""数据目录：Mnemosyne 代码仓与「Mnemosyne Memory Bank」记忆库分离。"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from pathlib import Path

# 对外显示名 / 默认文件夹名（与代码仓 mnemosyne/ 分离）
MEMORY_BANK_NAME = "Mnemosyne Memory Bank"
# 历史 DSH 共用路径（仅作迁移源，不再作为默认）
LEGACY_DSH_SEGMENT = ("DSH", "cognitive-memory")


def memory_bank_display_name() -> str:
    return MEMORY_BANK_NAME


def legacy_dsh_data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if local:
        return (Path(local).joinpath(*LEGACY_DSH_SEGMENT)).resolve()
    return (Path.home() / ".local" / "share").joinpath(*LEGACY_DSH_SEGMENT).resolve()


def default_data_root() -> Path:
    """权威默认记忆库根目录（≠ 代码仓，≠ DSH\\cognitive-memory）。

    优先级：
    1. ``MNEMOSYNE_MEMORY_BANK`` / ``MNEMOSYNE_DATA_DIR``（新）
    2. ``DSH_MEMORY_DATA_DIR``（旧环境变量兼容）
    3. ``%LOCALAPPDATA%\\Mnemosyne Memory Bank``（或 XDG 等价路径）
    """
    for key in ("MNEMOSYNE_MEMORY_BANK", "MNEMOSYNE_DATA_DIR", "DSH_MEMORY_DATA_DIR"):
        override = os.environ.get(key, "").strip()
        if override:
            return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if local:
        return (Path(local) / MEMORY_BANK_NAME).resolve()
    return (Path.home() / ".local" / "share" / MEMORY_BANK_NAME).resolve()


def ensure_layout(root: Path | None = None) -> dict[str, Path]:
    base = (root or default_data_root()).resolve()
    paths = {
        "root": base,
        "runtime": base / "runtime",
        "data": base / "data",
        "logs": base / "logs",
        "cache": base / "cache",
        "components": base / "components",
        "db": base / "data" / "memory.db",
        "service_json": base / "runtime" / "service.json",
        "service_lock": base / "runtime" / "service.lock",
        "bank_readme": base / "README.txt",
    }
    for key in ("runtime", "data", "logs", "cache", "components"):
        paths[key].mkdir(parents=True, exist_ok=True)
    readme = paths["bank_readme"]
    if not readme.is_file():
        readme.write_text(
            (
                f"{MEMORY_BANK_NAME}\n"
                "====================\n"
                "This directory is the Mnemosyne Memory Bank (sqlite + runtime).\n"
                "It is separate from the Mnemosyne Node source tree (mnemosyne/).\n"
                "Do not point host and an external Node at different banks unless intentional.\n"
                "Legacy path (do not use as default): %LOCALAPPDATA%\\DSH\\cognitive-memory\n"
            ),
            encoding="utf-8",
        )
    return paths


def legacy_db_present(legacy: Path | None = None) -> bool:
    root = (legacy or legacy_dsh_data_root()).resolve()
    return (root / "data" / "memory.db").is_file()


def bank_looks_empty(root: Path | None = None) -> bool:
    base = (root or default_data_root()).resolve()
    db = base / "data" / "memory.db"
    return not db.is_file() or db.stat().st_size < 4096


def migration_status(
    *,
    target: Path | None = None,
    legacy: Path | None = None,
) -> dict:
    """供 doctor / CLI：是否还需从旧 DSH 路径迁到 Memory Bank。"""
    tgt = (target or default_data_root()).resolve()
    src = (legacy or legacy_dsh_data_root()).resolve()
    src_db = src / "data" / "memory.db"
    tgt_db = tgt / "data" / "memory.db"
    return {
        "bank_name": MEMORY_BANK_NAME,
        "target": str(tgt),
        "legacy": str(src),
        "legacy_db_present": src_db.is_file(),
        "legacy_bytes": src_db.stat().st_size if src_db.is_file() else 0,
        "target_db_present": tgt_db.is_file(),
        "target_bytes": tgt_db.stat().st_size if tgt_db.is_file() else 0,
        "recommend_migrate": src_db.is_file()
        and (not tgt_db.is_file() or tgt_db.stat().st_size < 4096),
        "same_path": src == tgt,
    }


def copy_memory_bank(
    source: Path,
    target: Path,
    *,
    overwrite: bool = False,
) -> dict:
    """把旧 data_dir 整树复制到新 Memory Bank（不删除源）。"""
    src = source.expanduser().resolve()
    dst = target.expanduser().resolve()
    if src == dst:
        return {"ok": False, "error": "source_equals_target", "source": str(src), "target": str(dst)}
    if src.is_relative_to(dst) or dst.is_relative_to(src):
        return {"ok":False,"error":"overlapping_source_and_target"}
    from cognitive_memory.onboarding import node_running
    if node_running(dst):
        return {"ok":False,"error":"target_node_running"}
    if not (src / "data" / "memory.db").is_file() and not (src / "memory.db").is_file():
        return {"ok": False, "error": "source_db_missing", "source": str(src)}
    source_db=src/"data/memory.db" if (src/"data/memory.db").is_file() else src/"memory.db"
    try:
        check=sqlite3.connect(source_db.as_uri()+"?mode=ro",uri=True)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                return {"ok":False,"error":"source_db_invalid"}
        finally:
            check.close()
    except sqlite3.DatabaseError:
        return {"ok":False,"error":"source_db_invalid"}
    if dst.exists() and any(dst.iterdir()) and not overwrite:
        # 允许目标仅有空 layout / README
        meaningful = [
            p
            for p in dst.rglob("*")
            if p.is_file() and p.name not in {"README.txt"} and p.suffix != ".lock"
        ]
        if meaningful and not overwrite:
            return {
                "ok": False,
                "error": "target_not_empty",
                "target": str(dst),
                "hint": "pass overwrite=True or choose empty target",
            }
    dst.mkdir(parents=True, exist_ok=True)
    target_db = dst / "data" / "memory.db"
    if target_db.is_file() and overwrite:
        backup_dir = dst / "backups"
        backup_dir.mkdir(exist_ok=True)
        previous = sqlite3.connect(target_db.as_uri()+"?mode=ro",uri=True)
        backup = sqlite3.connect(backup_dir / f"before-bank-copy-{uuid.uuid4().hex}.db")
        try:
            previous.backup(backup)
        finally:
            previous.close()
            backup.close()
    copied: list[str] = []
    for name in ("data", "logs", "components"):
        s = src / name
        if not s.exists():
            continue
        d = dst / name
        shutil.copytree(s, d, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("memory.db","memory.db-wal","memory.db-shm"))
        copied.append(name)
    # 根级零散文件
    for item in src.iterdir():
        if item.name in {"data", "runtime", "logs", "cache", "components"}:
            continue
        if item.is_file() and item.name not in {"memory.db","memory.db-wal","memory.db-shm"}:
            dest_f = dst / item.name
            if dest_f.exists() and not overwrite:
                continue
            shutil.copy2(item, dest_f)
            copied.append(item.name)
    ensure_layout(dst)
    source_db = src / "data" / "memory.db"
    if not source_db.is_file():
        source_db = src / "memory.db"
    original = sqlite3.connect(source_db.as_uri()+"?mode=ro",uri=True)
    snapshot = sqlite3.connect(target_db)
    try:
        original.backup(snapshot)
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return {"ok":False,"error":"copied_database_integrity_failed"}
    finally:
        original.close()
        snapshot.close()
    # 标记源为已迁出归档提示
    marker = src / "MIGRATED_TO_MNEMOSYNE_MEMORY_BANK.txt"
    try:
        marker.write_text(
            (
                f"This legacy DSH cognitive-memory directory was copied to:\n{dst}\n"
                f"Bank name: {MEMORY_BANK_NAME}\n"
                "Mnemosyne defaults no longer use this path. Safe to keep as archive.\n"
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    tgt_db = dst / "data" / "memory.db"
    return {
        "ok": True,
        "bank_name": MEMORY_BANK_NAME,
        "source": str(src),
        "target": str(dst),
        "copied": copied,
        "target_db_bytes": tgt_db.stat().st_size if tgt_db.is_file() else 0,
    }
