# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PrincipalKind(str, Enum):
    USER = "user"
    NODE = "node"
    AGENT = "agent"
    EXTERNAL = "external"


class Principal(BaseModel):
    id: str
    kind: PrincipalKind
    display_name: str = ""
    external_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class PrincipalUpsert(BaseModel):
    id: str | None = None
    kind: PrincipalKind
    display_name: str = ""
    external_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
