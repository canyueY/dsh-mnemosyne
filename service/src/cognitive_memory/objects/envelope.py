# -*- coding: utf-8 -*-
"""CognitiveObjectRef / Envelope mapping. governance_state never overwrites raw_status."""

from __future__ import annotations

import json
from typing import Any

from cognitive_memory.objects.relations import canonicalize

KIND_BY_PREFIX = (
    ("obs_", "Observation", "observations"),
    ("mem_", "Memory", "memories"),
    ("evd_", "Evidence", "evidences"),
    ("clm_", "Claim", "claims"),
    ("kn_", "Knowledge", "knowledge_nodes"),
    ("sch_", "CognitiveSchema", "cognitive_schemas"),
    ("exp_", "Experience", "meta_experiences"),
    ("evl_", "Evaluation", "meta_evaluations"),
    ("hyp_", "Hypothesis", "meta_hypotheses"),
    ("scd_", "StrategyCandidate", "meta_strategy_candidates"),
    ("snp_", "Snapshot", "meta_snapshots"),
    ("xpr_", "Experiment", "meta_experiments"),
    ("slf_", "SelfModel", "meta_self_models"),
    ("trc_", "Trace", "meta_traces"),
    ("med_", "MediaAsset", "media_assets"),
    ("par_", "ParsedArtifact", "parsed_artifacts"),
    ("loc_", "SourceLocator", "source_locators"),
    ("gol_", "Goal", "goals"),
    ("eev_", "Event", "env_events"),
    ("att_", "Attention", "attention_items"),
    ("rpr_", "RepairProposal", "repair_proposals"),
    ("eex_", "EvolutionExperiment", "evolution_experiments"),
    ("ext_", "ExternalBundle", "meta_external_bundles"),
)

MEMORY_KIND = {
    "working": "Trace",
    "episodic": "Observation",
    "semantic": "Knowledge",
    "procedural": "StrategyLog",
}


