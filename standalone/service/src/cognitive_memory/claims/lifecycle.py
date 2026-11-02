# -*- coding: utf-8 -*-
"""Claim lifecycle transitions — single gate (P1 rules, in-memory)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClaimLifecycle(str, Enum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    QUALIFIED = "qualified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    RETRACTED = "retracted"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


# Allowed edges (from → to). disputed preferred over supported when both argued.
_ALLOWED: dict[ClaimLifecycle, set[ClaimLifecycle]] = {
    ClaimLifecycle.PROPOSED: {
        ClaimLifecycle.SUPPORTED,
        ClaimLifecycle.QUALIFIED,
        ClaimLifecycle.DISPUTED,
        ClaimLifecycle.REJECTED,
        ClaimLifecycle.RETRACTED,
        ClaimLifecycle.SUPERSEDED,
        ClaimLifecycle.EXPIRED,
    },
    ClaimLifecycle.SUPPORTED: {
        ClaimLifecycle.VERIFIED,
        ClaimLifecycle.QUALIFIED,
        ClaimLifecycle.DISPUTED,
        ClaimLifecycle.EXPIRED,
        ClaimLifecycle.SUPERSEDED,
        ClaimLifecycle.REJECTED,
        ClaimLifecycle.RETRACTED,
    },
    ClaimLifecycle.QUALIFIED: {
        ClaimLifecycle.SUPPORTED,
        ClaimLifecycle.VERIFIED,
        ClaimLifecycle.DISPUTED,
        ClaimLifecycle.EXPIRED,
        ClaimLifecycle.SUPERSEDED,
        ClaimLifecycle.RETRACTED,
    },
    ClaimLifecycle.VERIFIED: {
        ClaimLifecycle.DISPUTED,
        ClaimLifecycle.EXPIRED,
        ClaimLifecycle.SUPERSEDED,
        ClaimLifecycle.RETRACTED,
        ClaimLifecycle.SUPPORTED,  # revalidate downgrade
    },
    ClaimLifecycle.DISPUTED: {
        ClaimLifecycle.SUPPORTED,
        ClaimLifecycle.REJECTED,
        ClaimLifecycle.SUPERSEDED,
        ClaimLifecycle.EXPIRED,
        ClaimLifecycle.RETRACTED,
    },
    ClaimLifecycle.EXPIRED: {
        ClaimLifecycle.SUPPORTED,
        ClaimLifecycle.VERIFIED,  # after revalidation
        ClaimLifecycle.SUPERSEDED,
        ClaimLifecycle.REJECTED,
        ClaimLifecycle.RETRACTED,
    },
    ClaimLifecycle.REJECTED: {ClaimLifecycle.SUPERSEDED, ClaimLifecycle.RETRACTED},
    ClaimLifecycle.RETRACTED: {ClaimLifecycle.SUPERSEDED},
    ClaimLifecycle.SUPERSEDED: set(),
}


class TransitionResult(BaseModel):
    ok: bool
    claim_id: str
    from_status: ClaimLifecycle
    to_status: ClaimLifecycle | None = None
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    policy_version: str = "claim.lifecycle.v1"
    actor: str = ""
    error: str | None = None
    history_entry: dict[str, Any] | None = None


class ClaimLifecycleEngine:
    def __init__(self) -> None:
        self._status: dict[str, ClaimLifecycle] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}

    def get(self, claim_id: str) -> ClaimLifecycle:
        return self._status.get(claim_id, ClaimLifecycle.PROPOSED)

    def transition_claim(
        self,
        claim_id: str,
        target_status: ClaimLifecycle | str,
        *,
        reason: str,
        evidence_refs: list[str] | None = None,
        policy_version: str = "claim.lifecycle.v1",
        actor: str = "",
        authorized: bool = True,
    ) -> TransitionResult:
        if not authorized:
            return TransitionResult(
                ok=False,
                claim_id=claim_id,
                from_status=self.get(claim_id),
                error="not_authorized",
                reason=reason,
                actor=actor,
            )
        if isinstance(target_status, ClaimLifecycle):
            target = target_status
        else:
            target = ClaimLifecycle(str(target_status))
        current = self.get(claim_id)
        if current is target:
            return TransitionResult(
                ok=True,
                claim_id=claim_id,
                from_status=current,
                to_status=target,
                reason=reason or "idempotent",
                evidence_refs=list(evidence_refs or []),
                policy_version=policy_version,
                actor=actor,
                history_entry=None,
            )
        if target is ClaimLifecycle.VERIFIED and not evidence_refs:
            return TransitionResult(
                ok=False,
                claim_id=claim_id,
                from_status=current,
                error="verified_requires_evidence_and_record",
                reason=reason,
                actor=actor,
            )
        allowed = _ALLOWED.get(current, set())
        if target not in allowed:
            return TransitionResult(
                ok=False,
                claim_id=claim_id,
                from_status=current,
                error="illegal_transition",
                reason=reason,
                actor=actor,
            )
        # disputed preferred: if moving to supported while disputed — blocked unless explicit
        entry = {
            "from": current.value,
            "to": target.value,
            "reason": reason,
            "evidence_refs": list(evidence_refs or []),
            "policy_version": policy_version,
            "actor": actor,
        }
        self._status[claim_id] = target
        self._history.setdefault(claim_id, []).append(entry)
        return TransitionResult(
            ok=True,
            claim_id=claim_id,
            from_status=current,
            to_status=target,
            reason=reason,
            evidence_refs=list(evidence_refs or []),
            policy_version=policy_version,
            actor=actor,
            history_entry=entry,
        )

    def history(self, claim_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(claim_id) or [])
