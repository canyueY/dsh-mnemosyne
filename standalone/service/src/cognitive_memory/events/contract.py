# -*- coding: utf-8 -*-
"""对外合同对外契约（E3.4）：版本化事件类型。"""

from __future__ import annotations

from typing import Any

EVENT_CONTRACT_VERSION = 1

# 稳定对外事件类型（生产方可订阅/审计）
PUBLIC_EVENT_TYPES: dict[str, dict[str, Any]] = {
    "observation.captured": {"version": 1, "payload": ["observation_id", "source_kind"]},
    "memory.candidate_created": {"version": 1, "payload": ["memory_id", "type", "status"]},
    "memory.activated": {"version": 1, "payload": ["memory_id", "type", "status"]},
    "memory.updated": {"version": 1, "payload": ["memory_id", "version"]},
    "memory.invalidated": {"version": 1, "payload": ["memory_id"]},
    "memory.feedback_received": {"version": 1, "payload": ["memory_id"]},
    "memory.relation_created": {"version": 1, "payload": ["from_id", "to_id", "relation"]},
}


def event_contract_snapshot() -> dict[str, Any]:
    return {
        "contract_version": EVENT_CONTRACT_VERSION,
        "events": dict(PUBLIC_EVENT_TYPES),
    }


def validate_event_type(event_type: str) -> bool:
    return event_type in PUBLIC_EVENT_TYPES
