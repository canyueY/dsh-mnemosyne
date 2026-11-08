# -*- coding: utf-8 -*-
"""Repair proposals, tech debt, evolution experiments, capability gates."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

FROZEN = frozenset({
    "user_final_authority",
    "identity_auth_base",
    "approval_audit",
    "production_data_protection",
    "bank_scenario_isolation",
    "evolution_safety_gate",
})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _j(raw: Any, default: Any):
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw or ("[]" if isinstance(default, list) else "{}"))
    except json.JSONDecodeError:
        return default


class EvolutionStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def propose_repair(
        self,
        *,
        fault_id: str | None,
        evidence: list[Any] | None,
        impact: dict[str, Any] | None,
        risk_level: str,
        change_set: list[Any] | None,
        tests: list[Any] | None,
        rollback: dict[str, Any] | None,
        sandbox: dict[str, Any] | None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if not rollback:
            return {"ok": False, "detail": "rollback_required"}
        pid = _id("rpr")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO repair_proposals(
                  id,fault_id,evidence_json,impact_json,risk_level,change_set_json,tests_json,
                  rollback_json,sandbox_json,expires_at,status,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid, fault_id, json.dumps(evidence or [], ensure_ascii=False),
                    json.dumps(impact or {}, ensure_ascii=False), risk_level or "high",
                    json.dumps(change_set or [], ensure_ascii=False), json.dumps(tests or [], ensure_ascii=False),
                    json.dumps(rollback, ensure_ascii=False), json.dumps(sandbox or {}, ensure_ascii=False),
                    expires_at, "proposed", now,
                ),
            )
        return {"ok": True, "proposal": self.get_proposal(pid), "note": "proposal_does_not_mutate_system"}

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM repair_proposals WHERE id=?", (proposal_id,)).fetchone()
        return self._prop(row) if row else None

    def list_proposals(self, *, limit: int = 40) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM repair_proposals ORDER BY created_at DESC LIMIT ?", (lim,)).fetchall()
        return [self._prop(r) for r in rows]

    def apply_proposal(self, proposal_id: str, *, confirm: bool) -> dict[str, Any]:
        prop = self.get_proposal(proposal_id)
        if not prop:
            return {"ok": False, "detail": "proposal_not_found"}
        if not confirm:
            return {"ok": False, "detail": "confirm_required"}
        sandbox = prop.get("sandbox") or {}
        if not sandbox.get("ok"):
            return {"ok": False, "detail": "sandbox_required"}
        if not prop.get("rollback"):
            return {"ok": False, "detail": "rollback_required"}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute("UPDATE repair_proposals SET status='applied' WHERE id=?", (proposal_id,))
            conn.execute(
                "INSERT INTO tech_debt(id,title,area,status,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (_id("tdb"), f"apply receipt {proposal_id}", "evolution", "open", "effect_verification_pending", now, now),
            )
        return {"ok": True, "proposal": self.get_proposal(proposal_id), "note": "no_auto_strategy_promotion"}

    def add_debt(self, *, title: str, area: str = "unknown", notes: str = "") -> dict[str, Any]:
        did = _id("tdb")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO tech_debt(id,title,area,status,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (did, title, area, "open", notes, now, now),
            )
        return {"ok": True, "item": {"id": did, "title": title, "area": area, "status": "open"}}

    def list_debt(self, *, limit: int = 40) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM tech_debt ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall()
        return [dict(r) for r in rows]

    def create_experiment(
        self,
        *,
        title: str,
        control: dict[str, Any] | None,
        bank_id: str = "sandbox",
        version: str = "1",
    ) -> dict[str, Any]:
        eid = _id("eex")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO evolution_experiments(id,title,version,control_json,result_json,status,bank_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (eid, title, version, json.dumps(control or {}, ensure_ascii=False), "{}", "draft", bank_id or "sandbox", now, now),
            )
        return {"ok": True, "experiment": self.get_experiment(eid), "note": "failure_must_not_pollute_production"}

    def list_experiments(self, *, limit: int = 40) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute(
                "SELECT * FROM evolution_experiments ORDER BY updated_at DESC LIMIT ?",
                (lim,),
            ).fetchall()
        items = []
        for row in rows:
            items.append({
                "id": row["id"],
                "title": row["title"],
                "version": row["version"],
                "control": _j(row["control_json"], {}),
                "result": _j(row["result_json"], {}),
                "status": row["status"],
                "bank_id": row["bank_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return items

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM evolution_experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "version": row["version"],
            "control": _j(row["control_json"], {}),
            "result": _j(row["result_json"], {}),
            "status": row["status"],
            "bank_id": row["bank_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record_experiment_result(self, experiment_id: str, *, result: dict[str, Any], status: str = "completed") -> dict[str, Any]:
        if not self.get_experiment(experiment_id):
            return {"ok": False, "detail": "experiment_not_found"}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE evolution_experiments SET result_json=?, status=?, updated_at=? WHERE id=?",
                (json.dumps(result or {}, ensure_ascii=False), status, now, experiment_id),
            )
        return {"ok": True, "experiment": self.get_experiment(experiment_id), "note": "self_score_does_not_promote_strategy"}

    def list_gates(self) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM capability_gates ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def set_gate(self, gate_id: str, *, autonomy_level: str, actor: str) -> dict[str, Any]:
        if gate_id in FROZEN:
            return {"ok": False, "detail": "frozen_capability_gate", "gate_id": gate_id}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO capability_gates(id,frozen,autonomy_level,note,updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET autonomy_level=excluded.autonomy_level, updated_at=excluded.updated_at",
                (gate_id, 0, autonomy_level, f"updated_by:{actor}", now),
            )
        return {"ok": True, "gates": self.list_gates()}

    def _prop(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "fault_id": row["fault_id"],
            "evidence": _j(row["evidence_json"], []),
            "impact": _j(row["impact_json"], {}),
            "risk_level": row["risk_level"],
            "change_set": _j(row["change_set_json"], []),
            "tests": _j(row["tests_json"], []),
            "rollback": _j(row["rollback_json"], {}),
            "sandbox": _j(row["sandbox_json"], {}),
            "expires_at": row["expires_at"],
            "status": row["status"],
            "created_at": row["created_at"],
        }
