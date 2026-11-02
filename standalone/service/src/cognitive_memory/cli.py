# -*- coding: utf-8 -*-
"""运维 CLI：备份 / 恢复 / 迁移 / 健康检查。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def cmd_backup(args: argparse.Namespace) -> int:
    """备份 memory.db + node 配置/runtime 元数据为目录包（非仅 sqlite）。"""
    import sqlite3
    from cognitive_memory.storage.paths import ensure_layout

    paths = ensure_layout(Path(args.data_dir).resolve() if args.data_dir else None)
    db = paths["db"]
    if not db.is_file():
        print(f"no db at {db}", file=sys.stderr)
        return 1
    out_root = Path(args.output or (paths["root"] / "backups")).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    pack = out_root / f"mnemosyne-backup-{stamp}"
    pack.mkdir(parents=True, exist_ok=True)
    dest_db = pack / "memory.db"
    src = sqlite3.connect(db)
    try:
        dst = sqlite3.connect(dest_db)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    copied: list[str] = []
    for rel in (
        "node.config.json",
        "node.config.yaml",
        "runtime/service.json",
    ):
        src_p = paths["root"] / rel
        if src_p.is_file():
            target = pack / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_p, target)
            copied.append(rel)

    # legacy single-file also for drill compat
    legacy = out_root / f"memory-{stamp}.db"
    shutil.copy2(dest_db, legacy)

    meta = {
        "created_at": stamp,
        "source_data_dir": str(paths["root"]),
        "source_db": str(db),
        "package": str(pack),
        "backup_db": str(dest_db),
        "legacy_db": str(legacy),
        "bytes": dest_db.stat().st_size,
        "copied_files": copied,
        "kind": "package",
    }
    (pack / "manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    legacy.with_suffix(".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False))
    return 0



def _node_running(data_root: Path) -> dict:
    lock = data_root / "runtime" / "service.lock"
    meta = data_root / "runtime" / "service.json"
    pid = None
    if meta.is_file():
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
            pid = payload.get("pid")
        except Exception:
            pid = None
    if pid:
        try:
            import os
            os.kill(int(pid), 0)
            return {"running": True, "pid": int(pid), "lock": str(lock)}
        except Exception:
            pass
    if lock.is_file():
        # lock exists but pid dead → treat as stale unless --force
        return {"running": False, "stale_lock": True, "lock": str(lock)}
    return {"running": False}


def cmd_restore(args: argparse.Namespace) -> int:
    from cognitive_memory.storage.paths import ensure_layout

    src = Path(args.backup).resolve()
    if not src.exists():
        print(f"backup not found: {src}", file=sys.stderr)
        return 1
    paths = ensure_layout(Path(args.data_dir).resolve() if args.data_dir else None)
    running = _node_running(paths["root"])
    if running.get("running") and not getattr(args, "force_running", False):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "node_running",
                    "detail": "refuse restore onto live node; stop first or pass --force-running",
                    **running,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3

    # package dir or single db
    if src.is_dir():
        db_src = src / "memory.db"
        if not db_src.is_file():
            print(f"package missing memory.db: {src}", file=sys.stderr)
            return 1
        # restore config files if present
        for rel in ("node.config.json", "node.config.yaml", "runtime/service.json"):
            cand = src / rel
            if cand.is_file():
                dest = paths["root"] / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cand, dest)
    else:
        db_src = src

    db = paths["db"]
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.is_file() and not args.force:
        bak = db.with_suffix(f".pre-restore-{_now_stamp()}.db")
        shutil.copy2(db, bak)
        print(f"existing db copied to {bak}")
    shutil.copy2(db_src, db)
    print(
        json.dumps(
            {
                "ok": True,
                "restored_to": str(db),
                "from": str(db_src),
                "package": str(src) if src.is_dir() else None,
                "node_was_running": bool(running.get("running")),
            },
            ensure_ascii=False,
        )
    )
    return 0



def cmd_health(args: argparse.Namespace) -> int:
    from cognitive_memory.bootstrap import build_context

    ctx = build_context(data_dir=args.data_dir or None, token=args.token or "cli-health")
    print(json.dumps(ctx.service.health(), ensure_ascii=False, indent=2))
    ctx.db.close()
    return 0


def cmd_migrate_amem(args: argparse.Namespace) -> int:
    """A-MEM / JSONL → 认知记忆：走 MemoryMigrationEngine。"""
    from cognitive_memory.bootstrap import build_context
    from cognitive_memory.migration import MemoryMigrationEngine, MigrationConfig
    from cognitive_memory.migration.sources import load_source
    from cognitive_memory.storage.paths import ensure_layout

    try:
        records = load_source(
            jsonl=args.jsonl or None,
            project_root=(args.project_root or Path.cwd()) if not args.jsonl else None,
        )
    except Exception as exc:
        print(f"cannot load source: {exc}", file=sys.stderr)
        return 2

    checkpoint_path = Path(args.checkpoint).resolve() if args.checkpoint else None
    if checkpoint_path is None and not args.dry_run:
        paths = ensure_layout(Path(args.data_dir).resolve() if args.data_dir else None)
        checkpoint_path = paths["root"] / "migration" / "amem.checkpoint.json"

    cfg = MigrationConfig(
        dry_run=bool(args.dry_run),
        resume=not bool(args.no_resume),
        limit=int(args.limit) if args.limit else None,
        checkpoint_path=checkpoint_path,
        producer="migrate.amem",
        stop_on_error=bool(args.stop_on_error),
    )

    if args.dry_run and args.no_service:
        from cognitive_memory.migration.sink import RecordingSink

        engine = MemoryMigrationEngine(RecordingSink(), config=cfg)
        report = engine.run(records)
        print(json.dumps({"ok": report.failed == 0, **report.to_dict()}, ensure_ascii=False))
        return 0 if report.failed == 0 else 1

    ctx = build_context(data_dir=args.data_dir or None, token=args.token or "cli-migrate")
    try:
        engine = MemoryMigrationEngine.from_service(ctx.service, config=cfg)
        report = engine.run(records)
    finally:
        ctx.db.close()

    payload = {"ok": report.failed == 0, **report.to_dict()}
    if args.report:
        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["report_path"] = str(report_path)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if report.failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cognitive-memory")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--token", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bak = sub.add_parser("backup", help="备份 memory.db")
    p_bak.add_argument("-o", "--output", default="")
    p_bak.set_defaults(func=cmd_backup)

    p_res = sub.add_parser("restore", help="从备份恢复（Node 运行中默认拒绝）")
    p_res.add_argument("backup")
    p_res.add_argument("--force", action="store_true")
    p_res.add_argument(
        "--force-running",
        action="store_true",
        help="允许在 Node 运行中覆盖（危险）",
    )
    p_res.set_defaults(func=cmd_restore)

    p_h = sub.add_parser("health", help="本地打开库并打印 health")
    p_h.set_defaults(func=cmd_health)

    p_m = sub.add_parser("migrate-amem", help="迁移引擎：从 A-MEM 或 JSONL 导入")
    p_m.add_argument("--jsonl", default="")
    p_m.add_argument("--project-root", default="")
    p_m.add_argument("--dry-run", action="store_true")
    p_m.add_argument("--no-resume", action="store_true", help="忽略 checkpoint，强制重跑")
    p_m.add_argument("--checkpoint", default="", help="断点文件路径（默认 data/migration/amem.checkpoint.json）")
    p_m.add_argument("--limit", type=int, default=0, help="最多处理 N 条（0=不限）")
    p_m.add_argument("--report", default="", help="写出 MigrationReport JSON")
    p_m.add_argument("--stop-on-error", action="store_true")
    p_m.add_argument(
        "--no-service",
        action="store_true",
        help="仅 dry-run 时可用：不打开数据库，只跑映射/计数",
    )
    p_m.set_defaults(func=cmd_migrate_amem)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
