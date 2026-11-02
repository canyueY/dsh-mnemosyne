# -*- coding: utf-8 -*-
"""Observation 入站归一化：附着 Identity，不自动升格长期 Memory。"""

from __future__ import annotations

from typing import Any

from cognitive_memory.domain.models import ObservationIn, SourceKind


DEFAULT_AGENT_BY_PRODUCER: dict[str, str] = {
    "dsh": "agent:dsh",
    "murasame.chat": "agent:dsh",
    "murasame.video": "agent:dsh",
    "cognitive-memory": "agent:standalone",
    "migrate.amem": "agent:standalone",
    "backup-restore-drill": "agent:standalone",
    "test": "agent:standalone",
}


def resolve_agent_id(
    *,
    explicit: str | None,
    producer: str,
    metadata: dict[str, Any] | None,
) -> str:
    meta = metadata or {}
    for key in ("agent_id", "principal_id", "actor_id"):
        raw = meta.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if explicit and explicit.strip():
        return explicit.strip()
    mapped = DEFAULT_AGENT_BY_PRODUCER.get((producer or "").strip())
    if mapped:
        return mapped
    return "agent:standalone"


def normalize_observation(
    body: ObservationIn,
    *,
    node_id: str = "local-node",
    subject_id: str | None = None,
) -> ObservationIn:
    """返回带 identity 元数据的 ObservationIn（不落库）。"""
    meta = dict(body.metadata or {})
    agent_id = resolve_agent_id(
        explicit=getattr(body, "agent_id", None),
        producer=body.producer,
        metadata=meta,
    )
    subj = (
        (getattr(body, "subject_id", None) or "").strip()
        or (subject_id or "").strip()
        or str(meta.get("subject_id") or meta.get("user_id") or "user:local").strip()
        or "user:local"
    )
    nid = (
        (getattr(body, "node_id", None) or "").strip()
        or str(meta.get("node_id") or node_id).strip()
        or node_id
    )
    identity = {
        "node_id": nid,
        "agent_id": agent_id,
        "subject_id": subj,
        "producer": body.producer,
        "source_kind": body.source_kind.value
        if isinstance(body.source_kind, SourceKind)
        else str(body.source_kind),
    }
    meta["identity"] = identity
    meta.setdefault("agent_id", agent_id)
    meta.setdefault("subject_id", subj)
    meta.setdefault("node_id", nid)

    data = body.model_dump()
    data["metadata"] = meta
    data["agent_id"] = agent_id
    data["subject_id"] = subj
    data["node_id"] = nid
    return ObservationIn.model_validate(data)
