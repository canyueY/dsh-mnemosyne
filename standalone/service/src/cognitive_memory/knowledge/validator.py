# -*- coding: utf-8 -*-
"""Knowledge validator — structural only; never Verified Claims."""

from __future__ import annotations

from typing import Any

from cognitive_memory.knowledge.models import KnowledgeKind, KnowledgeStatus


def validate_knowledge(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    kind = str(payload.get("kind") or "").strip()
    name = str(payload.get("name") or "").strip()
    status = str(payload.get("status") or KnowledgeStatus.PROPOSED.value).strip()

    if kind not in {k.value for k in KnowledgeKind}:
        errors.append("invalid_kind")
    if not name:
        errors.append("name_required")
    if status not in {s.value for s in KnowledgeStatus}:
        errors.append("invalid_status")

    claim_ids = payload.get("claim_ids") if isinstance(payload.get("claim_ids"), list) else []
    if kind == KnowledgeKind.CLAIM.value and not claim_ids:
        errors.append("claim_knowledge_requires_claim_ids")
    if kind == KnowledgeKind.STRATEGY.value and not str(payload.get("strategy_id") or "").strip():
        errors.append("strategy_knowledge_requires_strategy_id")
    if kind == KnowledgeKind.STRUCTURAL.value and not (
        str(payload.get("schema_id") or "").strip() or payload.get("payload")
    ):
        errors.append("structural_knowledge_requires_schema_or_payload")

    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "authoritative": False,
        "note": "validation_does_not_verify_claims",
        "kind": kind,
        "status": status,
    }
