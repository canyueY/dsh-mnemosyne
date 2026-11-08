# -*- coding: utf-8 -*-
"""Online Memory Guard: read published snapshots only; never mutate them."""

from __future__ import annotations

from typing import Any

from cognitive_memory.domain.models import Memory, MemoryType
from cognitive_memory.metacognition.store import MetacogStore

ONLINE_TYPES = {MemoryType.WORKING.value, MemoryType.EPISODIC.value, "working", "episodic"}
STABLE_TYPES = {MemoryType.SEMANTIC.value, MemoryType.PROCEDURAL.value, "semantic", "procedural"}


def working_set(store: MetacogStore, *, bank_id: str = "personal") -> dict[str, Any]:
    pack = store.current_all(bank_id)
    memory = pack.get("memory")
    strategy = pack.get("strategy")
    self_model = pack.get("self_model")
    ids: set[str] = set()
    exclusive = False
    if isinstance(memory, dict):
        exclusive = bool(memory.get("exclusive_stable"))
        for item in memory.get("items") or []:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
            elif isinstance(item, str) and item:
                ids.add(item)
    shadow_ids = [
        c["id"]
        for c in store.list_candidates(limit=200)
        if str(c.get("publication_state") or "") == "shadow"
    ]
    published_strategy_ids = []
    if isinstance(strategy, dict):
        for item in strategy.get("items") or []:
            if isinstance(item, dict) and item.get("id"):
                published_strategy_ids.append(str(item["id"]))
    return {
        "ok": True,
        "bank_id": bank_id,
        "memory_snapshot_id": (memory or {}).get("id") if isinstance(memory, dict) else None,
        "strategy_snapshot_id": (strategy or {}).get("id") if isinstance(strategy, dict) else None,
        "self_model_snapshot_id": (self_model or {}).get("id") if isinstance(self_model, dict) else None,
        "stable_ids": ids,
        "exclusive_stable": exclusive,
        "shadow_strategy_ids": shadow_ids,
        "published_strategy_ids": published_strategy_ids,
        "note": "online reads published snapshots; working/episodic stay append-only",
    }


def allow_online_memory(memory: Memory, pack: dict[str, Any]) -> bool:
    mem_type = memory.type.value if hasattr(memory.type, "value") else str(memory.type or "")
    if mem_type in ONLINE_TYPES:
        return True
    if not pack.get("exclusive_stable"):
        return True
    if mem_type not in STABLE_TYPES:
        return True
    return str(memory.id) in set(pack.get("stable_ids") or [])


def filter_online_memories(memories: list[Memory], pack: dict[str, Any]) -> list[Memory]:
    return [m for m in memories if allow_online_memory(m, pack)]


def stamp_experience(body: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    out = dict(body)
    out.setdefault("memory_snapshot_id", pack.get("memory_snapshot_id"))
    out.setdefault("strategy_snapshot_id", pack.get("strategy_snapshot_id"))
    out.setdefault("self_model_snapshot_id", pack.get("self_model_snapshot_id"))
    selected = [str(x) for x in (out.get("selected_strategy_ids") or [])]
    shadow = set(pack.get("shadow_strategy_ids") or [])
    published = set(pack.get("published_strategy_ids") or [])
    kept = []
    for sid in selected:
        if sid in shadow:
            continue
        if published and sid not in published:
            continue
        kept.append(sid)
    out["selected_strategy_ids"] = kept
    return out
