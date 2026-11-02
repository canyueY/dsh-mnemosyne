# -*- coding: utf-8 -*-
"""First-class Experience rows (task/action/outcome material)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "exp") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ExperienceStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def create(
        self,
        *,
        context: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        memory_ids: list[str] | None = None,
        claim_ids: list[str] | None = None,
        reward: float | None = None,
        bank_id: str = "personal",
        source: str = "manual",
    ) -> dict[str, Any]:
        eid = _new_id()
        now = _now()
        ctx = dict(context or {})
        act = dict(action or {})
        out = dict(outcome or {})
        mids = list(memory_ids or [])
        cids = list(claim_ids or [])
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO experiences(
                  id, bank_id, context_json, action_json, outcome_json,
                  memory_ids_json, claim_ids_json, reward, source, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    eid,
                    bank_id,
                    json.dumps(ctx, ensure_ascii=False),
                    json.dumps(act, ensure_ascii=False),
                    json.dumps(out, ensure_ascii=False),
                    json.dumps(mids, ensure_ascii=False),
                    json.dumps(cids, ensure_ascii=False),
                    reward,
                    source,
                    now,
                ),
            )
        return {
            "ok": True,
            "id": eid,
            "bank_id": bank_id,
            "context": ctx,
            "action": act,
            "outcome": out,
            "memory_ids": mids,
            "claim_ids": cids,
            "reward": reward,
            "source": source,
            "created_at": now,
            "note": "Experience is raw material; does not verify Claims",
        }

    def get(self, experience_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM experiences WHERE id=?", (experience_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        bank_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        with self.db.transaction() as conn:
            if bank_id:
                rows = conn.execute(
                    "SELECT * FROM experiences WHERE bank_id=? ORDER BY created_at DESC LIMIT ?",
                    (bank_id, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM experiences ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "bank_id": d.get("bank_id") or "personal",
            "context": json.loads(d.get("context_json") or "{}"),
            "action": json.loads(d.get("action_json") or "{}"),
            "outcome": json.loads(d.get("outcome_json") or "{}"),
            "memory_ids": json.loads(d.get("memory_ids_json") or "[]"),
            "claim_ids": json.loads(d.get("claim_ids_json") or "[]"),
            "reward": d.get("reward"),
            "source": d.get("source") or "manual",
            "created_at": d.get("created_at"),
        }
