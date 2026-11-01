# -*- coding: utf-8 -*-
"""One-shot E00 inventory helper; not part of product runtime."""
from __future__ import annotations

from pathlib import Path

root = Path("service/src/cognitive_memory")
needles = {
    "E01_source": [
        "root_source_id",
        "independence_key",
        "fingerprint",
        "provenance",
        "parent_evidence",
    ],
    "E01_coverage": ["coverage", "modality", "covered_range", "missing_range"],
    "E02_derive": ["freshness", "stale", "invalidate", "revision", "tombstone", "retract"],
    "E03_answer": ["CognitiveLoops", "select_action", "answers/select", "select_answer"],
    "E04_context": ["ContextBuild", "budget", "token", "envelope"],
    "E06_session": ["checkpoint", "session", "correlation"],
    "E07_cost": ["UsageLedger", "meter_scope", "cost", "budget"],
    "E08_health": ["health", "probe", "readiness", "doctor"],
    "E09_retrieval": ["fusion", "lexical", "semantic_sidepath", "planner"],
    "E10_consol": ["consolidate", "utility"],
    "E11_fence": ["watermark", "fencing", "lease", "expected_version"],
    "E12_exec": ["receipt", "outcome", "effect_confirmed", "fake_executor"],
    "E13_learn": ["LearningProposal", "ValidationSuite", "TaskFeedback", "off_policy"],
    "E14_domain": ["DomainPack", "book", "staging", "migration"],
}
files = list(root.rglob("*.py"))
texts = {f: f.read_text(encoding="utf-8", errors="ignore") for f in files}
for group, keys in needles.items():
    print("##", group)
    for k in keys:
        hits = [
            str(f.relative_to(root)).replace("\\", "/")
            for f, t in texts.items()
            if k in t
        ]
        print(f"  {k}: {len(hits)} | {', '.join(hits[:6])}")

print("domains files:", sorted(p.name for p in (root / "domains").rglob("*") if p.is_file()))
print("migration_tail:", end=" ")
from cognitive_memory.storage.migrations import MIGRATIONS  # noqa: E402

print([m[0] for m in MIGRATIONS[-5:]])
