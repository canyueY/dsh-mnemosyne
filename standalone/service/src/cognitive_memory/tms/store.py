# -*- coding: utf-8 -*-
"""Optional SQLite persistence for TMS justifications / beliefs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database
from cognitive_memory.tms.engine import TruthMaintenanceEngine
from cognitive_memory.tms.models import Belief, BeliefStatus, Justification


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TmsStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()
        self.engine = TruthMaintenanceEngine()
        with db.exclusive() as conn:
            for row in conn.execute("SELECT * FROM tms_justifications"):
                just = Justification(
                    justification_id=row["id"], conclusion_id=row["conclusion_id"],
                    premises=json.loads(row["premises_json"]), kind=row["kind"],
                    valid=bool(row["valid"]), reason=row["reason"], actor=row["actor"],
                    metadata=json.loads(row["metadata_json"] or "{}"),
                )
                self.engine._justifications[just.justification_id] = just
                self.engine._by_conclusion[just.conclusion_id].append(just.justification_id)
                for premise in just.premises:
                    self.engine._by_premise[premise].append(just.justification_id)
            for row in conn.execute("SELECT * FROM tms_beliefs"):
                self.engine._belief[row["node_id"]] = BeliefStatus(row["status"])

    def persist_justification(self, just: Justification) -> None:
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tms_justifications(
                  id, conclusion_id, premises_json, kind, valid, reason, actor,
                  policy_version, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  valid=excluded.valid,
                  reason=excluded.reason,
                  updated_at=excluded.updated_at,
                  metadata_json=excluded.metadata_json
                """,
                (
                    just.justification_id,
                    just.conclusion_id,
                    json.dumps(list(just.premises), ensure_ascii=False),
                    just.kind,
                    1 if just.valid else 0,
                    just.reason,
                    just.actor,
                    just.policy_version,
                    json.dumps(just.metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def persist_belief(self, node_id: str, status: BeliefStatus, reason_codes: list[str] | None = None) -> None:
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tms_beliefs(node_id, status, reason_codes_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                  status=excluded.status,
                  reason_codes_json=excluded.reason_codes_json,
                  updated_at=excluded.updated_at
                """,
                (
                    node_id,
                    status.value,
                    json.dumps(list(reason_codes or []), ensure_ascii=False),
                    now,
                ),
            )

    def add_and_persist(
        self,
        conclusion_id: str,
        premises: list[str],
        *,
        kind: str = "support",
        reason: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        just = self.engine.add_justification(
            conclusion_id,
            premises,
            kind=kind,
            reason=reason,
            actor=actor,
        )
        self.persist_justification(just)
        for p in premises:
            self.engine.set_premise_truth(p, "true")
        prop = self.engine.propagate([conclusion_id, *premises])
        for ch in prop.changed:
            self.persist_belief(
                ch["node_id"],
                BeliefStatus(ch["to"]),
                reason_codes=["propagate"],
            )
        bel = self.engine.belief(conclusion_id)
        self.persist_belief(conclusion_id, bel.status, bel.reason_codes)
        return {
            "ok": prop.ok,
            "justification": just.model_dump(mode="json"),
            "belief": bel.model_dump(mode="json"),
            "propagate": prop.model_dump(mode="json"),
        }

    def get_belief(self, node_id: str) -> Belief:
        """Prefer persisted belief (cross-instance), else in-memory engine."""
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT status, reason_codes_json FROM tms_beliefs WHERE node_id=?",
                (node_id,),
            ).fetchone()
        if row:
            try:
                codes = json.loads(row["reason_codes_json"] or "[]")
            except json.JSONDecodeError:
                codes = []
            return Belief(
                node_id=node_id,
                status=BeliefStatus(str(row["status"])),
                reason_codes=list(codes) if isinstance(codes, list) else [],
            )
        return self.engine.belief(node_id)

    def invalidate_node(
        self,
        node_id: str,
        *,
        reason: str = "invalidated",
        actor: str = "",
    ) -> dict[str, Any]:
        self.engine.set_premise_truth(node_id, "false")
        # retract support justifications concluding this node
        for jid in list(self.engine._by_conclusion.get(node_id, [])):
            self.engine.retract_justification(jid)
            just = self.engine._justifications[jid]
            self.persist_justification(just)
        prop = self.engine.propagate([node_id])
        for ch in prop.changed:
            self.persist_belief(ch["node_id"], BeliefStatus(ch["to"]), ["invalidate", reason])
        self.engine._belief[node_id] = BeliefStatus.OUT
        self.persist_belief(node_id, BeliefStatus.OUT, [reason, actor or "system"])
        return {
            "ok": True,
            "node_id": node_id,
            "belief": self.get_belief(node_id).model_dump(mode="json"),
            "propagate": prop.model_dump(mode="json"),
        }
