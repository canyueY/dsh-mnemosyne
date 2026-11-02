# -*- coding: utf-8 -*-
"""Persist fault localization / repair validation records."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.fault.models import FaultStatus
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "flt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class FaultStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def create(
        self,
        *,
        invariant_code: str,
        subject_refs: list[str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fid = _new_id()
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO fault_cases(
                  id, status, invariant_code, subject_refs_json, detail_json,
                  created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    fid,
                    FaultStatus.OPEN.value,
                    invariant_code,
                    json.dumps(list(subject_refs or []), ensure_ascii=False),
                    json.dumps(dict(detail or {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get(fid)  # type: ignore[return-value]

    def append_stage(
        self,
        fault_id: str,
        stage: str,
        *,
        payload: dict[str, Any] | None = None,
        ok: bool = True,
    ) -> dict[str, Any]:
        sid = _new_id("fst")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO fault_stages(
                  id, fault_id, stage, ok, payload_json, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    sid,
                    fault_id,
                    stage,
                    1 if ok else 0,
                    json.dumps(dict(payload or {}), ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                "UPDATE fault_cases SET updated_at=? WHERE id=?",
                (now, fault_id),
            )
        return {"ok": True, "stage_id": sid, "fault_id": fault_id, "stage": stage}

    def set_status(self, fault_id: str, status: str) -> dict[str, Any]:
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE fault_cases SET status=?, updated_at=? WHERE id=?",
                (status, now, fault_id),
            )
        return {"ok": True, "fault": self.get(fault_id)}

    def get(self, fault_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM fault_cases WHERE id=?", (fault_id,)
            ).fetchone()
            stages = conn.execute(
                """
                SELECT * FROM fault_stages WHERE fault_id=?
                ORDER BY created_at ASC
                """,
                (fault_id,),
            ).fetchall()
        if not row:
            return None
        d = dict(row)
        return {
            "id": d["id"],
            "status": d["status"],
            "invariant_code": d["invariant_code"],
            "subject_refs": json.loads(d.get("subject_refs_json") or "[]"),
            "detail": json.loads(d.get("detail_json") or "{}"),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
            "stages": [
                {
                    "id": s["id"],
                    "stage": s["stage"],
                    "ok": bool(s["ok"]),
                    "payload": json.loads(s["payload_json"] or "{}"),
                    "created_at": s["created_at"],
                }
                for s in stages
            ],
        }
