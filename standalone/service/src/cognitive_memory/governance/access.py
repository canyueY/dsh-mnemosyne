# -*- coding: utf-8 -*-
"""统一访问执法：Node Bearer（运维超管）或 Agent grant（范围权限）。

字段 ≠ 执法：memory CRUD 必须经本模块；硬删另受 capability_gate 约束。
"""

from __future__ import annotations

import secrets
import json
from dataclasses import dataclass
from typing import Literal

from fastapi import Header, HTTPException, Request

from cognitive_memory.governance.capability_gate import CapabilityLockedError, require_capability
from cognitive_memory.identity.grants import AgentAccessError, require_agent_access

ActorKind = Literal["node", "agent"]


@dataclass
class AccessContext:
    kind: ActorKind
    actor_id: str
    banks: list[str] | None = None
    permissions: list[str] | None = None
    grant_id: str | None = None

    @property
    def is_node_admin(self) -> bool:
        return self.kind == "node"

    def to_security_context(self, *, default_bank: str = "personal"):
        """Map AccessContext → SecurityContext for Claim visibility."""
        from cognitive_memory.security.context import SecurityContext

        banks = list(self.banks or [])
        if self.is_node_admin:
            # Node admin: default bank personal; grants allow explicit banks if listed
            grants = [f"bank:{b}" for b in banks] if banks else []
            return SecurityContext(
                bank_id=default_bank,
                agent_id="",
                owner_id=self.actor_id,
                authorized_grants=grants or [f"bank:{default_bank}"],
            )
        primary = banks[0] if banks else default_bank
        grants = [f"bank:{b}" for b in banks]
        return SecurityContext(
            bank_id=primary,
            agent_id=self.actor_id,
            owner_id=self.actor_id,
            authorized_grants=grants,
        )


def _extract_bearer(authorization: str | None, x_memory_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_memory_token:
        return x_memory_token.strip()
    return None


def resolve_access(
    request: Request,
    *,
    permission: str,
    bank: str | None = None,
    resource: str = "",
    allow_node: bool = True,
    require_capability_id: str | None = None,
) -> AccessContext:
    """Header：Bearer node_token；或 X-Agent-Id + X-Agent-Token（有 Agent 头则必须过 grant）。"""
    app = request.app
    service = app.state.service
    authorization = request.headers.get("authorization")
    x_memory_token = request.headers.get("x-memory-token")
    agent_id = (request.headers.get("x-agent-id") or "").strip()
    agent_token = (request.headers.get("x-agent-token") or "").strip()

    if agent_id or agent_token:
        if not agent_id or not agent_token:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "agent_headers_incomplete",
                    "message": "X-Agent-Id and X-Agent-Token required together",
                },
            )
        try:
            grant = require_agent_access(
                service.db,
                agent_id,
                agent_token,
                permission=permission,
                bank=bank,
                resource=resource or permission,
            )
        except AgentAccessError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        ctx = AccessContext(
            kind="agent",
            actor_id=agent_id,
            banks=list(grant.banks),
            permissions=list(grant.permissions),
            grant_id=grant.id,
        )
    else:
        if not allow_node:
            raise HTTPException(
                status_code=401,
                detail={"code": "agent_required", "message": "agent credentials required"},
            )
        token = _extract_bearer(authorization, x_memory_token)
        expected = getattr(app.state, "access_token", "") or ""
        if not expected or not token or not secrets.compare_digest(token, expected):
            raise HTTPException(
                status_code=401,
                detail={"code": "unauthorized", "message": "invalid token"},
            )
        ctx = AccessContext(kind="node", actor_id="node:admin")

    if require_capability_id:
        try:
            require_capability(service.db, require_capability_id)
        except CapabilityLockedError as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "capability_locked",
                    "capability": exc.capability,
                    "message": str(exc),
                },
            ) from exc

    if not ctx.is_node_admin:
        enforce_object_scope(request, ctx, {})
    return ctx


