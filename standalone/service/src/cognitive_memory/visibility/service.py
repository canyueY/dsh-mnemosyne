# -*- coding: utf-8 -*-
"""Deterministic Visibility evaluation (P1) — fail closed, pre-retrieval."""

from __future__ import annotations

from typing import Any, Iterable

from cognitive_memory.security.context import SecurityContext
from cognitive_memory.visibility.models import VisibilityDecision, VisibilityResult

# Controlled disclosure units for Claim
CLAIM_SENSITIVE_FIELDS = frozenset(
    {"verification_status", "evidence_refs", "justifications", "ast", "content"}
)
DEFAULT_PUBLIC_FIELDS = frozenset({"claim_id", "lifecycle_status", "ast_version"})


def evaluate_visibility(
    principal: SecurityContext,
    *,
    object_ref: str,
    object_bank: str = "personal",
    purpose: str = "retrieve",
    requested_fields: Iterable[str] | None = None,
    evidence_visible: bool = True,
    object_policy_allowed: bool = True,
    field_whitelist: Iterable[str] | None = None,
) -> VisibilityResult:
    """Permission-before-retrieval. Unknown → deny disclosure (fail closed)."""
    reasons: list[str] = []
    req_fields = [str(x) for x in (requested_fields or list(CLAIM_SENSITIVE_FIELDS | DEFAULT_PUBLIC_FIELDS))]

    if not principal.allows_bank(object_bank):
        return VisibilityResult(
            decision=VisibilityDecision.DENY,
            denied_fields=req_fields,
            reason_codes=["cross_bank_denied"],
            existence_disclosed=False,
        )
    if not principal.allows_purpose(purpose):
        return VisibilityResult(
            decision=VisibilityDecision.DENY,
            denied_fields=req_fields,
            reason_codes=["purpose_denied"],
            existence_disclosed=False,
        )
    if not object_policy_allowed:
        return VisibilityResult(
            decision=VisibilityDecision.DENY,
            denied_fields=req_fields,
            reason_codes=["object_policy_denied"],
            existence_disclosed=False,
        )

    whitelist = set(DEFAULT_PUBLIC_FIELDS | CLAIM_SENSITIVE_FIELDS) if field_whitelist is None else set(field_whitelist)
    visible: list[str] = []
    denied: list[str] = []
    for f in req_fields:
        if f not in whitelist:
            denied.append(f)
            reasons.append(f"field_not_whitelisted:{f}")
            continue
        if f in ("verification_status", "lifecycle_status", "evidence_refs", "justifications") and not evidence_visible:
            # Claim body may be visible; Verified mark must not leak without Evidence
            denied.append(f)
            reasons.append("evidence_invisible_blocks_verification_disclosure")
            continue
        visible.append(f)

    if not visible and denied:
        return VisibilityResult(
            decision=VisibilityDecision.DENY,
            denied_fields=denied,
            reason_codes=sorted(set(reasons)) or ["no_visible_fields"],
            existence_disclosed=False,
        )
    if denied:
        return VisibilityResult(
            decision=VisibilityDecision.PARTIAL,
            visible_fields=visible,
            denied_fields=denied,
            usable_justifications=[],
            reason_codes=sorted(set(reasons)),
            existence_disclosed=True,
        )
    return VisibilityResult(
        decision=VisibilityDecision.ALLOW,
        visible_fields=visible,
        denied_fields=[],
        usable_justifications=["principal_authorized"],
        reason_codes=["ok"],
        existence_disclosed=True,
        metadata={"object_ref": object_ref},
    )


def filter_and_justifications(
    justifications: list[dict[str, Any]],
    *,
    principal_can_see_all_and_parts: bool,
) -> list[dict[str, Any]]:
    """AND justifications require full authorization; never stitch partial ANDs."""
    if not principal_can_see_all_and_parts:
        return []
    usable = []
    for j in justifications:
        parts = j.get("and_parts") or j.get("required") or []
        if parts and not all(bool(p.get("authorized")) for p in parts if isinstance(p, dict)):
            continue
        usable.append(j)
    return usable
