# -*- coding: utf-8 -*-
"""同 Node 多 Agent 授权：权限 / Bank 范围 / 有效期 / 撤销 / 审计。

Node Bearer Token = 运维超管（不经 grant）。
Agent 须 agent_id + agent_token，且当前 grant 未撤销、未过期。
注册主体 ≠ 已授权（ENGINEERING_CONSTRAINTS）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cognitive_memory.identity.grant_cache import DEFAULT_GRANT_CACHE, invalidate_grant_cache
from cognitive_memory.identity.models import PrincipalKind, PrincipalUpsert
from cognitive_memory.identity.store import get_principal, upsert_principal
from cognitive_memory.storage.ids import new_id


class AgentAccessError(PermissionError):
    def __init__(self, message: str, *, code: str = "agent_denied"):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_s() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_grant_tables(db: Any) -> None:
    """迁移 008 已建表；此处幂等兜底（测试/旧库）。"""
    with db.transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_grants (
              id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              token_hash TEXT NOT NULL,
              permissions_json TEXT NOT NULL DEFAULT '[]',
              banks_json TEXT NOT NULL DEFAULT '[]',
              consent_json TEXT NOT NULL DEFAULT '{}',
              expires_at TEXT,
              revoked_at TEXT,
              revoke_reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_grants_agent
              ON agent_grants(agent_id, revoked_at, expires_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_audit (
              id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              action TEXT NOT NULL,
              resource TEXT NOT NULL DEFAULT '',
              decision TEXT NOT NULL,
              detail_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_audit_agent
              ON agent_audit(agent_id, created_at)
            """
        )


@dataclass
class AgentGrant:
    id: str
    agent_id: str
    permissions: list[str]
    banks: list[str]
    token_hash: str
    expires_at: str | None
    revoked_at: str | None
    revoke_reason: str
    consent: dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self, *, include_hash: bool = False) -> dict[str, Any]:
        out = {
            "id": self.id,
            "agent_id": self.agent_id,
            "permissions": list(self.permissions),
            "banks": list(self.banks),
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "revoke_reason": self.revoke_reason,
            "consent": dict(self.consent),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "active": self.is_active(),
        }
        if include_hash:
            out["token_hash"] = self.token_hash
        return out

    def is_active(self, *, now: datetime | None = None) -> bool:
        if self.revoked_at:
            return False
        if not self.token_hash:
            return False
        exp = _parse_ts(self.expires_at)
        if exp and (now or _now()) >= exp:
            return False
        return True