def make_access_dep(
    *,
    permission: str,
    resource: str = "",
    require_capability_id: str | None = None,
):
    async def _dep(
        request: Request,
        authorization: str | None = Header(default=None),
        x_memory_token: str | None = Header(default=None),
        x_agent_id: str | None = Header(default=None),
        x_agent_token: str | None = Header(default=None),
    ) -> AccessContext:
        _ = (authorization, x_memory_token, x_agent_id, x_agent_token)
        access = resolve_access(
            request,
            permission=permission,
            resource=resource or permission,
            require_capability_id=require_capability_id,
        )
        if not access.is_node_admin:
            try:
                body = await request.json()
            except (ValueError, RuntimeError):
                body = {}
            if isinstance(body,dict) and request.method=="POST" and any(
                request.url.path.endswith(part) for part in
                ("/claims/propose","/memories","/experiences","/compat/mem0/add","/compat/mem0/search")
            ):
                body.setdefault("bank_id","personal")
            enforce_object_scope(request, access, body if isinstance(body, dict) else {})
        return access

    return _dep


def enforce_object_scope(request: Request, access: AccessContext, body: dict) -> None:
    """One boundary for object IDs, indirect inputs and requested bank scopes."""
    from cognitive_memory.security.disclosure import opaque_not_found
    allowed = set(access.banks or [])
    db = request.app.state.service.db
    if any(part in str(request.url.path) for part in
           ("/tms/", "/fault/", "/knowledge", "/meta/", "/ab/", "/strategies")):
        raise HTTPException(status_code=403, detail={"code":"node_admin_required"})
    def deny():
        raise HTTPException(status_code=404, detail=opaque_not_found(resource="object"))
    banks = []
    for key in ("bank_id", "bank"):
        if body.get(key) is not None:
            banks.append(str(body[key]))
        if request.query_params.get(key) is not None:
            banks.append(request.query_params[key])
    banks.extend(body.get("banks") or [])
    if any(b not in allowed for b in banks):
        deny()
    table_keys = {
        "claims": ("claim_id", "other_id", "target_id", "replacement_claim_id", "claim_ids"),
        "evidences": ("evidence_id", "evidence_refs", "evidence_ref", "evidence_ids"),
        "memories": ("memory_id", "source_memory_id", "memory_ids"),
        "experiences": ("experience_id", "experience_ids"),
        "strategies": ("strategy_id", "strategy_ids"),
        "knowledge_nodes": ("knowledge_id",),
    }
    values = {**dict(request.path_params), **body}
    # IDs shared by multiple domains are resolved by prefix, then checked in SQL.
    prefixes = {"clm_":"claims", "evd_":"evidences", "mem_":"memories",
                "exp_":"experiences", "str_":"strategies", "knw_":"knowledge_nodes"}
    with db.exclusive() as conn:
        for table, keys in table_keys.items():
            for key in keys:
                val = values.get(key)
                ids = val if isinstance(val, list) else [val] if val else []
                for oid in ids:
                    oid = str(oid)
                    target = next((t for prefix, t in prefixes.items() if oid.startswith(prefix)), table)
                    if key in ("target_id", "other_id", "replacement_claim_id"):
                        target = "claims"
                    if target == "knowledge_nodes":
                        # Knowledge routes currently require node administration below.
                        continue
                    row = conn.execute(f"SELECT bank_id FROM {target} WHERE id=?", (oid,)).fetchone()
                    if row is None or str(row[0]) not in allowed:
                        deny()
                    if target=="evidences":
                        sec=json.loads(conn.execute("SELECT security_json FROM evidences WHERE id=?",(oid,)).fetchone()[0] or "{}")
                        principal=access.to_security_context()
                        if (sec.get("owner_id") and sec["owner_id"]!=principal.owner_id
                                and not set(sec.get("authorized_grants",[])) & set(principal.authorized_grants)):
                            deny()
                        if sec.get("purpose_constraints") and "retrieve" not in sec["purpose_constraints"]:
                            deny()
    # Whole-node graphs/learning operations are not bank-scoped public mutations.
    route = str(request.url.path)
    if any(part in route for part in ("/tms/", "/fault/", "/knowledge", "/meta/", "/ab/")):
        raise HTTPException(status_code=403, detail={"code":"node_admin_required"})


def recheck_write_access(db, access):
    """Called under the mutation transaction, after any request dependency work."""
    if access.is_node_admin:
        return
    from cognitive_memory.identity.grants import get_grant_by_id
    grant=get_grant_by_id(db,access.grant_id) if access.grant_id else None
    if not grant or not grant.is_active() or grant.agent_id!=access.actor_id or "memory.write" not in grant.permissions:
        raise HTTPException(status_code=403,detail={"code":"not_authorized"})
