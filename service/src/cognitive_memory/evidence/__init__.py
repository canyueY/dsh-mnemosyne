# -*- coding: utf-8 -*-
"""Evidence Engine（E1.4 legacy + P3 Evidence objects）。"""

from cognitive_memory.evidence.models import (
    EvidenceAttachIn,
    EvidenceKind,
    EvidenceRecord,
    ValidationState,
)
from cognitive_memory.evidence.derivation import DerivationStore
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.evidence.source_contract import (
    Coverage,
    SourceRef,
    SourceRole,
    count_independent_supports,
    coverage_adequacy,
    normalize_coverage,
    normalize_source_ref,
)
from cognitive_memory.evidence.store import attach_evidence, list_evidence_for_memory

__all__ = [
    "Coverage",
    "DerivationStore",
    "EvidenceAttachIn",
    "EvidenceKind",
    "EvidenceObjectStore",
    "EvidenceRecord",
    "SourceRef",
    "SourceRole",
    "ValidationState",
    "attach_evidence",
    "count_independent_supports",
    "coverage_adequacy",
    "list_evidence_for_memory",
    "normalize_coverage",
    "normalize_source_ref",
]
