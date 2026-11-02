# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VisibilityDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class VisibilityResult(BaseModel):
    decision: VisibilityDecision
    visible_fields: list[str] = Field(default_factory=list)
    denied_fields: list[str] = Field(default_factory=list)
    usable_justifications: list[str] = Field(default_factory=list)
    policy_version: str = "visibility.v1"
    reason_codes: list[str] = Field(default_factory=list)
    # Fail-closed: never reveal whether a denied object exists
    existence_disclosed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
