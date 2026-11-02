# -*- coding: utf-8 -*-
"""P2 read-only shadow replay: Memory → Claim into a separate shadow DB.

Never writes to the source memory.db. Never marks Verified. Never backfills prod Claims.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ShadowReplayReport:
    ok: bool
    source_db: str
    shadow_db: str
    scanned: int = 0
    proposed: int = 0
    same_as: int = 0
    unresolved: int = 0
    atoms: int = 0
    errors: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _open_source_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def sample_memory_rows(
    source: sqlite3.Connection, *, limit: int = 50
) -> list[dict[str, Any]]:
    rows = source.execute(
        """
        SELECT id, content, status, bank_id, created_at
        FROM memories
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "content": r["content"] or "",
                "status": r["status"],
                "bank_id": r["bank_id"] if "bank_id" in r.keys() else "personal",
                "created_at": r["created_at"],
            }
        )
    return out


def run_shadow_replay(
    source_db: Path,
    shadow_db: Path,
    *,
    limit: int = 50,
) -> ShadowReplayReport:
    source_db = Path(source_db)
    shadow_db = Path(shadow_db)
    report = ShadowReplayReport(
        ok=True, source_db=str(source_db), shadow_db=str(shadow_db)
    )
    if not source_db.is_file():
        report.ok = False
        report.errors.append("source_db_not_found")
        return report

    shadow_db.parent.mkdir(parents=True, exist_ok=True)
    src = _open_source_readonly(source_db)
    try:
        try:
            rows = sample_memory_rows(src, limit=limit)
        except sqlite3.Error as exc:
            report.ok = False
            report.errors.append(f"sample_failed:{exc}")
            return report
    finally:
        src.close()

    shadow = Database(shadow_db)
    store = ClaimStore(shadow)
    try:
        for row in rows:
            report.scanned += 1
            text = str(row.get("content") or "").strip()
            if not text:
                continue
            try:
                result = store.propose_from_text(
                    text,
                    bank_id=str(row.get("bank_id") or "personal"),
                    source_memory_id=str(row.get("id") or "") or None,
                    persist_unresolved=True,
                )
            except Exception as exc:  # noqa: BLE001 — collect per-row
                report.errors.append(f"{row.get('id')}:{exc}")
                continue
            if result.get("same_as"):
                report.same_as += 1
            elif result.get("persisted"):
                report.proposed += 1
            if result.get("unresolved"):
                report.unresolved += 1
            else:
                root = ((result.get("ast") or {}).get("root") or {})
                if root.get("kind") == "atom":
                    report.atoms += 1
                elif root.get("kind") == "unresolved":
                    report.unresolved += 1
    finally:
        shadow.close()

    # Write report sidecar next to shadow db
    report_path = shadow_db.with_suffix(".shadow_report.json")
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="P2 read-only Memory→Claim shadow replay")
    p.add_argument("--source-db", required=True, help="Source memory.db (read-only)")
    p.add_argument(
        "--shadow-db",
        required=True,
        help="Target shadow sqlite (Claims written only here)",
    )
    p.add_argument("--limit", type=int, default=50)
    args = p.parse_args(argv)
    report = run_shadow_replay(
        Path(args.source_db), Path(args.shadow_db), limit=int(args.limit)
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
