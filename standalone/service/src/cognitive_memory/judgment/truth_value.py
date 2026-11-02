# -*- coding: utf-8 -*-
"""Strong Kleene three-valued logic."""

from __future__ import annotations

from enum import Enum


class TruthValue(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def tv_and(a: TruthValue, b: TruthValue) -> TruthValue:
    if a is TruthValue.FALSE or b is TruthValue.FALSE:
        return TruthValue.FALSE
    if a is TruthValue.TRUE and b is TruthValue.TRUE:
        return TruthValue.TRUE
    return TruthValue.UNKNOWN


def tv_or(a: TruthValue, b: TruthValue) -> TruthValue:
    if a is TruthValue.TRUE or b is TruthValue.TRUE:
        return TruthValue.TRUE
    if a is TruthValue.FALSE and b is TruthValue.FALSE:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN


def tv_not(a: TruthValue) -> TruthValue:
    if a is TruthValue.TRUE:
        return TruthValue.FALSE
    if a is TruthValue.FALSE:
        return TruthValue.TRUE
    return TruthValue.UNKNOWN
