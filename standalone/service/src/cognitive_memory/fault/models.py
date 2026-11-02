# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum


class FaultStage(str, Enum):
    INVARIANT = "invariant_violation"
    EARLIEST = "earliest_deviation"
    DIFF_TRACE = "differential_trace"
    MIN_REPRO = "minimal_reproduction"
    ROOT_CAUSE = "candidate_root_cause"
    PATCH = "patch"
    TARGETED_TEST = "targeted_test"
    REGRESSION = "regression_test"
    REPLAY = "historical_replay"
    ROLLBACK = "rollback_test"
    GOVERNANCE = "governance"


class FaultStatus(str, Enum):
    OPEN = "open"
    LOCALIZED = "localized"
    PATCHED = "patched"
    VALIDATED = "validated"
    ROLLED_BACK = "rolled_back"
    CLOSED = "closed"
