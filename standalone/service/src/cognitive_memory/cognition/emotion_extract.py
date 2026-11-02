# -*- coding: utf-8 -*-
"""P1：从观察文本提取复合情绪候选（默认 shadow，不写正式 LT）。"""

from __future__ import annotations

import json
import re
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.emotion import (
    MODEL_VERSION,
    SOURCE_DIRECT_OBS,
    SOURCE_INFERENCE,
    SOURCE_SELF_REPORT,
    STATUS_EPHEMERAL,
    STATUS_PROVISIONAL,
    attach_mnemosyne,
    normalize_emotion_state,
)
from cognitive_memory.storage.ids import new_id

# 明确自述：「我很/好/有点…」
_SELF_PATTERNS: list[tuple[str, str, float, float]] = [
    (r"我(现在)?(很|好|有点|有些|超级|特别)?(开心|高兴|快乐|喜悦)", "joy", 0.75, 0.85),
    (r"我(现在)?(很|好|有点|有些)?(难过|伤心|悲伤|想哭)", "sadness", 0.75, 0.85),
    (r"我(现在)?(很|好|有点|有些|超级)?(生气|愤怒|火大|气死)", "anger", 0.8, 0.9),
    (r"我(现在)?(很|好|有点|有些)?(焦虑|紧张|慌|不安)", "anxiety", 0.75, 0.85),
    (r"我(现在)?(很|好|有点|有些)?(害怕|恐惧|怕)", "fear", 0.7, 0.8),
    (r"我(现在)?(很|好|有点|有些)?(失望|心寒)", "disappointment", 0.7, 0.8),
    (r"我(现在)?(很|好|有点|有些)?(羞耻|丢脸|尴尬)", "shame", 0.65, 0.75),
    (r"我(现在)?(很|好|有点|有些)?(内疚|愧疚|对不起)", "guilt", 0.65, 0.75),
    (r"我(现在)?(很|好|有点|有些)?(孤独|一个人|没人)", "loneliness", 0.65, 0.75),
    (r"我(现在)?(很|好|有点|有些)?(释然|轻松|松了口气)", "relief", 0.7, 0.8),
    (r"我(现在)?(很|好|有点|有些)?(好奇|想知道)", "curiosity", 0.55, 0.7),
    (r"我(现在)?(很|好|有点|有些)?(想你|喜欢你|爱你|亲近)", "affection", 0.7, 0.8),
]

# 弱推断线索（非自述）
_INFER_PATTERNS: list[tuple[str, str, float, float]] = [
    (r"(崩溃|抑郁|不想活|好累啊|烦死了)", "sadness", 0.45, 0.45),
    (r"(气死|怒了|火大|滚)", "anger", 0.5, 0.4),
    (r"(怎么办|会不会出事|好慌)", "anxiety", 0.45, 0.4),
    (r"(哈哈|太好了|耶|开心)", "joy", 0.4, 0.35),
    (r"(算了吧|没意思|心凉)", "disappointment", 0.4, 0.35),
]

_BEHAVIOR_HINTS = (
    (r"(不回了|已读不回|中断|挂断|不想(再)?说了)", "withdrawal"),
    (r"(反复确认|再确认一下|真的吗|你确定)", "repeated_confirmation"),
    (r"(拒绝|不要|别说了|闭嘴)", "rejection"),
)


def _valence_from_vector(vec: dict[str, float]) -> float:
    pos = vec.get("joy", 0) + vec.get("relief", 0) + vec.get("affection", 0) + vec.get("curiosity", 0) * 0.3
    neg = (
        vec.get("sadness", 0)
        + vec.get("anger", 0)
        + vec.get("anxiety", 0)
        + vec.get("fear", 0)
        + vec.get("disappointment", 0)
        + vec.get("shame", 0)
        + vec.get("guilt", 0)
        + vec.get("loneliness", 0)
    )
    raw = pos - neg
    return max(-1.0, min(1.0, raw))


def extract_emotion_from_text(content: str) -> dict[str, Any]:
    """规则提取：区分自述与推断；强度与置信度分开。"""
    text = content or ""
    vec: dict[str, float] = {}
    conf: dict[str, float] = {}
    source = SOURCE_INFERENCE
    matched_self = False

    for pat, emo, intensity, c in _SELF_PATTERNS:
        if re.search(pat, text):
            matched_self = True
            vec[emo] = max(vec.get(emo, 0.0), intensity)
            conf[emo] = max(conf.get(emo, 0.0), c)
            source = SOURCE_SELF_REPORT

    # 同句复合：「我很生气，又有点焦虑」— 已有自述后，允许无「我」的强度+情绪片段
    if matched_self:
        for emo_label, intensity, c in (
            ("joy", 0.65, 0.75),
            ("sadness", 0.65, 0.75),
            ("anger", 0.7, 0.8),
            ("anxiety", 0.65, 0.75),
            ("fear", 0.6, 0.7),
            ("disappointment", 0.6, 0.7),
            ("shame", 0.55, 0.65),
            ("guilt", 0.55, 0.65),
            ("loneliness", 0.55, 0.65),
            ("relief", 0.6, 0.7),
            ("curiosity", 0.5, 0.6),
            ("affection", 0.6, 0.7),
        ):
            # 中文标签与英文键映射
            zh = {
                "joy": r"开心|高兴|快乐|喜悦",
                "sadness": r"难过|伤心|悲伤|想哭",
                "anger": r"生气|愤怒|火大|气死",
                "anxiety": r"焦虑|紧张|慌|不安",
                "fear": r"害怕|恐惧|怕",
                "disappointment": r"失望|心寒",
                "shame": r"羞耻|丢脸|尴尬",
                "guilt": r"内疚|愧疚",
                "loneliness": r"孤独",
                "relief": r"释然|轻松",
                "curiosity": r"好奇",
                "affection": r"喜欢|爱你|想你",
            }.get(emo_label, "")
            if not zh:
                continue
            if re.search(rf"(很|好|有点|有些|又|还|超级|特别)({zh})", text):
                vec[emo_label] = max(vec.get(emo_label, 0.0), intensity)
                conf[emo_label] = max(conf.get(emo_label, 0.0), c)

    if not matched_self:
        for pat, emo, intensity, c in _INFER_PATTERNS:
            if re.search(pat, text):
                vec[emo] = max(vec.get(emo, 0.0), intensity)
                conf[emo] = max(conf.get(emo, 0.0), c)

    behaviors: list[str] = []
    for pat, label in _BEHAVIOR_HINTS:
        if re.search(pat, text):
            behaviors.append(label)

    if not vec and not behaviors:
        return {
            "ok": True,
            "has_signal": False,
            "emotion_state": None,
            "behaviors": [],
            "source": SOURCE_DIRECT_OBS if behaviors else SOURCE_INFERENCE,
        }

    if behaviors and not vec:
        source = SOURCE_DIRECT_OBS

    state = normalize_emotion_state(
        {
            "emotion_vector": vec,
            "confidence_vector": conf,
            "valence": _valence_from_vector(vec) if vec else None,
            "arousal": 0.7 if any(k in vec for k in ("anger", "anxiety", "fear")) else (0.45 if vec else None),
            "stress": max(vec.get("anxiety", 0), vec.get("anger", 0) * 0.6) if vec else None,
            "uncertainty": 0.25 if source == SOURCE_SELF_REPORT else 0.55,
            "source": source,
            "status": STATUS_PROVISIONAL if vec else STATUS_EPHEMERAL,
            "evidence_refs": [],
        }
    )
    return {
        "ok": True,
        "has_signal": True,
        "emotion_state": state,
        "behaviors": behaviors,
        "source": source,
        "model_version": MODEL_VERSION,
    }


