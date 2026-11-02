# -*- coding: utf-8 -*-
"""Strategy + StrategyLog persistence. ACTIVE requires RL_POLICY_DEPLOYMENT."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.governance.capability_gate import (
    CapabilityLockedError,
    check_capability,
    require_capability,
)
from cognitive_memory.storage.db import Database
from cognitive_memory.strategy.models import StrategyStatus


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "str") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class StrategyStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def propose(
        self,
        name: str,
        *,
        description: str = "",
        scope: dict[str, Any] | None = None,
        status: str = StrategyStatus.PROPOSED.value,
    ) -> dict[str, Any]:
        st = str(status or StrategyStatus.PROPOSED.value).lower()
        if st == StrategyStatus.ACTIVE.value:
            return {
                "ok": False,
                "detail": "cannot_propose_as_active",
                "hint": "use activate() after RL_POLICY_DEPLOYMENT unlock",
            }
        if st not in {
            StrategyStatus.PROPOSED.value,
            StrategyStatus.EXPERIMENTAL.value,
            StrategyStatus.RETIRED.value,
        }:
            st = StrategyStatus.PROPOSED.value
        sid = _new_id()
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO strategies(
                  id, name, description, scope_json, status, propensity_json,
                  version, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    sid,
                    name.strip() or sid,
                    description or "",
                    json.dumps(dict(scope or {}), ensure_ascii=False),
                    st,
                    json.dumps({}, ensure_ascii=False),
                    1,
                    now,
                    now,
                ),
            )
        return {"ok": True, "strategy": self.get(sid)}

    def get(self, strategy_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id=?", (strategy_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM strategies WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                    (status, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM strategies ORDER BY updated_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, strategy_id: str, status: str) -> dict[str, Any]:
        st = str(status or "").lower()
        row = self.get(strategy_id)
        if not row:
            return {"ok": False, "detail": "not_found", "strategy_id": strategy_id}
        if st == StrategyStatus.ACTIVE.value:
            try:
                require_capability(self.db, "RL_POLICY_DEPLOYMENT")
            except CapabilityLockedError as exc:
                return {
                    "ok": False,
                    "detail": "capability_locked",
                    "capability": exc.capability,
                    "strategy_id": strategy_id,
                    "gate": check_capability(self.db, "RL_POLICY_DEPLOYMENT"),
                }
        if st not in {s.value for s in StrategyStatus}:
            return {"ok": False, "detail": "invalid_status", "status": st}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE strategies SET status=?, updated_at=?, version=version+1 WHERE id=?",
                (st, now, strategy_id),
            )
        return {"ok": True, "strategy": self.get(strategy_id)}

    def activate(self, strategy_id: str) -> dict[str, Any]:
        return self.set_status(strategy_id, StrategyStatus.ACTIVE.value)

    def retire(self, strategy_id: str) -> dict[str, Any]:
        return self.set_status(strategy_id, StrategyStatus.RETIRED.value)

    def update_propensity(self, strategy_id: str, propensity: dict[str, Any]) -> dict[str, Any]:
        row = self.get(strategy_id)
        if not row:
            return {"ok": False, "detail": "not_found"}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE strategies SET propensity_json=?, updated_at=? WHERE id=?",
                (json.dumps(dict(propensity or {}), ensure_ascii=False), now, strategy_id),
            )
        return {"ok": True, "strategy": self.get(strategy_id)}

    def log(
        self,
        strategy_id: str,
        *,
        experience_id: str | None = None,
        decision: str = "",
        outcome: str = "",
        propensity: float | None = None,
        overlap: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.get(strategy_id):
            return {"ok": False, "detail": "strategy_not_found", "strategy_id": strategy_id}
        lid = _new_id("slog")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO strategy_logs(
                  id, strategy_id, experience_id, decision, outcome,
                  propensity, overlap, detail_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    lid,
                    strategy_id,
                    experience_id,
                    decision or "",
                    outcome or "",
                    propensity,
                    overlap,
                    json.dumps(dict(detail or {}), ensure_ascii=False),
                    now,
                ),
            )
        return {
            "ok": True,
            "log": {
                "id": lid,
                "strategy_id": strategy_id,
                "experience_id": experience_id,
                "decision": decision,
                "outcome": outcome,
                "propensity": propensity,
                "overlap": overlap,
                "detail": dict(detail or {}),
                "created_at": now,
            },
        }

    def list_logs(self, strategy_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM strategy_logs WHERE strategy_id=?
                ORDER BY created_at DESC LIMIT ?
                """,
                (strategy_id, lim),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            out.append(
                {
                    "id": d["id"],
                    "strategy_id": d["strategy_id"],
                    "experience_id": d.get("experience_id"),
                    "decision": d.get("decision") or "",
                    "outcome": d.get("outcome") or "",
                    "propensity": d.get("propensity"),
                    "overlap": d.get("overlap"),
                    "detail": json.loads(d.get("detail_json") or "{}"),
                    "created_at": d.get("created_at"),
                }
            )
        return out

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "name": d.get("name") or "",
            "description": d.get("description") or "",
            "scope": json.loads(d.get("scope_json") or "{}"),
            "status": d.get("status") or StrategyStatus.PROPOSED.value,
            "propensity": json.loads(d.get("propensity_json") or "{}"),
            "version": int(d.get("version") or 1),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }
