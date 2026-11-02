# -*- coding: utf-8 -*-
"""Claim AST v1 — deterministic, JSON-serializable structures.

Supported shapes (P2 scope starts here; P0.5 prototypes the same):

    Atom | Negation | Temporal | Conditional | Unresolved
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AST_VERSION = "claim.ast.v1"


class TemporalInterval(BaseModel):
    """Half-open interval [start, end). ISO-8601 strings or empty."""

    start: str | None = None
    end: str | None = None

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        # Normalize empty strings to None
        if self.start == "":
            object.__setattr__(self, "start", None)
        if self.end == "":
            object.__setattr__(self, "end", None)


class Atom(BaseModel):
    kind: Literal["atom"] = "atom"
    subject: str
    predicate: str
    arguments: list[str] = Field(default_factory=list)
    modality: str | None = None  # e.g. preference | fact | unknown


class Negation(BaseModel):
    kind: Literal["negation"] = "negation"
    target: "ClaimNode"


class Temporal(BaseModel):
    kind: Literal["temporal"] = "temporal"
    interval: TemporalInterval
    target: "ClaimNode"


class Conditional(BaseModel):
    kind: Literal["conditional"] = "conditional"
    condition: "ClaimNode"
    consequence: "ClaimNode"


class Unresolved(BaseModel):
    kind: Literal["unresolved"] = "unresolved"
    raw_text: str
    missing_fields: list[str] = Field(default_factory=list)
    parse_attempts: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


ClaimNode = Atom | Negation | Temporal | Conditional | Unresolved


class ClaimAST(BaseModel):
    """Top-level Claim AST envelope."""

    ast_version: str = AST_VERSION
    root: ClaimNode
    scope: dict[str, Any] = Field(default_factory=dict)
    # Optional surface text retained for audit (not used in same_as)
    surface: str = ""

    def is_unresolved(self) -> bool:
        return isinstance(self.root, Unresolved) or self.root.kind == "unresolved"


# Rebuild forward refs for pydantic v2
Negation.model_rebuild()
Temporal.model_rebuild()
Conditional.model_rebuild()
ClaimAST.model_rebuild()