def extract_emotion_for_observation(
    service: MemoryService,
    observation_id: str,
    *,
    shadow_only: bool = True,
    write_enabled: bool = False,
    min_confidence: float = 0.35,
) -> dict[str, Any]:
    """对单条观察做情绪提取；默认只写候选审计 / 观察 metadata，不升格 LT。"""
    conn = service.db.connect()
    row = conn.execute(
        "SELECT id, content, occurred_at, created_at, metadata_json FROM observations WHERE id=?",
        (observation_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "detail": "observation_not_found"}

    content = str(row["content"] or "")
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        meta = {}

    extracted = extract_emotion_from_text(content)
    if not extracted.get("has_signal"):
        return {
            "ok": True,
            "observation_id": observation_id,
            "has_signal": False,
            "shadow_only": shadow_only,
            "candidates": [],
        }

    state = extracted["emotion_state"]
    # 过滤过低置信
    if state:
        conf = state.get("confidence_vector") or {}
        peak = max((float(v) for v in conf.values()), default=0.0)
        if peak < float(min_confidence) and extracted.get("source") != SOURCE_SELF_REPORT:
            return {
                "ok": True,
                "observation_id": observation_id,
                "has_signal": False,
                "detail": "below_min_confidence",
                "peak_confidence": peak,
                "candidates": [],
            }
        state = dict(state)
        state["evidence_refs"] = [observation_id]
        state["observed_at"] = row["occurred_at"] or row["created_at"]
        state["state_id"] = new_id("est")

    event = {
        "event_id": new_id("eev"),
        "subject_refs": ["owner"],
        "event_time": {"start": row["occurred_at"] or row["created_at"], "precision": "unknown"},
        "recorded_at": state.get("recorded_at") if state else None,
        "context_refs": [observation_id],
        "fact_evidence_refs": [observation_id],
        "emotion_state_refs": [state.get("state_id")] if state else [],
        "behavior_refs": list(extracted.get("behaviors") or []),
        "memory_status": "provisional",
        "shadow_only": bool(shadow_only),
    }

    from cognitive_memory.cognition.emotion_assess import assess_emotion_behavior
    from cognitive_memory.cognition.emotion_patterns import lookup_pattern_match

    behaviors = list(extracted.get("behaviors") or [])
    # 粗取主导情绪供模式匹配
    dominant_guess = ""
    if state:
        vec = state.get("emotion_vector") or {}
        dominant_guess = max(vec.items(), key=lambda kv: float(kv[1] or 0), default=("", 0))[0]
    pattern_hit = lookup_pattern_match(
        service, emotion=dominant_guess, behaviors=behaviors
    )

    assessment = assess_emotion_behavior(
        emotion_state=state,
        behaviors=behaviors,
        content=content,
        has_individual_pattern=bool(pattern_hit.get("has_pattern")),
        pattern_match=pattern_hit.get("pattern_match"),
    )

    meta = attach_mnemosyne(meta, emotion_state=state, event=event, assessment=assessment)
    # 回写观察 metadata（证据层，不是 LT 升格）
    with service.db.transaction() as tx:
        tx.execute(
            "UPDATE observations SET metadata_json=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), observation_id),
        )

    candidate = {
        "observation_id": observation_id,
        "emotion_state": state,
        "emotional_event": event,
        "assessment": assessment,
        "behaviors": behaviors,
        "source": extracted.get("source"),
        "shadow_only": shadow_only,
        "write_enabled": write_enabled,
        "pattern_hit": pattern_hit.get("pattern"),
    }
    n = service.store_emotion_candidates(
        observation_id=observation_id,
        candidates=[candidate],
        shadow_only=shadow_only,
    )

    # P3：仅 write_enabled 且非 shadow 且强关联 → propose candidate（永不 active）
    proposed = None
    level = str(assessment.get("association_level") or "")
    allow_write = bool(write_enabled) and not bool(shadow_only) and level == "strong"
    if allow_write:
        from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryType

        preview = content.strip()[:240]
        proposed = service.propose_memory(
            MemoryProposeIn(
                type=MemoryType.EPISODIC,
                status=MemoryStatus.CANDIDATE,
                content=f"【情绪事件候选】{preview}",
                source_ids=[observation_id],
                metadata={
                    "mnemosyne": meta.get("mnemosyne"),
                    "emotion_event": True,
                    "association_level": level,
                },
                confidence=float((assessment.get("score") or 0) / 100.0),
                idempotency_key=f"emoevt:{observation_id}",
            )
        )
        mid = getattr(proposed, "id", None)
        if mid:
            service.mark_emotion_candidates_written(observation_id, memory_id=mid)

    return {
        "ok": True,
        "observation_id": observation_id,
        "has_signal": True,
        "shadow_only": shadow_only,
        "write_enabled": bool(write_enabled),
        "stored_candidates": n,
        "candidate": candidate,
        "proposed_memory_id": getattr(proposed, "id", None) if proposed else None,
        "association_level": level,
    }
