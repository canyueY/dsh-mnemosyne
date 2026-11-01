# -*- coding: utf-8 -*-
"""统一 Observation 入站：外部输入必须先经此管道，不得直接写长期 ACTIVE。"""

from __future__ import annotations

from typing import Any

from cognitive_memory.domain.models import Observation, ObservationIn
from cognitive_memory.ingestion.normalize import normalize_observation


def ingest_observation(
    service: Any,
    body: ObservationIn,
    *,
    node_id: str = "local-node",
    subject_id: str | None = None,
) -> Observation:
    """归一化 Identity 后委托 MemoryService.capture_observation。"""
    from cognitive_memory.governance.workspace import assert_workspace_authorized

    normalized = normalize_observation(body, node_id=node_id, subject_id=subject_id)
    assert_workspace_authorized(
        service.db, dict(normalized.metadata or {}), action="ingest"
    )
    return service.capture_observation(normalized)


def coerce_long_term_status(
    *,
    memory_type: str,
    status: str,
    source_ids: list[str] | None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """非 WORKING 且声称 ACTIVE、又无 Observation 证据时，强制降为 candidate。

    Working 记忆允许 ACTIVE（对话快通道）。长期类型必须有 source_ids 证据链。
    """
    meta = dict(metadata or {})
    mtype = (memory_type or "").lower()
    st = (status or "").lower()
    if mtype == "working":
        return status, meta
    if st != "active":
        return status, meta
    if meta.get("epistemic") in {"inference", "inferred_signal", "summary", "generated"}:
        meta["promotion_guard"] = {"coerced_from": "active", "reason": "hypothesis_not_observation"}
        return "candidate", meta
    sources = [s for s in (source_ids or []) if s]
    if sources:
        return status, meta
    meta["promotion_guard"] = {
        "coerced_from": "active",
        "reason": "long_term_active_requires_observation_source_ids",
    }
    return "candidate", meta
