# -*- coding: utf-8 -*-
"""P2：情绪—行为关联评估（E/T/P/A）。S 不是因果概率。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cognitive_memory.domain.emotion import MODEL_VERSION, SOURCE_SELF_REPORT

DEFAULT_WEIGHTS = {"E": 0.30, "T": 0.20, "P": 0.30, "A": 0.20}


def _peak_emotion_evidence(state: dict[str, Any] | None) -> tuple[float, float, str]:
    """返回 (强度峰值, 置信峰值, 主导情绪)。"""
    if not state:
        return 0.0, 0.0, ""
    vec = state.get("emotion_vector") or {}
    conf = state.get("confidence_vector") or {}
    best_e, best_c, best_k = 0.0, 0.0, ""
    for k, v in vec.items():
        try:
            e = float(v)
            c = float(conf.get(k) or 0.0)
        except (TypeError, ValueError):
            continue
        if e * c >= best_e * best_c:
            best_e, best_c, best_k = e, c, str(k)
    return best_e, best_c, best_k


def assess_emotion_behavior(
    *,
    emotion_state: dict[str, Any] | None,
    behaviors: list[str],
    content: str = "",
    has_individual_pattern: bool = False,
    pattern_match: float | None = None,
    weights: dict[str, float] | None = None,
    method_version: str = MODEL_VERSION,
) -> dict[str, Any]:
    """产出关联评估。缺证据时不得输出强关联。"""
    w = dict(DEFAULT_WEIGHTS)
    if isinstance(weights, dict):
        for k in ("E", "T", "P", "A"):
            if k in weights:
                try:
                    w[k] = float(weights[k])
                except (TypeError, ValueError):
                    pass

    intensity, confidence, dominant = _peak_emotion_evidence(emotion_state)
    source = str((emotion_state or {}).get("source") or "")
    alternatives: list[str] = []
    counter: list[str] = []

    # E: 情绪证据 0–100
    e_score = 100.0 * intensity * (0.5 + 0.5 * confidence)
    if source == SOURCE_SELF_REPORT:
        e_score = min(100.0, e_score + 15.0)
    if not emotion_state:
        e_score = 0.0
        alternatives.append("no_emotion_evidence")

    # T: 时间关联——同条观察内共现给中等分；无行为则低
    if behaviors and emotion_state:
        t_score = 70.0
    elif behaviors and not emotion_state:
        t_score = 40.0
        alternatives.append("behavior_without_emotion_label")
    elif emotion_state and not behaviors:
        t_score = 35.0
        alternatives.append("emotion_without_observed_behavior")
    else:
        t_score = 0.0

    # P: 个体模式——无历史时未知，用中性保守值并标记
    p_unknown = False
    if has_individual_pattern and pattern_match is not None:
        p_score = max(0.0, min(100.0, float(pattern_match) * 100.0))
    else:
        p_score = 50.0
        p_unknown = True
        alternatives.append("no_individual_pattern_yet")

    # A: 替代解释审查——有反证线索则降，审查越充分分越高
    a_score = 55.0
    text = content or ""
    if any(x in text for x in ("其实是", "因为忙", "信号不好", "在开会", "不是生气", "别误会")):
        alternatives.append("situational_alternative")
        a_score = 35.0
        counter.append("explicit_alternative_in_text")
    if source == SOURCE_SELF_REPORT and "导致我" in text:
        # 当事人自称因果仍只作证据，不自动升格
        a_score = min(100.0, a_score + 10.0)

    s = w["E"] * e_score + w["T"] * t_score + w["P"] * p_score + w["A"] * a_score

    # 证据门槛：缺情绪/时序不得强关联；无个体史封顶 moderate（不是直接打成 weak）
    reasons: list[str] = []
    if e_score < 25:
        reasons.append("insufficient_emotion_evidence")
    if t_score < 30:
        reasons.append("weak_temporal_coupling")
    if p_unknown:
        reasons.append("pattern_unknown_cap")

    hard_weak = ("insufficient_emotion_evidence" in reasons) or (
        "weak_temporal_coupling" in reasons
    )
    if s < 40 or hard_weak:
        level = "none" if s < 40 else "weak"
    elif s < 55:
        level = "weak"
    elif s < 70 or p_unknown:
        level = "moderate"
    else:
        level = "strong"

    if counter:
        if level == "strong":
            level = "conflict"
        elif level == "moderate":
            level = "weak"

    return {
        "ok": True,
        "scores": {
            "E": round(e_score, 1),
            "T": round(t_score, 1),
            "P": round(p_score, 1),
            "A": round(a_score, 1),
        },
        "weights": w,
        "score": round(s, 1),
        "association_level": level,
        "dominant_emotion": dominant,
        "pattern_unknown": p_unknown,
        "alternative_explanations": alternatives,
        "counter_evidence": counter,
        "gate_reasons": reasons,
        "method_version": method_version,
        "assessed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "S is engineering score, not causal probability",
        "reevaluation_triggers": ["user_correction", "new_outcome", "counter_evidence"],
    }
