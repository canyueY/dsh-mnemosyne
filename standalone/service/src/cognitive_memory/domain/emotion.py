# -*- coding: utf-8 -*-
"""Mnemosyne 情绪元数据 schema v1（挂 metadata.mnemosyne，不另建个人 LT 库）。

强度 e_i ∈ [0,1] 与置信度 c_i ∈ [0,1] 必须分开。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
MODEL_VERSION = "mnemosyne.v1"

# 基础情绪字典（可扩展；未知情绪进 unknown_labels）
BASE_EMOTIONS = (
    "joy",
    "sadness",
    "anger",
    "anxiety",
    "fear",
    "disappointment",
    "shame",
    "guilt",
    "loneliness",
    "relief",
    "curiosity",
    "affection",
)

SOURCE_SELF_REPORT = "self_report"
SOURCE_DIRECT_OBS = "direct_observation"
SOURCE_INFERENCE = "model_inference"
SOURCE_CONFIRMATION = "later_confirmation"
SOURCE_COUNTER = "counter_evidence"

STATUS_PROVISIONAL = "provisional"
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_EPHEMERAL = "ephemeral"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp01(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, x))


def _clamp_valence(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(-1.0, min(1.0, x))


def empty_emotion_vector() -> dict[str, float]:
    return {k: 0.0 for k in BASE_EMOTIONS}


def empty_confidence_vector() -> dict[str, float]:
    return {k: 0.0 for k in BASE_EMOTIONS}


def normalize_emotion_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    """规范化 EmotionState 逻辑对象。"""
    src = raw if isinstance(raw, dict) else {}
    vec_in = src.get("emotion_vector") if isinstance(src.get("emotion_vector"), dict) else {}
    conf_in = src.get("confidence_vector") if isinstance(src.get("confidence_vector"), dict) else {}
    vec = empty_emotion_vector()
    conf = empty_confidence_vector()
    unknown: list[str] = []
    for k, v in vec_in.items():
        key = str(k).strip()
        if key in vec:
            vec[key] = _clamp01(v)
        elif key:
            unknown.append(key)
            vec.setdefault(key, _clamp01(v))  # 允许扩展键
    for k, v in conf_in.items():
        key = str(k).strip()
        if key in conf:
            conf[key] = _clamp01(v)
        elif key:
            conf[key] = _clamp01(v)
    source = str(src.get("source") or SOURCE_INFERENCE).strip() or SOURCE_INFERENCE
    status = str(src.get("status") or STATUS_PROVISIONAL).strip() or STATUS_PROVISIONAL
    return {
        "schemaVersion": SCHEMA_VERSION,
        "modelVersion": str(src.get("modelVersion") or MODEL_VERSION),
        "state_id": src.get("state_id"),
        "subject_id": str(src.get("subject_id") or "owner"),
        "emotion_vector": vec,
        "confidence_vector": conf,
        "unknown_labels": sorted(set(unknown + list(src.get("unknown_labels") or []))),
        "valence": _clamp_valence(src.get("valence")),
        "arousal": _clamp01(src.get("arousal"), 0.0) if src.get("arousal") is not None else None,
        "stress": _clamp01(src.get("stress"), 0.0) if src.get("stress") is not None else None,
        "control": _clamp01(src.get("control"), 0.0) if src.get("control") is not None else None,
        "stability": _clamp01(src.get("stability"), 0.0) if src.get("stability") is not None else None,
        "uncertainty": _clamp01(src.get("uncertainty"), 0.5),
        "valid_from": src.get("valid_from"),
        "valid_to": src.get("valid_to"),
        "observed_at": src.get("observed_at"),
        "recorded_at": src.get("recorded_at") or _now(),
        "evidence_refs": list(src.get("evidence_refs") or []),
        "source": source,
        "status": status,
    }


def attach_mnemosyne(
    metadata: dict[str, Any] | None,
    *,
    emotion_state: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
    assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写回 metadata['mnemosyne']，保留其它字段。"""
    meta = dict(metadata or {})
    pack = meta.get("mnemosyne") if isinstance(meta.get("mnemosyne"), dict) else {}
    pack = dict(pack)
    pack["schemaVersion"] = SCHEMA_VERSION
    pack["modelVersion"] = MODEL_VERSION
    if emotion_state is not None:
        pack["emotion_state"] = normalize_emotion_state(emotion_state)
    if event is not None and isinstance(event, dict):
        pack["emotional_event"] = event
    if assessment is not None and isinstance(assessment, dict):
        pack["assessment"] = assessment
    meta["mnemosyne"] = pack
    return meta


