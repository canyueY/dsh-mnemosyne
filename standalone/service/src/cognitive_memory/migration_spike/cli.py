# -*- coding: utf-8 -*-
"""CLI: python -m cognitive_memory.migration_spike.cli --db ... --out ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cognitive_memory.migration_spike.runner import run_spike


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="P0.5 read-only Memory→Claim migration spike")
    p.add_argument(
        "--db",
        default="",
        help="Path to memory.db (read-only). Default: %%LOCALAPPDATA%%/Mnemosyne Memory Bank/data/memory.db",
    )
    p.add_argument(
        "--out",
        default="",
        help="Output directory for fixtures (default: service/docs/2.0/fixtures/migration_samples)",
    )
    p.add_argument("--per-stratum", type=int, default=10)
    p.add_argument("--total", type=int, default=100)
    args = p.parse_args(argv)

    db = Path(args.db) if args.db else Path.home().joinpath(
        # Windows LOCALAPPDATA preferred
    )
    if not args.db:
        import os

        local = os.environ.get("LOCALAPPDATA") or ""
        candidate = Path(local) / "Mnemosyne Memory Bank" / "data" / "memory.db"
        db = candidate
    else:
        db = Path(args.db)

    if args.out:
        out = Path(args.out)
    else:
        # service/docs/2.0/fixtures/migration_samples relative to this package's service root
        out = Path(__file__).resolve().parents[3] / "docs" / "2.0" / "fixtures" / "migration_samples"

    if not db.is_file():
        print(json.dumps({"ok": False, "error": "db_not_found", "db": str(db)}, ensure_ascii=False))
        return 2

    report = run_spike(db, out, per_stratum=int(args.per_stratum), total_target=int(args.total))
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "sample_count": report.sample_count,
                "stats": report.stats,
                "generated_at": report.generated_at,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
