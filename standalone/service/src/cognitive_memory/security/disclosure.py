# -*- coding: utf-8 -*-
"""P8 inference-leak guards: deny paths must not disclose Claim text / existence."""

from __future__ import annotations

from typing import Any

from cognitive_memory.security.context import SecurityContext
from cognitive_memory.visibility.models import VisibilityDecision
from cognitive_memory.visibility.service import evaluate_visibility

# Uniform opaque body for missing OR denied (existence-safe).
OPAQUE_NOT_FOUND: dict[str, Any] = {
    "code": "not_found",
    "message": "resource_unavailable",
}


def opaque_not_found(*, resource: str = "resource") -> dict[str, Any]:
    """Return a body that never encodes whether the object exists."""
    return {"code": "not_found", "message": "resource_unavailable", "resource": resource}


def claim_visible_to_principal(
    principal: SecurityContext,
    *,
    claim_id: str,
    bank_id: str,
    evidence_visible: bool = True,
) -> dict[str, Any]:
    """Pre-retrieval visibility gate. DENY → existence_disclosed=False."""
    vis = evaluate_visibility(
        principal,
        object_ref=claim_id,
        object_bank=bank_id or "personal",
        requested_fields=[
            "claim_id",
            "lifecycle_status",
            "ast_version",
            "ast",
            "content",
            "verification_status",
            "evidence_refs",
        ],
        evidence_visible=evidence_visible,
    )
    return {
        "decision": vis.decision.value,
        "existence_disclosed": bool(vis.existence_disclosed),
        "visible_fields": list(vis.visible_fields),
        "denied_fields": list(vis.denied_fields),
        "reason_codes": list(vis.reason_codes),
        "allow_retrieve": vis.decision != VisibilityDecision.DENY,
    }


def redact_claim_for_visibility(
    pack: dict[str, Any],
    *,
    visible_fields: list[str],
    denied_fields: list[str],
) -> dict[str, Any]:
    """Strip denied fields so PARTIAL responses cannot leak verification/content."""
    out = dict(pack)
    field_map = {
        "ast": "ast",
        "content": "normalized_text",
        "verification_status": "verification_status",
        "lifecycle_status": "lifecycle",
        "ast_version": "ast_version",
        "claim_id": "id",
        "evidence_refs": "_evidence_refs",
    }
    for f in denied_fields:
        key = field_map.get(f, f)
        out.pop(key, None)
        if f == "evidence_refs":
            out.pop("evidence", None)
        if f == "content":
            out.pop("normalized_text", None)
            out.pop("raw_text", None)
            out.pop("text", None)
        if f == "ast":
            out.pop("ast", None)
            out.pop("ast_json", None)
    if "claim_id" in visible_fields or "id" in visible_fields:
        out["id"] = pack.get("id")
    return out


def response_leaks_secret(body: Any, secret: str) -> bool:
    """True if serialized response contains the secret Claim text (inference leak)."""
    if not secret:
        return False
    needle = str(secret).strip()
    if not needle:
        return False
    blob = str(body)
    return needle in blob


def assert_no_existence_signal(vis_pack: dict[str, Any]) -> bool:
    """DENY must keep existence_disclosed=False."""
    if vis_pack.get("decision") != VisibilityDecision.DENY.value:
        return True
    return vis_pack.get("existence_disclosed") is False
