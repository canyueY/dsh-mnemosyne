# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JudgmentTrace(BaseModel):
    trace_id: str
    judgment_type: str
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    rule_version: str = ""
    algorithm_version: str = ""
    model_version: str = ""
    prompt_version: str = ""
    config_version: str = ""
    uncertainty: str = "unknown"
    reason_codes: list[str] = Field(default_factory=list)
    duration_ms: float | None = None
    cost: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = ""
    trace_completeness: str = "complete"  # complete | partial | failed
    governance_critical: bool = False