def _loads(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return {} if default is None else default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {} if default is None else default


def kind_from_id(object_id: str) -> tuple[str, str]:
    text = str(object_id or "")
    for prefix, kind, table in KIND_BY_PREFIX:
        if text.startswith(prefix):
            return kind, table
    return "Unknown", "unknown"


def map_governance_state(*, kind: str, raw_status: str | None, verification: str | None = None) -> str:
    raw = str(raw_status or "").strip().lower()
    ver = str(verification or "").strip().lower()
    if not raw and not ver:
        return "Unknown"
    if ver in {"verified", "accepted"}:
        return "Verified"
    mapping = {
        "active": "Active",
        "candidate": "Candidate",
        "proposed": "Candidate",
        "needsreview": "NeedsReview",
        "needs_review": "NeedsReview",
        "conflicted": "Conflicted",
        "pending": "Pending",
        "deprecated": "Deprecated",
        "superseded": "Deprecated",
        "archived": "Archived",
        "invalidated": "Archived",
        "retired": "Archived",
        "draft": "Candidate",
        "shadow": "Candidate",
        "published": "Active",
        "unverified": "Candidate",
    }
    return mapping.get(raw) or mapping.get(ver) or "Unknown"


def parse_id(object_id: str) -> dict[str, Any]:
    kind, table = kind_from_id(object_id)
    return {
        "authority_id": object_id,
        "kind": kind,
        "source_table": table,
        "source_kind": kind,
        "bank_id": None,
        "scope": None,
        "version": None,
    }


def envelope(
    *,
    object_id: str,
    kind: str,
    source_table: str,
    title: str,
    content: str | None,
    raw_status: str | None,
    verification_state: str | None = None,
    bank_id: str | None = None,
    scope: Any = None,
    version: Any = None,
    confidence: Any = None,
    recorded_time: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    source_refs: list[Any] | None = None,
    provenance: Any = None,
    evidence_refs: list[Any] | None = None,
    relation_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    permissions: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gov = map_governance_state(kind=kind, raw_status=raw_status, verification=verification_state)
    conf = confidence
    if conf is not None:
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = None
    body = {
        "ref": {
            "authority_id": object_id,
            "kind": kind,
            "source_table": source_table,
            "source_kind": kind,
            "bank_id": bank_id if bank_id not in (None, "") else "unknown",
            "scope": scope if scope not in (None, "") else "unknown",
            "version": version if version not in (None, "") else None,
        },
        "title": title or object_id,
        "content": content,
        "summary": (content or "")[:240] or None,
        "raw_status": raw_status if raw_status not in (None, "") else "unknown",
        "governance_state": gov,
        "verification_state": verification_state if verification_state not in (None, "") else "unknown",
        "confidence": conf,
        "recorded_time": recorded_time,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source_refs": source_refs or [],
        "provenance": provenance,
        "evidence_refs": evidence_refs or [],
        "relation_count": relation_count,
        "metadata": metadata or {},
        "permissions": permissions or {},
        "disclosure": permissions or {},
    }
    if extra:
        body["extra"] = extra
    return body


def from_memory_row(row: dict[str, Any], *, relation_count: int | None = None) -> dict[str, Any]:
    meta = _loads(row.get("metadata_json"))
    mem_type = str(row.get("type") or "")
    kind = MEMORY_KIND.get(mem_type, "Observation")
    content = str(row.get("content") or "")
    return envelope(
        object_id=str(row["id"]),
        kind=kind,
        source_table="memories",
        title=content.split("\n", 1)[0][:96] or str(row["id"]),
        content=content,
        raw_status=row.get("status"),
        bank_id=row.get("bank_id"),
        scope=meta.get("scope"),
        version=row.get("version"),
        confidence=row.get("confidence"),
        recorded_time=row.get("created_at"),
        valid_from=meta.get("valid_from") or row.get("created_at"),
        valid_to=row.get("expires_at"),
        source_refs=_loads(row.get("source_ids_json"), []),
        provenance={"producer": meta.get("producer") or "unknown", "memory_type": mem_type},
        relation_count=relation_count,
        metadata=meta,
        extra={"memory_type": mem_type, "physical_kind": "Memory"},
    )


def from_observation_row(row: dict[str, Any]) -> dict[str, Any]:
    meta = _loads(row.get("metadata_json"))
    content = str(row.get("content") or row.get("text") or "")
    return envelope(
        object_id=str(row["id"]),
        kind="Observation",
        source_table="observations",
        title=content.split("\n", 1)[0][:96] or str(row["id"]),
        content=content,
        raw_status=row.get("status") or meta.get("status"),
        bank_id=row.get("bank_id") or meta.get("bank_id"),
        scope=meta.get("scope"),
        version=row.get("version"),
        recorded_time=row.get("created_at") or row.get("recorded_at"),
        source_refs=[row.get("source_kind")] if row.get("source_kind") else [],
        provenance={"source_kind": row.get("source_kind"), "producer": row.get("producer")},
        metadata=meta,
    )


def from_evidence_row(row: dict[str, Any]) -> dict[str, Any]:
    meta = _loads(row.get("metadata_json"))
    title = str(row.get("source_ref") or row.get("id"))
    return envelope(
        object_id=str(row["id"]),
        kind="Evidence",
        source_table="evidences",
        title=title[:96],
        content=str(meta.get("content") or row.get("source_ref") or ""),
        raw_status=row.get("validation_state") or row.get("provenance_status"),
        verification_state=row.get("validation_state"),
        bank_id=row.get("bank_id"),
        scope=_loads(row.get("security_json")).get("scope"),
        recorded_time=row.get("created_at"),
        provenance={
            "producer": row.get("producer"),
            "source_type": row.get("source_type"),
            "source_ref": row.get("source_ref"),
            "independence_key": row.get("independence_key"),
            "root_source_id": row.get("root_source_id"),
        },
        metadata=meta,
    )


def claim_display_title(row: dict[str, Any], ast: dict[str, Any] | None = None) -> str:
    """Human title from AST; never invent facts. Falls back to surface, then id."""
    ast = ast if isinstance(ast, dict) else {}
    oid = str(row.get("id") or "")

    def _from_node(node: Any, negated: bool = False) -> str | None:
        if not isinstance(node, dict):
            return None
        kind = str(node.get("kind") or "")
        if kind == "negation":
            return _from_node(node.get("target") or {}, True)
        if kind == "temporal":
            return _from_node(node.get("target") or {}, negated)
        if kind == "conditional":
            return _from_node(node.get("consequence") or {}, negated)
        if kind == "unresolved":
            raw = str(node.get("raw_text") or "").strip()
            return raw[:96] if raw else None
        pred = str(node.get("predicate") or "").strip()
        if not pred:
            return None
        subj = str(node.get("subject") or "").strip()
        args = node.get("arguments") if isinstance(node.get("arguments"), list) else []
        obj = str(args[0] if args else node.get("object") or "").strip()
        likes = {"likes", "喜欢", "喜爱", "爱听", "偏好"}
        dislikes = {"dislikes", "讨厌", "不喜欢"}
        if pred in likes or pred in dislikes:
            polar = "不喜欢" if (pred in dislikes or (negated and pred in likes)) else "喜欢"
            if obj:
                return f"{obj}偏好：{polar}"[:96]
            if subj and subj not in {"user", "unknown_subject"}:
                return f"{subj}偏好：{polar}"[:96]
            return f"偏好：{polar}"
        parts = [p for p in (subj, ("不" + pred) if negated else pred, obj) if p]
        return "".join(parts)[:96] if parts else None

    built = _from_node(ast.get("root") if isinstance(ast.get("root"), dict) else ast)
    surface = str(row.get("surface") or ast.get("surface") or "").strip()

    def _human(text: str) -> bool:
        t = (text or "").strip()
        return bool(t) and t != oid and not t.startswith("clm_")

    if built and _human(built) and "偏好：" in built:
        obj_part = built.split("偏好：", 1)[0]
        if 0 < len(obj_part) <= 12 and " " not in obj_part and "：" not in obj_part:
            return built
    if _human(surface):
        return surface[:96]
    if _human(built or ""):
        return built[:96]
    return oid or "unknown"


def from_claim_row(row: dict[str, Any], *, evidence_refs: list[Any] | None = None) -> dict[str, Any]:
    ast = _loads(row.get("ast_json"))
    surface = str(row.get("surface") or ast.get("surface") or row["id"])
    title = claim_display_title(row, ast)
    return envelope(
        object_id=str(row["id"]),
        kind="Claim",
        source_table="claims",
        title=title,
        content=surface,
        raw_status=row.get("lifecycle"),
        verification_state=row.get("verification_status"),
        bank_id=row.get("bank_id"),
        scope=_loads(row.get("scope_json")),
        version=row.get("ast_version"),
        recorded_time=row.get("created_at"),
        source_refs=[row.get("source_memory_id")] if row.get("source_memory_id") else [],
        evidence_refs=evidence_refs or [],
        metadata={"ast": ast, "redirects_to": row.get("redirects_to")},
        extra={"ast": ast, "redirects_to": row.get("redirects_to")},
    )


def from_knowledge_row(row: dict[str, Any]) -> dict[str, Any]:
    return envelope(
        object_id=str(row["id"]),
        kind="Knowledge",
        source_table="knowledge_nodes",
        title=str(row.get("name") or row["id"]),
        content=str(row.get("description") or ""),
        raw_status=row.get("status"),
        bank_id=_loads(row.get("scope_json")).get("bank_id"),
        scope=_loads(row.get("scope_json")),
        version=row.get("version"),
        recorded_time=row.get("created_at"),
        evidence_refs=_loads(row.get("claim_ids_json"), []),
        extra={"knowledge_kind": row.get("kind"), "schema_id": row.get("schema_id")},
    )


def relation_envelope(row: dict[str, Any]) -> dict[str, Any]:
    meta = _loads(row.get("metadata_json"))
    raw_type = str(row.get("relation") or "")
    return {
        "relation_id": str(row.get("id")),
        "from": row.get("from_id"),
        "to": row.get("to_id"),
        "relation_type": raw_type,
        "canonical_type": canonicalize(raw_type),
        "direction": meta.get("direction") or "forward",
        "polarity": meta.get("polarity") or "unknown",
        "confidence": row.get("confidence"),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_until") or row.get("valid_to"),
        "evidence_ids": meta.get("evidence_ids") or [],
        "source_refs": meta.get("source_refs") or [],
        "audit_ref": row.get("audit_ref") or meta.get("audit_ref") or None,
        "scope": row.get("scope") or meta.get("scope") or "unknown",
        "status": row.get("status") or meta.get("status") or "unknown",
        "version": row.get("version") or meta.get("version") or 1,
        "deleted_at": row.get("deleted_at"),
        "metadata": meta,
        "note": row.get("note") or "",
        "created_at": row.get("created_at"),
    }
