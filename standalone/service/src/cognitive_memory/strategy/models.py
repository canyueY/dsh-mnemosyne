# -*- coding: utf-8 -*-
"""P5 Strategy domain enums — no auto-Verified / no silent ACTIVE."""

from __future__ import annotations

from enum import Enum


class StrategyStatus(str, Enum):
    PROPOSED = "proposed"
    EXPERIMENTAL = "experimental"
    ACTIVE = "active"
    RETIRED = "retired"
