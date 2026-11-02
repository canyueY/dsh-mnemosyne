# -*- coding: utf-8 -*-
"""Knowledge node persistence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.knowledge.models import KnowledgeKind, KnowledgeStatus
from cognitive_memory.knowledge.validator import validate_knowledge
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "kn") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class KnowledgeStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def propose(
        self,
        *,
        kind: str,
        name: str,
        description: str = "",
        claim_ids: list[str] | None = None,
        schema_id: str | None = None,
        strategy_id: str | None = None,
        payload: dict[str, Any] | None = None,
        scope: dict[str, Any] | None = None,
        security: dict[str, Any] | None = None,
        status: str = KnowledgeStatus.PROPOSED.value,
    ) -> dict[str, Any]:
        st = str(status or KnowledgeStatus.PROPOSED.value)
        if st == KnowledgeStatus.ACTIVE.value:
            st = KnowledgeStatus.PROPOSED.value
        draft = {
            "kind": kind,
            "name": name,
            "status": st,
            "claim_ids": list(claim_ids or []),
            "schema_id": schema_id,
            "strategy_id": strategy_id,
            "payload": dict(payload or {}),
        }
        v = validate_knowledge(draft)
        if not v["ok"]:
            return {"ok": False, "detail": "validation_failed", "validation": v}

        kid = _new_id()
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_nodes(
                  id, kind, name, description, status,
                  claim_ids_json, schema_id, strategy_id,
                  payload_json, scope_json, security_json, validation_json,
                  version, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    kid,
                    kind,
                    name.strip(),
                    description or "",
                    st,
                    json.dumps(list(claim_ids or []), ensure_ascii=False),
                    schema_id,
                    strategy_id,
                    json.dumps(dict(payload or {}), ensure_ascii=False),
                    json.dumps(dict(scope or {}), ensure_ascii=False),
                    json.dumps(dict(security or {}), ensure_ascii=False),
                    json.dumps(v, ensure_ascii=False),
                    1,
                    now,
                    now,
                ),
            )
        return {"ok": True, "knowledge": self.get(kid), "validation": v}

    def get(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_nodes WHERE id=?", (knowledge_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.transaction() as conn:
            q = "SELECT * FROM knowledge_nodes WHERE 1=1"
            args: list[Any] = []
            if kind:
                q += " AND kind=?"
                args.append(kind)
            if status:
                q += " AND status=?"
                args.append(status)
            q += " ORDER BY updated_at DESC LIMIT ?"
            args.append(lim)
            rows = conn.execute(q, tuple(args)).fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, knowledge_id: str, status: str) -> dict[str, Any]:
        st = str(status or "").strip()
        if st not in {s.value for s in KnowledgeStatus}:
            return {"ok": False, "detail": "invalid_status"}
        row = self.get(knowledge_id)
        if not row:
            return {"ok": False, "detail": "not_found"}
        # ACTIVE knowledge ≠ Claim Verified / Strategy deploy
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_nodes SET status=?, updated_at=?, version=version+1 WHERE id=?",
                (st, now, knowledge_id),
            )
        out = self.get(knowledge_id)
        return {
            "ok": True,
            "knowledge": out,
            "note": "knowledge_active_does_not_verify_claims_or_deploy_strategy",
        }

    def from_schema(
        self,
        schema_id: str,
        *,
        name: str = "",
        as_pattern: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Demote / project Schema → Structural or Pattern Knowledge."""
        kind = (
            KnowledgeKind.PATTERN.value
            if as_pattern
            else KnowledgeKind.STRUCTURAL.value
        )
        return self.propose(
            kind=kind,
            name=name or f"schema:{schema_id}",
            schema_id=schema_id,
            payload=payload or {"source": "schema_demotion"},
            description="derived from cognitive schema; not Claim-verified",
        )

    def from_claims(
        self,
        claim_ids: list[str],
        *,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        return self.propose(
            kind=KnowledgeKind.CLAIM.value,
            name=name,
            description=description,
            claim_ids=claim_ids,
        )

    def from_strategy(
        self,
        strategy_id: str,
        *,
        name: str = "",
    ) -> dict[str, Any]:
        return self.propose(
            kind=KnowledgeKind.STRATEGY.value,
            name=name or f"strategy:{strategy_id}",
            strategy_id=strategy_id,
        )

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "kind": d.get("kind"),
            "name": d.get("name") or "",
            "description": d.get("description") or "",
            "status": d.get("status") or KnowledgeStatus.PROPOSED.value,
            "claim_ids": json.loads(d.get("claim_ids_json") or "[]"),
            "schema_id": d.get("schema_id"),
            "strategy_id": d.get("strategy_id"),
            "payload": json.loads(d.get("payload_json") or "{}"),
            "scope": json.loads(d.get("scope_json") or "{}"),
            "security": json.loads(d.get("security_json") or "{}"),
            "validation": json.loads(d.get("validation_json") or "{}"),
            "version": int(d.get("version") or 1),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }
