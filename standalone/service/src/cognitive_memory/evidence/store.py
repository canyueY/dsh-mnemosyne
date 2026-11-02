# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.evidence.models import EvidenceAttachIn, EvidenceRecord


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _row_to_record(row: Any) -> EvidenceRecord:
    keys = set(row.keys())
    meta = _loads(row["metadata_json"], {}) if "metadata_json" in keys else {}
    if not isinstance(meta, dict):
        meta = {}
    conf = row["confidence"] if "confidence" in keys else None
    kind = row["evidence_kind"] if "evidence_kind" in keys else "observation"
    vstate = row["validation_state"] if "validation_state" in keys else "unverified"
    producer = row["producer"] if "producer" in keys else ""
    provenance = {
        "observation_id": row["observation_id"],
        "relation": row["relation"],
        "evidence_kind": kind or "observation",
        "validation_state": vstate or "unverified",
        "producer": producer or "",
        "confidence": conf,
        "created_at": row["created_at"],
    }
    return EvidenceRecord(
        memory_id=row["memory_id"],
        observation_id=row["observation_id"],
        relation=row["relation"],
        note=row["note"] or "",
        created_at=row["created_at"],
        confidence=conf,
        evidence_kind=kind or "observation",
        validation_state=vstate or "unverified",
        producer=producer or "",
        metadata=meta,
        provenance=provenance,
    )


def attach_evidence(db: Any, body: EvidenceAttachIn) -> EvidenceRecord:
    now = _now()
    meta_json = json.dumps(body.metadata or {}, ensure_ascii=False)
    with db.transaction() as conn:
        mem = conn.execute(
            "SELECT id FROM memories WHERE id=?", (body.memory_id,)
        ).fetchone()
        if not mem:
            raise KeyError(f"memory not found: {body.memory_id}")
        obs = conn.execute(
            "SELECT id FROM observations WHERE id=?", (body.observation_id,)
        ).fetchone()
        if not obs:
            raise KeyError(f"observation not found: {body.observation_id}")
        conn.execute(
            """
            INSERT INTO memory_evidence(
              memory_id, observation_id, relation, note, created_at,
              confidence, evidence_kind, validation_state, producer, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, observation_id, relation) DO UPDATE SET
              note=excluded.note,
              confidence=excluded.confidence,
              evidence_kind=excluded.evidence_kind,
              validation_state=excluded.validation_state,
              producer=excluded.producer,
              metadata_json=excluded.metadata_json
            """,
            (
                body.memory_id,
                body.observation_id,
                body.relation,
                body.note or "",
                now,
                body.confidence,
                body.evidence_kind.value,
                body.validation_state.value,
                body.producer or "",
                meta_json,
            ),
        )
        # 同步 source_ids（若缺失）
        row = conn.execute(
            "SELECT source_ids_json FROM memories WHERE id=?", (body.memory_id,)
        ).fetchone()
        sources = _loads(row["source_ids_json"], []) if row else []
        if not isinstance(sources, list):
            sources = []
        if body.observation_id not in sources:
            sources.append(body.observation_id)
            conn.execute(
                "UPDATE memories SET source_ids_json=?, updated_at=? WHERE id=?",
                (json.dumps(sources, ensure_ascii=False), now, body.memory_id),
            )
        out = conn.execute(
            """
            SELECT memory_id, observation_id, relation, note, created_at,
                   confidence, evidence_kind, validation_state, producer, metadata_json
            FROM memory_evidence
            WHERE memory_id=? AND observation_id=? AND relation=?
            """,
            (body.memory_id, body.observation_id, body.relation),
        ).fetchone()
    return _row_to_record(out)


def list_evidence_for_memory(db: Any, memory_id: str) -> list[EvidenceRecord]:
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT memory_id, observation_id, relation, note, created_at,
                   confidence, evidence_kind, validation_state, producer, metadata_json
            FROM memory_evidence WHERE memory_id=?
            ORDER BY created_at ASC
            """,
            (memory_id,),
        ).fetchall()
    except Exception:
        rows = conn.execute(
            """
            SELECT memory_id, observation_id, relation, note, created_at
            FROM memory_evidence WHERE memory_id=?
            """,
            (memory_id,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]
