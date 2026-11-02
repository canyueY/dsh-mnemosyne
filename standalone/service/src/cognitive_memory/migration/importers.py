# -*- coding: utf-8 -*-
"""Markdown / SQLite importers（E4.3）：经 Interchange，不直写表。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterator

from cognitive_memory.migration.interchange import InterchangeRecord


_MD_BLOCK = re.compile(
    r"^##\s+(?P<title>.+?)\s*$|^-\s+(?P<bullet>.+)$|^(?P<para>[^\n#].+)$",
    re.MULTILINE,
)


def iter_markdown(path: Path, *, source_system: str = "markdown") -> Iterator[InterchangeRecord]:
    text = Path(path).read_text(encoding="utf-8-sig")
    # 按 ## 标题分块；无标题则整文件一条
    parts = re.split(r"(?m)^##\s+", text)
    if len(parts) <= 1:
        body = text.strip()
        if body:
            yield InterchangeRecord(
                source_system=source_system,
                source_record_id=f"{Path(path).name}:1",
                content={"text": body, "format": "markdown"},
                provenance={"importer": "markdown", "path": str(path)},
                raw_payload={"path": str(path)},
            ).with_checksum()
        return
    # parts[0] 是前言
    idx = 0
    for chunk in parts[1:]:
        idx += 1
        lines = chunk.splitlines()
        title = (lines[0] if lines else f"section-{idx}").strip()
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else title
        if not body:
            continue
        yield InterchangeRecord(
            source_system=source_system,
            source_record_id=f"{Path(path).name}:{idx}",
            content={"text": body, "title": title, "format": "markdown"},
            provenance={"importer": "markdown", "path": str(path), "title": title},
            tags=[title] if title else [],
            raw_payload={"path": str(path), "title": title},
        ).with_checksum()


def iter_sqlite_table(
    path: Path,
    *,
    table: str = "memories",
    id_col: str = "id",
    content_col: str = "content",
    source_system: str = "sqlite",
) -> Iterator[InterchangeRecord]:
    for identifier in (table,id_col,content_col):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",identifier):
            raise ValueError("invalid_sqlite_identifier")
    conn = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if content_col not in cols:
            # 尝试常见别名
            for cand in ("text", "note", "body", "memory"):
                if cand in cols:
                    content_col = cand
                    break
        if content_col not in cols:
            raise ValueError(f"no content column in {table}; have {sorted(cols)}")
        id_expr = id_col if id_col in cols else "rowid"
        rows = conn.execute(
            f"SELECT {id_expr} AS _id, {content_col} AS _content FROM {table}"
        ).fetchall()
        for row in rows:
            content = str(row["_content"] or "").strip()
            if not content:
                continue
            sid = str(row["_id"])
            yield InterchangeRecord(
                source_system=source_system,
                source_record_id=sid,
                content={"text": content, "format": "sqlite"},
                provenance={
                    "importer": "sqlite",
                    "path": str(path),
                    "table": table,
                },
                raw_payload={"table": table, "id": sid},
            ).with_checksum()
    finally:
        conn.close()
