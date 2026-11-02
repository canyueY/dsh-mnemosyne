# -*- coding: utf-8 -*-
"""SecurityContext — object-level authorization envelope (P1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SecurityContext(BaseModel):
    tenant_id: str = ""
    bank_id: str = "personal"
    owner_id: str = ""
    agent_id: str = ""
    data_classification: str = "internal"  # not an ordered privilege ladder
    access_scope: list[str] = Field(default_factory=list)
    purpose_constraints: list[str] = Field(default_factory=list)
    authorized_grants: list[str] = Field(default_factory=list)
    retention_policy: str = "default"

    def allows_bank(self, requested_bank: str) -> bool:
        """Cross-bank default deny unless bank matches or grant lists it."""
        req = (requested_bank or "personal").strip() or "personal"
        if req == (self.bank_id or "personal"):
            return True
        return f"bank:{req}" in (self.authorized_grants or [])

    def allows_purpose(self, purpose: str) -> bool:
        if not self.purpose_constraints:
            return True
        return purpose in self.purpose_constraints

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
