# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceKind(str, Enum):
    OBSERVATION = "observation"
    FEEDBACK = "feedback"
    IMPORT = "import"
    SYSTEM = "system"


class ValidationState(str, Enum):
    UNVERIFIED = "unverified"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    REVOKED = "revoked"


class EvidenceAttachIn(BaseModel):
    memory_id: str
    observation_id: str
    relation: str = "supports"
    note: str = ""
    confidence: float | None = None
    evidence_kind: EvidenceKind = EvidenceKind.OBSERVATION
    validation_state: ValidationState = ValidationState.UNVERIFIED
    producer: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(BaseModel):
    memory_id: str
    observation_id: str
    relation: str
    note: str = ""
    created_at: str
    confidence: float | None = None
    evidence_kind: str = EvidenceKind.OBSERVATION.value
    validation_state: str = ValidationState.UNVERIFIED.value
    producer: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
