# -*- coding: utf-8 -*-
"""治理：Capability Gate / Access 执法 / Authority Cutover / 审计（不可由 Agent 自授权）。"""

from cognitive_memory.governance.access import AccessContext, resolve_access
from cognitive_memory.governance.capability_gate import (
    CAPABILITIES,
    CapabilityLockedError,
    check_capability,
    ensure_default_capabilities,
    list_capabilities,
    require_capability,
    set_capability_level,
)
from cognitive_memory.governance.cutover import execute_cutover, preview_cutover
from cognitive_memory.governance.policy import (
    PolicyDeniedError,
    assert_allow_forward,
    assert_allow_store,
    assert_allow_train,
    extract_policy,
    filter_forwardable_memories,
)

__all__ = [
    "AccessContext",
    "CAPABILITIES",
    "CapabilityLockedError",
    "PolicyDeniedError",
    "assert_allow_forward",
    "assert_allow_store",
    "assert_allow_train",
    "check_capability",
    "ensure_default_capabilities",
    "execute_cutover",
    "extract_policy",
    "filter_forwardable_memories",
    "list_capabilities",
    "preview_cutover",
    "require_capability",
    "resolve_access",
    "set_capability_level",
]
