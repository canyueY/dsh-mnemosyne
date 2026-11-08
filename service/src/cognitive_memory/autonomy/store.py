# -*- coding: utf-8 -*-
"""Goal / Plan / Action, Environment Event, Attention Queue. Not Bank or Scenario substitutes."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

GOAL_STATUS = frozenset({"active", "paused", "completed", "cancelled", "suggested"})
ORIGINS = frozenset({"user", "agent_suggested"})
LEVELS = ("E0", "E1", "E2", "E3", "E4")
STAGES = ("discovered", "normalized", "deduped", "source_assessed", "impact", "permission", "scheduled", "responded", "verified")
DISPOSITION = frozenset({"Immediate", "Deferred", "Background", "Ignore"})


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


class AutonomyStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def create_goal(
        self,
        *,
        title: str,
        owner: str,
        origin: str = "user",
        parent_id: str | None = None,
        bank_id: str | None = None,
        scenario_id: str | None = None,
        authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not (title or "").strip():
            return {"ok": False, "detail": "title_required"}
        orig = origin if origin in ORIGINS else "agent_suggested"
        status = "active" if orig == "user" else "suggested"
        gid = _id("gol")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO goals(id,title,origin,owner,status,version,parent_id,bank_id,scenario_id,authorization_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    gid, title.strip(), orig, owner, status, 1, parent_id, bank_id, scenario_id,
                    json.dumps(authorization or {}, ensure_ascii=False), now, now,
                ),
            )
        return {"ok": True, "goal": self.get_goal(gid), "note": "goal_is_not_bank_or_scenario"}

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return self._goal(row) if row else None

    def list_goals(self, *, limit: int = 40) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM goals ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall()
        return [self._goal(r) for r in rows]

    def set_goal_status(self, goal_id: str, status: str, *, actor_origin: str = "user") -> dict[str, Any]:
        goal = self.get_goal(goal_id)
        if not goal:
            return {"ok": False, "detail": "goal_not_found"}
        if goal["origin"] == "user" and actor_origin != "user" and status in {"cancelled", "completed"}:
            return {"ok": False, "detail": "agent_cannot_abandon_or_rewrite_user_goal"}
        if status not in GOAL_STATUS:
            return {"ok": False, "detail": "bad_status"}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE goals SET status=?, version=version+1, updated_at=? WHERE id=?",
                (status, now, goal_id),
            )
        return {"ok": True, "goal": self.get_goal(goal_id)}

    def add_plan(self, goal_id: str, *, title: str, depends_on: list[str] | None = None) -> dict[str, Any]:
        if not self.get_goal(goal_id):
            return {"ok": False, "detail": "goal_not_found"}
        pid = _id("gpl")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO goal_plans(id,goal_id,title,status,depends_on_json,created_at) VALUES (?,?,?,?,?,?)",
                (pid, goal_id, title, "draft", json.dumps(depends_on or [], ensure_ascii=False), now),
            )
        return {"ok": True, "plan": {"id": pid, "goal_id": goal_id, "title": title, "status": "draft", "depends_on": depends_on or []}}

    def add_action(
        self,
        goal_id: str,
        *,
        kind: str,
        authorization: str,
        receipt: dict[str, Any] | None = None,
        trace_id: str | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.get_goal(goal_id):
            return {"ok": False, "detail": "goal_not_found"}
        if not (authorization or "").strip():
            return {"ok": False, "detail": "authorization_required"}
        aid = _id("gac")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO goal_actions(id,goal_id,plan_id,kind,authorization,receipt_json,trace_id,created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (aid, goal_id, plan_id, kind or "note", authorization, json.dumps(receipt or {}, ensure_ascii=False), trace_id, now),
            )
        return {"ok": True, "action": {"id": aid, "goal_id": goal_id, "kind": kind, "authorization": authorization, "receipt": receipt or {}, "trace_id": trace_id}}

    def ingest_event(self, *, source: str, payload: dict[str, Any], response_level: str = "E0") -> dict[str, Any]:
        level = response_level if response_level in LEVELS else "E0"
        blob = json.dumps({"source": source, "payload": payload}, ensure_ascii=False, sort_keys=True)
        fp = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
        now = _now()
        with self.db.transaction() as conn:
            hit = conn.execute("SELECT * FROM env_events WHERE fingerprint=?", (fp,)).fetchone()
            if hit:
                return {"ok": True, "event": self._evt(hit), "deduped": True, "note": "environment_event_is_observation_only"}
            eid = _id("eev")
            conn.execute(
                """
                INSERT INTO env_events(id,source,fingerprint,stage,response_level,status,payload_json,impact_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (eid, source or "", fp, "discovered", level, "open", json.dumps(payload or {}, ensure_ascii=False), "{}", now, now),
            )
        return {"ok": True, "event": self.get_event(eid), "deduped": False, "note": "environment_event_is_observation_only"}

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM env_events WHERE id=?", (event_id,)).fetchone()
        return self._evt(row) if row else None

    def list_events(self, *, limit: int = 40) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM env_events ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall()
        return [self._evt(r) for r in rows]

    def advance_event(self, event_id: str, *, stage: str, actor: str = "agent") -> dict[str, Any]:
        ev = self.get_event(event_id)
        if not ev:
            return {"ok": False, "detail": "event_not_found"}
        if stage not in STAGES:
            return {"ok": False, "detail": "bad_stage"}
        if ev["response_level"] == "E4" and stage in {"scheduled", "responded"} and actor != "user":
            return {"ok": False, "detail": "e4_second_approval_required"}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute("UPDATE env_events SET stage=?, updated_at=? WHERE id=?", (stage, now, event_id))
        return {"ok": True, "event": self.get_event(event_id)}

    def enqueue_attention(
        self,
        *,
        title: str,
        priority: int = 0,
        deadline: str | None = None,
        resource_budget: int = 0,
        disposition: str = "Deferred",
        goal_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        disp = disposition if disposition in DISPOSITION else "Deferred"
        aid = _id("att")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO attention_items(
                  id,title,priority,deadline,resource_budget,aging,checkpoint,disposition,status,goal_id,event_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (aid, title, int(priority), deadline, int(resource_budget), 0, None, disp, "open", goal_id, event_id, now, now),
            )
        return {"ok": True, "item": self.get_attention(aid), "note": "attention_does_not_override_governance"}

    def get_attention(self, item_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM attention_items WHERE id=?", (item_id,)).fetchone()
        return self._att(row) if row else None

    def list_attention(self, *, limit: int = 40) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute(
                "SELECT * FROM attention_items WHERE status='open' ORDER BY priority DESC, created_at ASC LIMIT ?",
                (lim,),
            ).fetchall()
        items = [self._att(r) for r in rows]
        now = _now()
        with self.db.transaction() as conn:
            for it in items:
                conn.execute(
                    "UPDATE attention_items SET aging=aging+1, updated_at=? WHERE id=?",
                    (now, it["id"]),
                )
        return [self.get_attention(it["id"]) or it for it in items]

    def user_attention(self, item_id: str, *, disposition: str | None = None, status: str | None = None, checkpoint: str | None = None) -> dict[str, Any]:
        if not self.get_attention(item_id):
            return {"ok": False, "detail": "attention_not_found"}
        now = _now()
        with self.db.transaction() as conn:
            if disposition and disposition in DISPOSITION:
                conn.execute("UPDATE attention_items SET disposition=?, updated_at=? WHERE id=?", (disposition, now, item_id))
            if status:
                conn.execute("UPDATE attention_items SET status=?, updated_at=? WHERE id=?", (status, now, item_id))
            if checkpoint is not None:
                conn.execute("UPDATE attention_items SET checkpoint=?, updated_at=? WHERE id=?", (checkpoint, now, item_id))
        return {"ok": True, "item": self.get_attention(item_id)}

    def _goal(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "origin": row["origin"],
            "owner": row["owner"],
            "status": row["status"],
            "version": row["version"],
            "parent_id": row["parent_id"],
            "bank_id": row["bank_id"],
            "scenario_id": row["scenario_id"],
            "authorization": _j(row["authorization_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _evt(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source": row["source"],
            "fingerprint": row["fingerprint"],
            "stage": row["stage"],
            "response_level": row["response_level"],
            "status": row["status"],
            "payload": _j(row["payload_json"], {}),
            "impact": _j(row["impact_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _att(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "priority": row["priority"],
            "deadline": row["deadline"],
            "resource_budget": row["resource_budget"],
            "aging": row["aging"],
            "checkpoint": row["checkpoint"],
            "disposition": row["disposition"],
            "status": row["status"],
            "goal_id": row["goal_id"],
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
