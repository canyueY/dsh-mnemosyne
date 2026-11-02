# -*- coding: utf-8 -*-
"""冲突保留策略（E2.2）：两侧均保留、可检索；用 contradicts 边连接。"""

from __future__ import annotations

from typing import Any


def record_conflict(
    service: Any,
    memory_id_a: str,
    memory_id_b: str,
    *,
    note: str = "",
    confidence: float | None = 0.7,
) -> dict[str, Any]:
    """记录双向冲突边；不删除、不静默覆盖任一侧。"""
    if not memory_id_a or not memory_id_b or memory_id_a == memory_id_b:
        raise ValueError("conflict requires two distinct memory ids")
    # 校验两侧存在
    a = service.get_memory(memory_id_a)
    b = service.get_memory(memory_id_b)
    edge = service.link_memories(
        from_id=memory_id_a,
        to_id=memory_id_b,
        relation="contradicts",
        note=note or "conflict_retained",
        confidence=confidence,
        metadata={
            "conflict_policy": "retain_both",
            "a_status": a.get("status"),
            "b_status": b.get("status"),
        },
        dry_run=False,
    )
    return {
        "ok": True,
        "policy": "retain_both",
        "memory_ids": [memory_id_a, memory_id_b],
        "relation": edge,
    }


def list_conflicts(service: Any, memory_id: str) -> list[dict[str, Any]]:
    rels = service.list_relations(memory_id, limit=100)
    return [r for r in rels if str(r.get("relation") or "") == "contradicts"]
