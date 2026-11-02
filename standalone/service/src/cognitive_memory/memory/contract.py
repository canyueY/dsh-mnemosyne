# -*- coding: utf-8 -*-
"""Memory Service Contract（稳定对外操作面；Adapter 不得直连 SQLite）。"""

from __future__ import annotations

# 对外稳定操作名（文档/契约测试锚点）
PUBLIC_OPERATIONS: tuple[str, ...] = (
    "capture_observation",
    "ingest_observation",
    "propose_memory",
    "update_memory",
    "forget_memory",
    "get_memory",
    "search",
    "build_context",
    "attach_evidence",
    "list_evidence",
    "list_banks",
    "get_authority",
    "transition_authority",
    "health",
)


def contract_snapshot() -> dict:
    from cognitive_memory.events.contract import event_contract_snapshot

    return {
        "version": "2.0-phase3",
        "operations": list(PUBLIC_OPERATIONS),
        "storage_access": "service_only",
        "adapter_may_bypass_service": False,
        "sdk": "cognitive_memory.sdk.MnemosyneClient",
        "events": event_contract_snapshot(),
    }
