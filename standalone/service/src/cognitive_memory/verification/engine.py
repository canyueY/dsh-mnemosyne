# -*- coding: utf-8 -*-
"""VerificationEngine — sole authority to submit Verified (P1 in-memory)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.verification.models import (
    VerificationRecord,
    VerificationResult,
    VerificationStatus,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class VerificationEngine:
    """In-memory engine for P1 contract tests; P2 will persist records."""

    def __init__(self) -> None:
        self._records: dict[str, VerificationRecord] = {}
        self._by_claim: dict[str, list[str]] = {}

    def assess(
        self,
        *,
        claim_id: str,
        evidence_refs: list[str] | None = None,
        counterevidence_refs: list[str] | None = None,
        evidence_valid: bool = True,
        authorized: bool = True,
        claim_version: int = 1,
        policy_version: str = "verification.v1",
    ) -> VerificationResult:
        if not authorized:
            return VerificationResult(
                status=VerificationStatus.NOT_VERIFIED,
                reason_codes=["not_authorized"],
                policy_version=policy_version,
            )
        evidence_refs = list(evidence_refs or [])
        counterevidence_refs = list(counterevidence_refs or [])
        if counterevidence_refs:
            return VerificationResult(
                status=VerificationStatus.DISPUTED,
                evidence_refs=evidence_refs,
                counterevidence_refs=counterevidence_refs,
                reason_codes=["counterevidence_present"],
                policy_version=policy_version,
                verification_scope={"claim_id": claim_id, "claim_version": claim_version},
            )
        if not evidence_refs or not evidence_valid:
            return VerificationResult(
                status=VerificationStatus.INSUFFICIENT,
                evidence_refs=evidence_refs,
                reason_codes=["insufficient_or_invalid_evidence"],
                policy_version=policy_version,
                verification_scope={"claim_id": claim_id, "claim_version": claim_version},
            )
        return VerificationResult(
            status=VerificationStatus.NOT_VERIFIED,
            evidence_refs=evidence_refs,
            reason_codes=["assessment_ready_awaiting_submit"],
            policy_version=policy_version,
            verification_scope={"claim_id": claim_id, "claim_version": claim_version},
        )

    def submit_verified(
        self,
        *,
        claim_id: str,
        evidence_refs: list[str],
        actor: str,
        authorized: bool = True,
        evidence_still_valid: bool = True,
        claim_version: int = 1,
        counterevidence_refs: list[str] | None = None,
        policy_version: str = "verification.v1",
        valid_until: str | None = None,
        security_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Transactional-style re-check at submit time (anti-TOCTOU)."""
        if not authorized:
            return {"ok": False, "error": "not_authorized", "likes_written": False}
        if counterevidence_refs:
            return {"ok": False, "error": "disputed", "status": VerificationStatus.DISPUTED.value}
        if not evidence_refs:
            return {"ok": False, "error": "verified_requires_evidence_refs"}
        if not evidence_still_valid:
            return {
                "ok": False,
                "error": "evidence_revoked_before_commit",
                "status": VerificationStatus.INSUFFICIENT.value,
            }
        now = _now()
        rid = f"ver_{secrets.token_hex(8)}"
        record = VerificationRecord(
            record_id=rid,
            claim_id=claim_id,
            claim_version=claim_version,
            status=VerificationStatus.VERIFIED,
            evidence_refs=list(evidence_refs),
            counterevidence_refs=[],
            policy_version=policy_version,
            actor=actor,
            verified_at=now,
            valid_until=valid_until,
            reason_codes=["engine_submit"],
            security_context=dict(security_context or {}),
        )
        self._records[rid] = record
        self._by_claim.setdefault(claim_id, []).append(rid)
        return {
            "ok": True,
            "record": record.model_dump(mode="json"),
            "result": VerificationResult(
                status=VerificationStatus.VERIFIED,
                evidence_refs=list(evidence_refs),
                policy_version=policy_version,
                verified_at=now,
                valid_until=valid_until,
                reason_codes=["verified"],
                verification_scope={"claim_id": claim_id, "claim_version": claim_version},
            ).model_dump(mode="json"),
        }

    def get_latest(self, claim_id: str) -> VerificationRecord | None:
        ids = self._by_claim.get(claim_id) or []
        if not ids:
            return None
        return self._records.get(ids[-1])

    def mark_expired(self, record_id: str) -> VerificationRecord | None:
        rec = self._records.get(record_id)
        if not rec:
            return None
        updated = rec.model_copy(update={"status": VerificationStatus.EXPIRED})
        self._records[record_id] = updated
        return updated

    @staticmethod
    def lifecycle_from_layers(
        *,
        parse_status: str,
        evidence_status: str,
        verification_status: str,
    ) -> str:
        """Map parse/evidence/verification → lifecycle (P1 table)."""
        if verification_status == VerificationStatus.DISPUTED.value or evidence_status == "contradicted":
            return "disputed"
        if verification_status == VerificationStatus.EXPIRED.value:
            return "expired"
        if verification_status == "superseded":
            return "superseded"
        if parse_status == "unresolved":
            return "proposed"
        if evidence_status == "insufficient" or verification_status in (
            VerificationStatus.NOT_VERIFIED.value,
            VerificationStatus.INSUFFICIENT.value,
        ):
            if evidence_status == "supported" and verification_status == VerificationStatus.NOT_VERIFIED.value:
                return "supported"
            return "proposed"
        if (
            parse_status == "parsed"
            and evidence_status == "supported"
            and verification_status == VerificationStatus.VERIFIED.value
        ):
            return "verified"
        return "proposed"