def _row_to_grant(row: Any) -> AgentGrant:
    try:
        perms = json.loads(row["permissions_json"] or "[]")
    except json.JSONDecodeError:
        perms = []
    try:
        banks = json.loads(row["banks_json"] or "[]")
    except json.JSONDecodeError:
        banks = []
    try:
        consent = json.loads(row["consent_json"] or "{}")
    except json.JSONDecodeError:
        consent = {}
    return AgentGrant(
        id=str(row["id"]),
        agent_id=str(row["agent_id"]),
        permissions=[str(x) for x in perms if str(x).strip()],
        banks=[str(x) for x in banks if str(x).strip()],
        token_hash=str(row["token_hash"] or ""),
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        revoke_reason=str(row["revoke_reason"] or "") if "revoke_reason" in row.keys() else "",
        consent=consent if isinstance(consent, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def register_agent(
    db: Any,
    *,
    agent_id: str | None = None,
    display_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """仅登记主体；不发 token、不授权。"""
    ensure_grant_tables(db)
    body = PrincipalUpsert(
        id=agent_id,
        kind=PrincipalKind.AGENT,
        display_name=display_name or (agent_id or "agent"),
        metadata=dict(metadata or {}),
    )
    p = upsert_principal(db, body)
    audit(db, agent_id=p.id, action="register", decision="ok", resource="identity")
    return {"registered": True, "authorized": False, "principal": p.model_dump()}


def issue_grant(
    db: Any,
    agent_id: str,
    *,
    permissions: list[str] | None = None,
    banks: list[str] | None = None,
    expires_in_sec: int | None = 86400 * 30,
    consent: dict[str, Any] | None = None,
    rotate_token: bool = True,
) -> dict[str, Any]:
    ensure_grant_tables(db)
    principal = get_principal(db, agent_id)
    if not principal:
        raise AgentAccessError(f"agent not registered: {agent_id}", code="not_registered")
    if principal.kind not in (PrincipalKind.AGENT, PrincipalKind.EXTERNAL):
        raise AgentAccessError("grants only for agent/external", code="invalid_kind")

    now = _now()
    now_s = _now_s()
    expires_at = None
    if expires_in_sec is not None and int(expires_in_sec) > 0:
        expires_at = (now + timedelta(seconds=int(expires_in_sec))).strftime("%Y-%m-%dT%H:%M:%SZ")

    perms = [str(x) for x in (permissions or ["memory.read"]) if str(x).strip()]
    bks = [str(x) for x in (banks or ["personal"]) if str(x).strip()]

    # 新发 grant：先撤销旧 active grants 的 token（同 agent 只保留一条 active）
    active = get_active_grant(db, agent_id)
    token_plain: str | None = None
    if rotate_token or not active:
        token_plain = secrets.token_urlsafe(32)
        token_h = hash_token(token_plain)
    else:
        token_h = active.token_hash

    if active and not active.revoked_at:
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE agent_grants
                SET revoked_at=?, revoke_reason=?, updated_at=?
                WHERE id=? AND revoked_at IS NULL
                """,
                (now_s, "superseded", now_s, active.id),
            )
        invalidate_grant_cache(agent_id)

    gid = new_id("agr")
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO agent_grants(
              id, agent_id, token_hash, permissions_json, banks_json,
              consent_json, expires_at, revoked_at, revoke_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '', ?, ?)
            """,
            (
                gid,
                agent_id,
                token_h,
                json.dumps(perms, ensure_ascii=False),
                json.dumps(bks, ensure_ascii=False),
                json.dumps(consent or {"user_consent": True}, ensure_ascii=False),
                expires_at,
                now_s,
                now_s,
            ),
        )
    invalidate_grant_cache(agent_id)
    grant = get_grant_by_id(db, gid)
    audit(
        db,
        agent_id=agent_id,
        action="issue_grant",
        decision="ok",
        resource="grant",
        detail={"grant_id": gid, "permissions": perms, "banks": bks, "expires_at": expires_at},
    )
    out: dict[str, Any] = {
        "ok": True,
        "grant": grant.to_dict() if grant else None,
        "note": "token shown once; store securely",
    }
    if token_plain:
        out["agent_token"] = token_plain
    return out


def get_grant_by_id(db: Any, grant_id: str) -> AgentGrant | None:
    ensure_grant_tables(db)
    conn = db.connect()
    row = conn.execute("SELECT * FROM agent_grants WHERE id=?", (grant_id,)).fetchone()
    return _row_to_grant(row) if row else None


def get_active_grant(db: Any, agent_id: str) -> AgentGrant | None:
    ensure_grant_tables(db)
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT * FROM agent_grants
        WHERE agent_id=?
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (agent_id,),
    ).fetchall()
    now = _now()
    for row in rows:
        g = _row_to_grant(row)
        if g.is_active(now=now):
            return g
    return None


def list_grants(db: Any, *, limit: int = 200, agent_id: str | None = None) -> list[dict[str, Any]]:
    ensure_grant_tables(db)
    lim = max(1, min(int(limit or 200), 1000))
    conn = db.connect()
    if agent_id:
        rows = conn.execute(
            """
            SELECT * FROM agent_grants WHERE agent_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (agent_id, lim),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_grants ORDER BY created_at DESC LIMIT ?",
            (lim,),
        ).fetchall()
    return [_row_to_grant(r).to_dict() for r in rows]


def revoke_grant(db: Any, agent_id: str, *, reason: str = "") -> dict[str, Any]:
    ensure_grant_tables(db)
    grant = get_active_grant(db, agent_id)
    if not grant:
        invalidate_grant_cache(agent_id)
        return {"ok": False, "error": "not_found"}
    now_s = _now_s()
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE agent_grants
            SET revoked_at=?, revoke_reason=?, token_hash='', updated_at=?
            WHERE agent_id=? AND revoked_at IS NULL
            """,
            (now_s, reason or "revoked", now_s, agent_id),
        )
    # P8: revoke must drop cached grant immediately (no TTL wait).
    dropped = invalidate_grant_cache(agent_id)
    audit(
        db,
        agent_id=agent_id,
        action="revoke",
        decision="ok",
        resource="grant",
        detail={"reason": reason, "grant_id": grant.id, "cache_dropped": dropped},
    )
    return {
        "ok": True,
        "agent_id": agent_id,
        "revoked_at": now_s,
        "grant_id": grant.id,
        "cache_invalidated": True,
        "cache_dropped": dropped,
    }


def authenticate_agent(db: Any, agent_id: str, token: str) -> AgentGrant:
    token_h = hash_token(token) if token else ""
    cached = DEFAULT_GRANT_CACHE.get(agent_id)
    if cached and cached.get("token_hash") and token_h:
        if secrets.compare_digest(str(cached["token_hash"]), token_h) and not cached.get(
            "revoked_at"
        ):
            # Re-hydrate AgentGrant from cache snapshot (still verify token).
            grant = AgentGrant(
                id=str(cached["id"]),
                agent_id=agent_id,
                permissions=list(cached.get("permissions") or []),
                banks=list(cached.get("banks") or []),
                token_hash=str(cached["token_hash"]),
                expires_at=cached.get("expires_at"),
                revoked_at=cached.get("revoked_at"),
                revoke_reason=str(cached.get("revoke_reason") or ""),
                consent=dict(cached.get("consent") or {}),
                created_at=str(cached.get("created_at") or ""),
                updated_at=str(cached.get("updated_at") or ""),
            )
            if grant.is_active():
                return grant
            invalidate_grant_cache(agent_id)

    grant = get_active_grant(db, agent_id)
    if not grant:
        # 区分：从未授权 vs 已撤销/过期
        rows = list_grants(db, agent_id=agent_id, limit=1)
        if not rows:
            raise AgentAccessError("no grant", code="no_grant")
        if rows[0].get("revoked_at"):
            raise AgentAccessError("grant revoked", code="revoked")
        raise AgentAccessError("grant expired", code="expired")
    if not token or not secrets.compare_digest(grant.token_hash, hash_token(token)):
        raise AgentAccessError("invalid agent token", code="bad_token")
    snap = grant.to_dict(include_hash=True)
    snap["consent"] = dict(grant.consent)
    DEFAULT_GRANT_CACHE.put(agent_id, snap)
    return grant


def require_agent_access(
    db: Any,
    agent_id: str,
    token: str,
    *,
    permission: str,
    bank: str | None = None,
    resource: str = "",
    audit_deny: bool = True,
) -> AgentGrant:
    try:
        grant = authenticate_agent(db, agent_id, token)
        if permission not in grant.permissions:
            raise AgentAccessError(
                f"permission denied: {permission}",
                code="permission_denied",
            )
        if bank and bank not in grant.banks:
            raise AgentAccessError(
                f"bank access denied: {bank}",
                code="bank_denied",
            )
    except AgentAccessError as exc:
        if audit_deny:
            audit(
                db,
                agent_id=agent_id or "",
                action="access",
                decision="deny",
                resource=resource or permission,
                detail={"code": exc.code, "permission": permission, "bank": bank},
            )
        raise
    audit(
        db,
        agent_id=agent_id,
        action="access",
        decision="allow",
        resource=resource or permission,
        detail={"permission": permission, "bank": bank, "grant_id": grant.id},
    )
    return grant


def audit(
    db: Any,
    *,
    agent_id: str,
    action: str,
    decision: str,
    resource: str = "",
    detail: dict[str, Any] | None = None,
) -> str:
    ensure_grant_tables(db)
    aid = new_id("aaud")
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO agent_audit(
              id, agent_id, action, resource, decision, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aid,
                agent_id,
                action,
                resource,
                decision,
                json.dumps(detail or {}, ensure_ascii=False),
                _now_s(),
            ),
        )
    return aid


def list_audit(
    db: Any,
    *,
    agent_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_grant_tables(db)
    lim = max(1, min(int(limit or 100), 500))
    conn = db.connect()
    if agent_id:
        rows = conn.execute(
            """
            SELECT * FROM agent_audit
            WHERE agent_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (agent_id, lim),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM agent_audit
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        out.append(
            {
                "id": r["id"],
                "agent_id": r["agent_id"],
                "action": r["action"],
                "resource": r["resource"],
                "decision": r["decision"],
                "detail": detail,
                "created_at": r["created_at"],
            }
        )
    return out
