# -*- coding: utf-8 -*-
"""Stratified read-only Memory sampler for P0.5 (never writes)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


STRATA = (
    "S1_simple_fact",
    "S2_preference",
    "S3_temporal",
    "S4_conditional",
    "S5_negation",
    "S6_synonym_candidate",
    "S7_conflict_candidate",
    "S8_incomplete_provenance",
    "S9_security_sensitive",
    "S10_unstructured",
)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_meta(raw: str | None) -> dict[str, Any]:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _row_to_sample(row: sqlite3.Row, *, stratum: str, case_id: str) -> dict[str, Any]:
    meta = _parse_meta(row["metadata_json"])
    return {
        "case_id": case_id,
        "stratum": stratum,
        "source_memory_id": row["id"],
        "source_text": row["content"] or "",
        "source_kind": row["type"],
        "source_status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "bank_id": meta.get("bank_id") or row["bank_id"] if "bank_id" in row.keys() else meta.get("bank_id"),
        "confidence": row["confidence"],
        "metadata": meta,
        "evidence_count": int(row["evidence_count"] or 0) if "evidence_count" in row.keys() else 0,
        "validation_states": row["validation_states"] if "validation_states" in row.keys() else "",
    }


def _fetch(
    conn: sqlite3.Connection,
    where: str,
    *,
    params: tuple[Any, ...] = (),
    limit: int = 10,
    order: str = "m.created_at DESC",
) -> list[sqlite3.Row]:
    # Detect bank_id column
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    bank_expr = "m.bank_id" if "bank_id" in cols else "NULL"
    sql = f"""
    SELECT
      m.id, m.type, m.status, m.content, m.created_at, m.updated_at,
      m.confidence, m.metadata_json, {bank_expr} AS bank_id,
      (SELECT COUNT(*) FROM memory_evidence me WHERE me.memory_id = m.id) AS evidence_count,
      (SELECT GROUP_CONCAT(DISTINCT me.validation_state)
         FROM memory_evidence me WHERE me.memory_id = m.id) AS validation_states
    FROM memories m
    WHERE {where}
    ORDER BY {order}
    LIMIT ?
    """
    return list(conn.execute(sql, (*params, int(limit))).fetchall())


def sample_memories(
    db_path: str | Path,
    *,
    per_stratum: int = 10,
    total_target: int = 100,
) -> list[dict[str, Any]]:
    """Return ~100 stratified samples. Read-only."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"memory_db_not_found:{path}")

    conn = _connect_ro(path)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(stratum: str, rows: list[sqlite3.Row]) -> None:
        for row in rows:
            mid = row["id"]
            if mid in seen:
                continue
            seen.add(mid)
            out.append(
                _row_to_sample(
                    row,
                    stratum=stratum,
                    case_id=f"{stratum}:{mid}",
                )
            )

    try:
        # S1 simple short facts
        add(
            "S1_simple_fact",
            _fetch(
                conn,
                "m.type IN ('semantic','working') AND length(m.content) BETWEEN 4 AND 40 "
                "AND m.content NOT LIKE '%喜欢%' AND m.content NOT LIKE '%如果%'",
                limit=per_stratum,
            ),
        )
        # S2 preference
        add(
            "S2_preference",
            _fetch(
                conn,
                "(m.content LIKE '%喜欢%' OR m.content LIKE '%讨厌%' OR m.metadata_json LIKE '%preference%')",
                limit=per_stratum,
            ),
        )
        # S3 temporal
        add(
            "S3_temporal",
            _fetch(
                conn,
                "(m.content LIKE '%今天%' OR m.content LIKE '%昨天%' OR m.content GLOB '*20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]*')",
                limit=per_stratum,
            ),
        )
        # S4 conditional
        add(
            "S4_conditional",
            _fetch(
                conn,
                "(m.content LIKE '%如果%' OR m.content LIKE '%当%就%' OR m.content LIKE '%若%')",
                limit=per_stratum,
            ),
        )
        # S5 negation
        add(
            "S5_negation",
            _fetch(
                conn,
                "(m.content LIKE '%不是%' OR m.content LIKE '%没有%' OR m.content LIKE '%不喜欢%' OR m.content LIKE '%并非%')",
                limit=per_stratum,
            ),
        )
        # S6 synonym / duplicate-ish titles
        add(
            "S6_synonym_candidate",
            _fetch(
                conn,
                "m.type='semantic' AND length(m.content) BETWEEN 8 AND 60",
                limit=per_stratum,
                order="m.content ASC",
            ),
        )
        # S7 conflict markers
        add(
            "S7_conflict_candidate",
            _fetch(
                conn,
                "(m.content LIKE '%但是%' OR m.content LIKE '%然而%' OR m.content LIKE '%其实%' "
                "OR m.status='invalidated' OR m.metadata_json LIKE '%conflict%')",
                limit=per_stratum,
            ),
        )
        # S8 no evidence links
        add(
            "S8_incomplete_provenance",
            _fetch(
                conn,
                "NOT EXISTS (SELECT 1 FROM memory_evidence me WHERE me.memory_id=m.id)",
                limit=per_stratum,
            ),
        )
        # S9 security / bank / policy metadata
        add(
            "S9_security_sensitive",
            _fetch(
                conn,
                "(m.metadata_json LIKE '%bank_id%' OR m.metadata_json LIKE '%policy%' "
                "OR m.metadata_json LIKE '%classification%' OR m.metadata_json LIKE '%personal%')",
                limit=per_stratum,
            ),
        )
        # S10 long unstructured
        add(
            "S10_unstructured",
            _fetch(
                conn,
                "length(m.content) > 120",
                limit=per_stratum,
            ),
        )

        # Fill remaining with episodic candidates
        if len(out) < total_target:
            need = total_target - len(out)
            add(
                "S1_simple_fact",
                _fetch(
                    conn,
                    "m.type='episodic' AND m.status='candidate'",
                    limit=need,
                ),
            )
    finally:
        conn.close()

    return out[:total_target]
