# -*- coding: utf-8 -*-
"""Run P0.5 spike: sample → parse → provenance/security assessment → report files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cognitive_memory.claims.normalize import normalized_hash
from cognitive_memory.claims.parser import parse_memory_text
from cognitive_memory.migration_spike.sampler import sample_memories


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def assess_provenance(sample: dict[str, Any]) -> dict[str, Any]:
    """Map legacy memory_evidence presence to provenance_status — never invent sources."""
    count = int(sample.get("evidence_count") or 0)
    states = str(sample.get("validation_states") or "")
    meta = sample.get("metadata") or {}
    if count <= 0 and not meta.get("source_ids") and not sample.get("source_ids"):
        return {
            "provenance_status": "unknown",
            "provenance_flags": ["legacy_provenance_incomplete", "no_memory_evidence_links"],
        }
    if count > 0 and "unverified" in states and "supports" not in states.replace("unverified", ""):
        return {
            "provenance_status": "partial",
            "provenance_flags": ["has_links_but_unverified", "must_not_auto_verify"],
            "evidence_count": count,
            "validation_states": states,
        }
    if count > 0:
        return {
            "provenance_status": "partial",
            "provenance_flags": ["legacy_link_only_not_independent_evidence"],
            "evidence_count": count,
            "validation_states": states,
        }
    return {
        "provenance_status": "unknown",
        "provenance_flags": ["insufficient_provenance"],
    }


def assess_security(sample: dict[str, Any]) -> dict[str, Any]:
    meta = sample.get("metadata") or {}
    bank = sample.get("bank_id") or meta.get("bank_id") or "personal"
    mapping_ok = True
    failures: list[str] = []
    # Fail closed markers: free-form metadata without explicit owner/bank
    if not bank:
        mapping_ok = False
        failures.append("missing_bank_id")
    return {
        "security_mapping_ok": mapping_ok,
        "security_scope": {
            "bank_id": bank,
            "domain": meta.get("domain"),
            "data_kind": meta.get("data_kind"),
            "classification": meta.get("classification") or meta.get("data_classification"),
            "note": "P0.5 projection only; not a full SecurityContext",
        },
        "security_mapping_failures": failures,
    }


@dataclass
class SpikeReport:
    generated_at: str
    db_path: str
    sample_count: int
    stats: dict[str, Any] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    parser_errors: list[dict[str, Any]] = field(default_factory=list)
    provenance_gaps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "db_path": self.db_path,
            "sample_count": self.sample_count,
            "stats": self.stats,
            "note": "read_only_spike; no production writes; no auto-verify",
        }


def _compute_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results) or 1
    parsed = sum(1 for r in results if r.get("parse_status") == "parsed")
    unresolved = sum(1 for r in results if r.get("parse_status") == "unresolved")
    review = sum(1 for r in results if r.get("parse_status") == "needs_review")
    multi = sum(1 for r in results if r.get("multi_claim"))
    ast_valid = sum(
        1
        for r in results
        if r.get("parsed_claim_ast") and r.get("parse_status") in ("parsed", "needs_review", "unresolved")
    )
    prov_complete = sum(1 for r in results if r.get("provenance_status") == "complete")
    prov_unknown = sum(1 for r in results if r.get("provenance_status") == "unknown")
    sec_fail = sum(1 for r in results if not r.get("security_mapping_ok", True))

    # false merge heuristic: identical normalized_hash across different memory ids within spike
    hashes: dict[str, list[str]] = {}
    for r in results:
        h = r.get("normalized_hash") or ""
        if not h or r.get("parse_status") == "unresolved":
            continue
        hashes.setdefault(h, []).append(r.get("source_memory_id") or "")
    merge_candidates = {h: ids for h, ids in hashes.items() if len(set(ids)) > 1}

    by_stratum: dict[str, dict[str, int]] = {}
    for r in results:
        s = str(r.get("stratum") or "unknown")
        bucket = by_stratum.setdefault(s, {"total": 0, "parsed": 0, "unresolved": 0, "needs_review": 0})
        bucket["total"] += 1
        st = r.get("parse_status")
        if st in bucket:
            bucket[st] += 1

    return {
        "sample_count": len(results),
        "claim_extraction_rate": round(parsed / n, 4),
        "multi_claim_memory_rate": round(multi / n, 4),
        "ast_validity_rate": round(ast_valid / n, 4),
        "unresolved_rate": round(unresolved / n, 4),
        "needs_review_rate": round(review / n, 4),
        "provenance_complete_rate": round(prov_complete / n, 4),
        "provenance_unknown_rate": round(prov_unknown / n, 4),
        "security_mapping_failure_rate": round(sec_fail / n, 4),
        "false_merge_candidate_groups": len(merge_candidates),
        "false_merge_rate_heuristic": round(len(merge_candidates) / n, 4),
        "review_required_rate": round((review + unresolved) / n, 4),
        "by_stratum": by_stratum,
        "merge_candidate_hashes": {
            h: ids for h, ids in list(merge_candidates.items())[:20]
        },
    }


def run_spike(
    db_path: str | Path,
    out_dir: str | Path,
    *,
    per_stratum: int = 10,
    total_target: int = 100,
) -> SpikeReport:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    samples = sample_memories(db_path, per_stratum=per_stratum, total_target=total_target)

    results: list[dict[str, Any]] = []
    parser_errors: list[dict[str, Any]] = []
    provenance_gaps: list[dict[str, Any]] = []

    for sample in samples:
        try:
            parsed = parse_memory_text(
                sample.get("source_text") or "",
                metadata=sample.get("metadata") or {},
            )
            prov = assess_provenance(sample)
            sec = assess_security(sample)
            row = {
                **sample,
                **parsed.to_dict(),
                **prov,
                **sec,
                "expected_claim_ast": None,
                "default_lifecycle_if_migrated": "proposed",
                "must_not_auto_verify": True,
                "notes": "; ".join(parsed.notes + prov.get("provenance_flags", [])),
            }
            results.append(row)
            if parsed.status == "unresolved":
                parser_errors.append(
                    {
                        "case_id": sample["case_id"],
                        "source_memory_id": sample["source_memory_id"],
                        "stratum": sample["stratum"],
                        "reason": (parsed.ast.root.reason_codes if hasattr(parsed.ast.root, "reason_codes") else []),
                        "attempts": parsed.attempts,
                        "excerpt": (sample.get("source_text") or "")[:200],
                    }
                )
            if prov.get("provenance_status") in ("unknown", "partial"):
                provenance_gaps.append(
                    {
                        "case_id": sample["case_id"],
                        "source_memory_id": sample["source_memory_id"],
                        "provenance_status": prov.get("provenance_status"),
                        "flags": prov.get("provenance_flags"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            parser_errors.append(
                {
                    "case_id": sample.get("case_id"),
                    "error": str(exc),
                    "fatal": True,
                }
            )

    stats = _compute_stats(results)
    report = SpikeReport(
        generated_at=_now(),
        db_path=str(db_path),
        sample_count=len(results),
        stats=stats,
        samples=results,
        parser_errors=parser_errors,
        provenance_gaps=provenance_gaps,
    )

    # Write artifacts (shadow files only)
    samples_path = out / "migration_samples.jsonl"
    with samples_path.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    (out / "parser_errors.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in parser_errors) + ("\n" if parser_errors else ""),
        encoding="utf-8",
    )
    (out / "provenance_gaps.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in provenance_gaps) + ("\n" if provenance_gaps else ""),
        encoding="utf-8",
    )
    (out / "spike_stats.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # unused import guard
    _ = normalized_hash
    return report