def get_mnemosyne(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("mnemosyne")
    return dict(raw) if isinstance(raw, dict) else {}


_POS = frozenset({"joy", "relief", "affection", "curiosity"})
_NEG = frozenset(
    {
        "sadness",
        "anger",
        "anxiety",
        "fear",
        "disappointment",
        "shame",
        "guilt",
        "loneliness",
    }
)


def _dominant_from_state(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    vec = state.get("emotion_vector") if isinstance(state.get("emotion_vector"), dict) else {}
    if not vec:
        return ""
    best_k, best_v = "", 0.0
    for k, v in vec.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv >= best_v:
            best_k, best_v = str(k), fv
    return best_k if best_v > 0 else ""


def _valence_bucket(label: str, state: dict[str, Any] | None = None) -> str:
    if label in _POS:
        return "pos"
    if label in _NEG:
        return "neg"
    if isinstance(state, dict) and state.get("valence") is not None:
        try:
            v = float(state["valence"])
        except (TypeError, ValueError):
            return "neu"
        if v > 0.15:
            return "pos"
        if v < -0.15:
            return "neg"
    return "neu"


def emotion_retrieval_score(
    metadata: dict[str, Any] | None,
    *,
    query_emotion: dict[str, Any] | None = None,
    query_behaviors: list[str] | None = None,
    enable: bool = True,
) -> float:
    """P5 检索重排附加分：1.0 中性；匹配情绪/行为上调，相反效价下调。不删除候选。

    query_emotion 为空（查询无情绪信号）时不重排，返回 1.0。
    记忆无 mnemosyne 时保持 1.0（不因缺情绪元数据被惩罚）。
    """
    if not enable:
        return 1.0
    q_state = None
    if isinstance(query_emotion, dict):
        q_state = query_emotion.get("emotion_state") or query_emotion
        if not isinstance(q_state, dict) or not (q_state.get("emotion_vector") or {}):
            q_state = None
    if not q_state and not (query_behaviors or []):
        return 1.0

    pack = get_mnemosyne(metadata)
    if not pack and not (isinstance(metadata, dict) and metadata.get("emotion_event")):
        return 1.0

    m_state = pack.get("emotion_state") if isinstance(pack.get("emotion_state"), dict) else None
    assessment = pack.get("assessment") if isinstance(pack.get("assessment"), dict) else {}
    event = pack.get("emotional_event") if isinstance(pack.get("emotional_event"), dict) else {}

    q_dom = _dominant_from_state(q_state)
    m_dom = str(assessment.get("dominant_emotion") or "").strip() or _dominant_from_state(m_state)
    score = 1.0

    if q_dom and m_dom:
        if q_dom == m_dom:
            score *= 1.25
        else:
            qb = _valence_bucket(q_dom, q_state)
            mb = _valence_bucket(m_dom, m_state)
            if qb != "neu" and mb != "neu" and qb == mb:
                score *= 1.1
            elif qb != "neu" and mb != "neu" and qb != mb:
                score *= 0.72

    q_beh = {str(b) for b in (query_behaviors or []) if str(b).strip()}
    m_beh = {str(b) for b in (event.get("behavior_refs") or []) if str(b).strip()}
    if q_beh and m_beh and (q_beh & m_beh):
        score *= 1.12

    # 显式情绪事件记忆略加分（仍可被相反效价压下）
    if isinstance(metadata, dict) and metadata.get("emotion_event"):
        score *= 1.05

    return max(0.05, min(1.5, score))
