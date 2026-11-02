# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum


class KnowledgeKind(str, Enum):
    CLAIM = "claim_knowledge"
    RULE = "rule_knowledge"
    STRUCTURAL = "structural_knowledge"
    PATTERN = "pattern_knowledge"
    STRATEGY = "strategy_knowledge"


class KnowledgeStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    ACTIVE = "active"
    RETIRED = "retired"
