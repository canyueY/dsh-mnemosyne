# -*- coding: utf-8 -*-
"""Scenario + TaskContext persistence. Bank ≠ Scenario; membership is not a Claim."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

CROSS = frozenset({"isolated", "shared", "referenced", "derived", "conditional"})
MEMBER = frozenset({"candidate", "confirmed", "rejected"})
SCN_STATUS = frozenset({"candidate", "active", "archived"})


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


class ScenarioStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def create_scenario(
        self,
        *,
        name: str,
        owner: str,
        parent_id: str | None = None,
        policy: dict[str, Any] | None = None,
        status: str = "candidate",
        audit_ref: str | None = None,
    ) -> dict[str, Any]:
        st = status if status in SCN_STATUS else "candidate"
        sid = _id("scn")
        now = _now()
        pol = dict(policy or {})
        with self.db.transaction() as conn:
            if parent_id:
                hit = conn.execute("SELECT id FROM scenarios WHERE id=?", (parent_id,)).fetchone()
                if not hit:
                    return {"ok": False, "detail": "parent_not_found"}
            conn.execute(
                """
                INSERT INTO scenarios(id,name,parent_id,status,version,owner,policy_json,audit_ref,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (sid, name.strip(), parent_id, st, 1, owner, json.dumps(pol, ensure_ascii=False), audit_ref, now, now),
            )
        return {"ok": True, "scenario": self.get_scenario(sid)}

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        return self._scn(row) if row else None

    def list_scenarios(self, *, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.exclusive() as conn:
            rows = conn.execute("SELECT * FROM scenarios ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall()
        return [self._scn(r) for r in rows]

    def set_status(
        self,
        scenario_id: str,
        status: str,
        *,
        confirm: bool,
        audit_ref: str | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            return {"ok": False, "detail": "confirm_required"}
        st = str(status or "").strip().lower()
        if st not in SCN_STATUS:
            return {"ok": False, "detail": "invalid_status", "allowed": sorted(SCN_STATUS)}
        if not self.get_scenario(scenario_id):
            return {"ok": False, "detail": "scenario_not_found"}
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE scenarios SET status=?, version=version+1, audit_ref=?, updated_at=? WHERE id=?",
                (st, audit_ref, now, scenario_id),
            )
        return {
            "ok": True,
            "scenario": self.get_scenario(scenario_id),
            "note": "scenario_is_not_bank_or_theme",
        }

    def add_membership(
        self,
        scenario_id: str,
        object_id: str,
        *,
        object_kind: str = "",
        source: str = "manual",
        status: str = "candidate",
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> dict[str, Any]:
        if not self.get_scenario(scenario_id):
            return {"ok": False, "detail": "scenario_not_found"}
        st = status if status in MEMBER else "candidate"
        mid = _id("sm")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scenario_memberships(id,scenario_id,object_id,object_kind,source,status,valid_from,valid_to,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scenario_id, object_id) DO UPDATE SET
                  status=excluded.status, source=excluded.source, object_kind=excluded.object_kind
                """,
                (mid, scenario_id, object_id, object_kind, source, st, valid_from, valid_to, now),
            )
        return {"ok": True, "membership": self.get_membership(scenario_id, object_id)}

    def get_membership(self, scenario_id: str, object_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT * FROM scenario_memberships WHERE scenario_id=? AND object_id=?",
                (scenario_id, object_id),
            ).fetchone()
        return self._mem(row) if row else None

    def list_memberships(self, scenario_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.db.exclusive() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM scenario_memberships WHERE scenario_id=? AND status=?",
                    (scenario_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scenario_memberships WHERE scenario_id=?",
                    (scenario_id,),
                ).fetchall()
        return [self._mem(r) for r in rows]

    def relate_scenarios(self, from_id: str, to_id: str, kind: str) -> dict[str, Any]:
        k = str(kind or "").strip().lower()
        if k not in CROSS:
            return {"ok": False, "detail": "invalid_cross_kind", "allowed": sorted(CROSS)}
        if not self.get_scenario(from_id) or not self.get_scenario(to_id):
            return {"ok": False, "detail": "scenario_not_found"}
        rid = _id("srel")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO scenario_relations(id,from_id,to_id,kind,created_at) VALUES (?,?,?,?,?)",
                (rid, from_id, to_id, k, _now()),
            )
        return {"ok": True, "id": rid, "kind": k}

    def suggest_candidate(self, scenario_id: str, object_id: str, *, fingerprint: str = "") -> dict[str, Any]:
        """Suggestion only. Does not change retrieval until membership is confirmed."""
        fp = fingerprint or hashlib.sha256(f"{scenario_id}:{object_id}".encode("utf-8")).hexdigest()
        with self.db.exclusive() as conn:
            if conn.execute("SELECT 1 FROM scenario_candidate_rejects WHERE fingerprint=?", (fp,)).fetchone():
                return {"ok": True, "skipped": True, "detail": "fingerprint_rejected", "fingerprint": fp}
        return self.add_membership(scenario_id, object_id, source="suggest", status="candidate")

    def reject_candidate(self, fingerprint: str, *, scenario_id: str | None = None, reason: str = "") -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scenario_candidate_rejects(fingerprint,scenario_id,reason,created_at) VALUES (?,?,?,?)",
                (fingerprint, scenario_id, reason, _now()),
            )
        return {"ok": True, "fingerprint": fingerprint, "retrieval_unchanged": True}

    def create_task_context(
        self,
        *,
        principal: str,
        scenario_id: str | None,
        project_id: str | None = None,
        role_id: str | None = None,
        allowed_banks: list[str] | None = None,
        allowed_scopes: list[str] | None = None,
        memory_allowlist: list[str] | None = None,
        memory_denylist: list[str] | None = None,
        sharing_policy: str = "isolated",
        authorization_level: str = "minimal",
        context_budget: int = 32,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if scenario_id and not self.get_scenario(scenario_id):
            return {"ok": False, "detail": "scenario_not_found"}
        scn = self.get_scenario(scenario_id) if scenario_id else None
        policy = (scn or {}).get("policy") or {}
        banks = list(allowed_banks or policy.get("allowed_banks") or [])
        share = sharing_policy if sharing_policy in CROSS else "isolated"
        tid = _id("tctx")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO task_contexts(
                  id,principal,scenario_id,project_id,role_id,allowed_banks_json,allowed_scopes_json,
                  memory_allowlist_json,memory_denylist_json,sharing_policy,authorization_level,
                  context_budget,expires_at,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tid, principal, scenario_id, project_id, role_id,
                    json.dumps(banks, ensure_ascii=False),
                    json.dumps(list(allowed_scopes or policy.get("allowed_scopes") or []), ensure_ascii=False),
                    json.dumps(list(memory_allowlist or []), ensure_ascii=False),
                    json.dumps(list(memory_denylist or []), ensure_ascii=False),
                    share, authorization_level, int(context_budget), expires_at, now,
                ),
            )
        return {"ok": True, "task_context": self.get_task_context(tid)}

    def list_task_contexts(
        self,
        *,
        scenario_id: str | None = None,
        principal: str | None = None,
        sharing_policy: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit or 50)))
        sql = "SELECT * FROM task_contexts WHERE 1=1"
        args: list[Any] = []
        if scenario_id:
            sql += " AND scenario_id=?"
            args.append(str(scenario_id))
        if principal:
            sql += " AND principal=?"
            args.append(str(principal))
        if sharing_policy:
            sql += " AND sharing_policy=?"
            args.append(str(sharing_policy))
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(lim)
        with self.db.exclusive() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        return [self._tctx(row) for row in rows if row]

    def get_task_context(self, task_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM task_contexts WHERE id=?", (task_id,)).fetchone()
        return self._tctx(row) if row else None

    def context_expired(self, ctx: dict[str, Any]) -> bool:
        exp = ctx.get("expires_at")
        if not exp:
            return False
        try:
            return datetime.fromisoformat(str(exp).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return True

    def allowed_ids(self, ctx: dict[str, Any]) -> set[str] | None:
        """None = no scenario selected (caller must not default to global)."""
        if not ctx or not ctx.get("scenario_id"):
            return None
        if self.context_expired(ctx):
            return set()
        deny = set(ctx.get("memory_denylist") or [])
        allow = set(ctx.get("memory_allowlist") or [])
        confirmed = {m["object_id"] for m in self.list_memberships(ctx["scenario_id"], status="confirmed")}
        visibility: dict[str, str] = {oid: "local" for oid in confirmed}
        policy = str(ctx.get("sharing_policy") or "isolated")
        if policy in {"shared", "referenced", "derived", "conditional"}:
            with self.db.exclusive() as conn:
                rels = conn.execute(
                    "SELECT to_id, kind FROM scenario_relations WHERE from_id=?",
                    (ctx["scenario_id"],),
                ).fetchall()
            for rel in rels:
                if str(rel["kind"] or "") != policy:
                    continue
                for m in self.list_memberships(rel["to_id"], status="confirmed"):
                    oid = m["object_id"]
                    if oid not in confirmed:
                        visibility[oid] = policy
                    confirmed.add(oid)
        ctx["_visibility"] = visibility
        return (confirmed | allow) - deny

    def apply_access_gate(
        self,
        *,
        access: Any,
        task_context_id: str | None,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Agents cannot default to the global library. Node admin is diagnostic, not a Scenario."""
        tid = str(task_context_id or "").strip()
        node = bool(getattr(access, "is_node_admin", False))
        if not tid:
            if node:
                return {"ok": True, "items": items, "scope": "node_admin_unscoped_not_scenario"}
            return {
                "ok": False,
                "status": "NOT_NEEDED",
                "detail": "scenario_required",
                "items": [],
                "note": "no_default_global_context",
            }
        ctx = self.get_task_context(tid)
        if ctx is None:
            if node:
                return {"ok": True, "items": items, "scope": "unknown_task_context_unscoped"}
            return {
                "ok": False,
                "status": "NOT_NEEDED",
                "detail": "scenario_required",
                "items": [],
                "note": "task_context_not_found",
            }
        gated = self.filter_items(items, ctx)
        gated["ok"] = True
        return gated

    def filter_items(self, items: list[dict[str, Any]], ctx: dict[str, Any] | None) -> dict[str, Any]:
        if ctx is None:
            return {"ok": False, "detail": "scenario_required", "items": [], "note": "no_default_global"}
        if self.context_expired(ctx):
            return {"ok": False, "detail": "task_context_expired", "items": []}
        allowed = self.allowed_ids(ctx)
        banks = set(ctx.get("allowed_banks") or [])
        out = []
        for it in items:
            oid = str(it.get("id") or (it.get("ref") or {}).get("authority_id") or "")
            bank = str(it.get("bank_id") or (it.get("ref") or {}).get("bank_id") or (it.get("metadata") or {}).get("bank_id") or "")
            if banks and bank and bank not in banks:
                continue
            if allowed is None:
                continue
            if oid and oid in allowed:
                vis = (ctx.get("_visibility") or {}).get(oid) or "local"
                stamped = dict(it)
                stamped["scenario_visibility"] = vis
                out.append(stamped)
        budget = int(ctx.get("context_budget") or 32)
        return {
            "ok": True,
            "items": out[: max(0, budget)],
            "task_context_id": ctx.get("id"),
            "scenario_id": ctx.get("scenario_id"),
            "sharing_policy": ctx.get("sharing_policy") or "isolated",
            "authorization_level": ctx.get("authorization_level") or "minimal",
            "truncated": len(out) > budget,
        }

    @staticmethod
    def _scn(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "name": d["name"],
            "parent_id": d.get("parent_id"),
            "status": d["status"],
            "version": int(d.get("version") or 1),
            "owner": d.get("owner") or "",
            "policy": _j(d.get("policy_json"), {}),
            "audit_ref": d.get("audit_ref"),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
            "kind": "Scenario",
            "note": "scenario_is_not_bank_or_theme",
        }

    @staticmethod
    def _mem(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "scenario_id": d["scenario_id"],
            "object_id": d["object_id"],
            "object_kind": d.get("object_kind") or "",
            "source": d.get("source") or "",
            "status": d["status"],
            "valid_from": d.get("valid_from"),
            "valid_to": d.get("valid_to"),
            "created_at": d.get("created_at"),
        }

    @staticmethod
    def _tctx(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "task_id": d["id"],
            "principal": d.get("principal") or "",
            "scenario_id": d.get("scenario_id"),
            "project_id": d.get("project_id"),
            "role_id": d.get("role_id"),
            "allowed_banks": _j(d.get("allowed_banks_json"), []),
            "allowed_scopes": _j(d.get("allowed_scopes_json"), []),
            "memory_allowlist": _j(d.get("memory_allowlist_json"), []),
            "memory_denylist": _j(d.get("memory_denylist_json"), []),
            "sharing_policy": d.get("sharing_policy") or "isolated",
            "authorization_level": d.get("authorization_level") or "minimal",
            "context_budget": int(d.get("context_budget") or 32),
            "expires_at": d.get("expires_at"),
            "created_at": d.get("created_at"),
        }
