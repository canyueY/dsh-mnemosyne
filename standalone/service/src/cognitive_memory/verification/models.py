# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    INSUFFICIENT = "insufficient"
    DISPUTED = "disputed"
    EXPIRED = "expired"
    REVALIDATE_REQUIRED = "revalidate_required"


class VerificationResult(BaseModel):
    status: VerificationStatus
    verification_scope: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    counterevidence_refs: list[str] = Field(default_factory=list)
    independence_result: dict[str, Any] = Field(default_factory=dict)
    policy_version: str = "verification.v1"
    verified_at: str | None = None
    valid_until: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class VerificationRecord(BaseModel):
    """Only VerificationEngine may create records that authorize Verified status."""

    record_id: str
    claim_id: str
    claim_version: int = 1
    status: VerificationStatus
    evidence_refs: list[str] = Field(default_factory=list)
    counterevidence_refs: list[str] = Field(default_factory=list)
    policy_version: str = "verification.v1"
    actor: str = ""
    verified_at: str | None = None
    valid_until: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    security_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _verified_needs_evidence(self) -> "VerificationRecord":
        if self.status is VerificationStatus.VERIFIED and not self.evidence_refs:
            raise ValueError("verified_requires_evidence_refs")
        if self.status is VerificationStatus.VERIFIED and not self.verified_at:
            raise ValueError("verified_requires_verified_at")
        return self
