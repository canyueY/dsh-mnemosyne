# -*- coding: utf-8 -*-
"""单 Node Identity：主体登记 + 多 Agent 授权/撤销/审计。"""

from cognitive_memory.identity.grants import (
    AgentAccessError,
    AgentGrant,
    authenticate_agent,
    issue_grant,
    list_audit,
    list_grants,
    register_agent,
    require_agent_access,
    revoke_grant,
)
from cognitive_memory.identity.models import Principal, PrincipalKind, PrincipalUpsert
from cognitive_memory.identity.store import (
    ensure_default_principals,
    get_principal,
    list_principals,
    upsert_principal,
)

__all__ = [
    "AgentAccessError",
    "AgentGrant",
    "Principal",
    "PrincipalKind",
    "PrincipalUpsert",
    "authenticate_agent",
    "ensure_default_principals",
    "get_principal",
    "issue_grant",
    "list_audit",
    "list_grants",
    "list_principals",
    "register_agent",
    "require_agent_access",
    "revoke_grant",
    "upsert_principal",
]
