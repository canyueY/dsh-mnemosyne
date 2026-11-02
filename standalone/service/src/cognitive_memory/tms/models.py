# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BeliefStatus(str, Enum):
    IN = "in"
    OUT = "out"
    UNKNOWN = "unknown"


class Justification(BaseModel):
    """AND-justification: all premises must hold for this support to be valid."""

    justification_id: str
    conclusion_id: str
    premises: list[str] = Field(default_factory=list)
    kind: str = "support"  # support | contradict | depends
    valid: bool = True
    reason: str = ""
    actor: str = ""
    policy_version: str = "tms.v1"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Belief(BaseModel):
    node_id: str
    status: BeliefStatus = BeliefStatus.OUT
    supporting_justification_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class PropagateResult(BaseModel):
    ok: bool
    changed: list[dict[str, Any]] = Field(default_factory=list)
    visited: int = 0
    budget_exhausted: bool = False
    scc_unknown: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
