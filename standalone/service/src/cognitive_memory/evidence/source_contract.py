# -*- coding: utf-8 -*-
"""SourceRef / Coverage 契约（E01）。

扩展 Observation/Evidence 元数据，不另建平行库。
哈希只证明内容一致性；complete 只表示覆盖，不表示内容真实或已 verified。
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceRole(str, Enum):
    USER_STATEMENT = "user_statement"
    AUTHOR_CLAIM = "author_claim"
    TOOL_OBSERVATION = "tool_observation"
    DERIVED_SUMMARY = "derived_summary"


_SOURCE_KIND_TO_ROLE = {
    "user_statement": SourceRole.USER_STATEMENT,
    "dialog": SourceRole.USER_STATEMENT,
    "video_observation": SourceRole.TOOL_OBSERVATION,
    "agent_inference": SourceRole.DERIVED_SUMMARY,
    "system": SourceRole.TOOL_OBSERVATION,
    "feedback": SourceRole.USER_STATEMENT,
    "observation": SourceRole.TOOL_OBSERVATION,
    "import": SourceRole.TOOL_OBSERVATION,
    "summary": SourceRole.DERIVED_SUMMARY,
    "derived_summary": SourceRole.DERIVED_SUMMARY,
}


class Locator(BaseModel):
    chapter: str | None = None
    paragraph: str | None = None
    page: int | None = None
    time_range: dict[str, Any] | None = None


class SourceRef(BaseModel):
    source_id: str
    root_source_id: str
    revision: str = "0"
    content_hash: str = ""
    locator: Locator = Field(default_factory=Locator)
    source_role: SourceRole = SourceRole.TOOL_OBSERVATION
    bank_id: str = "personal"
    producer: str = ""
    certified_root: bool = False


class Coverage(BaseModel):
    available_modalities: list[str] = Field(default_factory=list)
    covered_ranges: list[dict[str, Any]] = Field(default_factory=list)
    missing_ranges: list[dict[str, Any]] = Field(default_factory=list)
    completeness: str = "unknown"  # complete|partial|unknown

    def model_post_init(self, __context: Any) -> None:
        c = (self.completeness or "unknown").strip().lower()
        if c not in {"complete", "partial", "unknown"}:
            self.completeness = "unknown"
        else:
            self.completeness = c


# 已认证生产者：可登记稳定根来源；任意客户端不得自证独立性。
_CERTIFIED_PRODUCERS: set[str] = {
    "dsh",
    "mnemosyne",
    "node:admin",
    "cognitive_memory",
    "memory-node",
}


def register_certified_producer(producer: str) -> None:
    p = str(producer or "").strip()
    if p:
        _CERTIFIED_PRODUCERS.add(p)


def is_certified_producer(producer: str) -> bool:
    return str(producer or "").strip() in _CERTIFIED_PRODUCERS


def content_hash_of(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def map_source_role(source_type: str | None, *, explicit: str | None = None) -> SourceRole:
    if explicit:
        try:
            return SourceRole(str(explicit))
        except ValueError:
            pass
    key = str(source_type or "").strip().lower()
    return _SOURCE_KIND_TO_ROLE.get(key, SourceRole.TOOL_OBSERVATION)


def resolve_root_source_id(
    *,
    producer: str,
    requested_root: str = "",
    source_id: str = "",
    allow_self_attest: bool = False,
) -> tuple[str, bool]:
    """返回 (root_source_id, certified_root)。

    未认证生产者不得用任意 root_source_id 自证独立性（除非显式 allow_self_attest 兼容旧测）。
    """
    req = str(requested_root or "").strip()
    sid = str(source_id or "").strip()
    prod = str(producer or "").strip()
    if is_certified_producer(prod) and req:
        return req, True
    if allow_self_attest and req:
        return req, False
    base = req or sid or "unknown"
    if is_certified_producer(prod) and not req:
        return sid or base, True
    # claimed: 命名空间 — 声明而非认证
    return f"claimed:{prod or 'anon'}:{base}", False


def normalize_source_ref(
    *,
    source_id: str,
    root_source_id: str = "",
    revision: str = "0",
    content: str = "",
    content_hash: str = "",
    source_type: str = "",
    source_role: str | None = None,
    bank_id: str = "personal",
    producer: str = "",
    locator: dict[str, Any] | None = None,
    allow_self_attest: bool = False,
) -> SourceRef:
    sid = str(source_id or "").strip() or "unknown"
    root, certified = resolve_root_source_id(
        producer=producer,
        requested_root=root_source_id,
        source_id=sid,
        allow_self_attest=allow_self_attest,
    )
    ch = str(content_hash or "").strip() or (content_hash_of(content) if content else "")
    loc = Locator.model_validate(locator or {})
    # 未知页码保持 null，不伪造
    return SourceRef(
        source_id=sid,
        root_source_id=root,
        revision=str(revision or "0"),
        content_hash=ch,
        locator=loc,
        source_role=map_source_role(source_type, explicit=source_role),
        bank_id=str(bank_id or "personal"),
        producer=str(producer or ""),
        certified_root=certified,
    )


def normalize_coverage(
    raw: dict[str, Any] | Coverage | None,
    *,
    default_modalities: list[str] | None = None,
) -> Coverage:
    if isinstance(raw, Coverage):
        return raw
    data = dict(raw or {})
    mods = data.get("available_modalities")
    if not isinstance(mods, list) or not mods:
        mods = list(default_modalities or [])
    covered = data.get("covered_ranges") if isinstance(data.get("covered_ranges"), list) else []
    missing = data.get("missing_ranges") if isinstance(data.get("missing_ranges"), list) else []
    completeness = str(data.get("completeness") or "unknown")
    if mods and missing:
        completeness = "partial" if completeness == "unknown" else completeness
    elif mods and not missing and completeness == "unknown":
        completeness = "partial"  # 有模态但仍不知是否完整
    return Coverage(
        available_modalities=[str(m) for m in mods],
        covered_ranges=[dict(x) for x in covered if isinstance(x, dict)],
        missing_ranges=[dict(x) for x in missing if isinstance(x, dict)],
        completeness=completeness,
    )


def attach_source_contract(
    metadata: dict[str, Any] | None,
    *,
    source_ref: SourceRef | dict[str, Any] | None = None,
    coverage: Coverage | dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    if source_ref is not None:
        ref = source_ref if isinstance(source_ref, SourceRef) else SourceRef.model_validate(source_ref)
        meta["source_ref"] = ref.model_dump(mode="json")
        meta.setdefault("revision", ref.revision)
        meta.setdefault("source_role", ref.source_role.value)
    if coverage is not None:
        cov = coverage if isinstance(coverage, Coverage) else normalize_coverage(coverage)
        meta["coverage"] = cov.model_dump(mode="json")
    return meta


def support_identity_key(evidence: dict[str, Any]) -> str:
    """同源同修订身份：用于独立支持计数（副本不增独立支持）。"""
    meta = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    src = meta.get("source_ref") if isinstance(meta.get("source_ref"), dict) else {}
    root = (
        str(src.get("root_source_id") or "").strip()
        or str(evidence.get("root_source_id") or "").strip()
        or str(evidence.get("source_ref") or "").strip()
        or "unknown"
    )
    rev = str(src.get("revision") or meta.get("revision") or "0").strip() or "0"
    return f"{root}@{rev}"


def count_independent_supports(evidences: list[dict[str, Any]]) -> dict[str, Any]:
    keys: dict[str, list[str]] = {}
    for ev in evidences:
        if not isinstance(ev, dict):
            continue
        if ev.get("validation_state") in {"revoked", "invalidated", "expired", "rejected"}:
            continue
        key = support_identity_key(ev)
        keys.setdefault(key, []).append(str(ev.get("id") or ""))
    return {
        "ok": True,
        "independent_count": len(keys),
        "groups": {k: [i for i in v if i] for k, v in keys.items()},
        "duplicate_groups": {k: v for k, v in keys.items() if len(v) > 1},
    }


_VISUAL_MODALITIES = frozenset({"frame", "frames", "video", "image", "visual", "screenshot"})
_TEXT_MODALITIES = frozenset({"text", "subtitle", "subtitles", "caption", "transcript", "ocr"})
_AUDIO_MODALITIES = frozenset({"audio", "soundtrack", "speech"})


def coverage_adequacy(
    coverage: Coverage | dict[str, Any] | None,
    *,
    required_modalities: list[str] | None = None,
    query_kind: str = "",
) -> dict[str, Any]:
    """判断覆盖是否足以支持查询目标；不足时不得声称看过画面等。"""
    cov = normalize_coverage(coverage)
    required = [str(x).lower() for x in (required_modalities or [])]
    q = str(query_kind or "").strip().lower()
    if not required:
        if q in {"visual", "vision", "frame", "video_scene"}:
            required = ["visual"]
        elif q in {"audio", "speech"}:
            required = ["audio"]
        elif q:
            required = ["text"]

    available = {str(m).lower() for m in cov.available_modalities}
    missing_declared = {
        str(m.get("modality") or m.get("kind") or "").lower()
        for m in cov.missing_ranges
        if isinstance(m, dict)
    }

    def _has(mod: str) -> bool:
        m = mod.lower()
        if m in {"visual", "vision", "frame", "video"}:
            return bool(available & _VISUAL_MODALITIES)
        if m in {"audio", "speech"}:
            return bool(available & _AUDIO_MODALITIES)
        if m in {"text", "subtitle", "transcript"}:
            return bool(available & _TEXT_MODALITIES) or "text" in available
        return m in available

    lacking = [m for m in required if not _has(m)]
    text_only = bool(available & _TEXT_MODALITIES) and not (available & _VISUAL_MODALITIES)
    reasons: list[str] = []
    if lacking:
        reasons.append("coverage_insufficient")
    if text_only and any(m in {"visual", "vision", "frame", "video"} for m in required):
        reasons.append("text_coverage_only")
    if missing_declared & set(required) or (
        "frame" in missing_declared or "visual" in missing_declared
    ):
        if "coverage_insufficient" not in reasons:
            reasons.append("coverage_insufficient")

    sufficient = not lacking and "coverage_insufficient" not in reasons
    return {
        "ok": True,
        "sufficient": sufficient,
        "available_modalities": sorted(available),
        "required_modalities": required,
        "lacking_modalities": lacking,
        "text_coverage_only": text_only,
        "completeness": cov.completeness,
        "claims_visual_seen": False if text_only or lacking else bool(available & _VISUAL_MODALITIES),
        "reason_codes": reasons or (["coverage_ok"] if sufficient else ["coverage_unknown"]),
    }


def extract_source_contract(metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = dict(metadata or {})
    src = meta.get("source_ref") if isinstance(meta.get("source_ref"), dict) else None
    cov = meta.get("coverage") if isinstance(meta.get("coverage"), dict) else None
    return {
        "source_ref": SourceRef.model_validate(src).model_dump(mode="json") if src else None,
        "coverage": normalize_coverage(cov).model_dump(mode="json") if cov else None,
        "source_role": meta.get("source_role"),
    }


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
