# -*- coding: utf-8 -*-
"""Propensity / overlap — statistics only, no online RL updates."""

from __future__ import annotations

from typing import Any


def _token_set(payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    tokens: set[str] = set()
    for k, v in payload.items():
        tokens.add(f"k:{k}")
        if isinstance(v, (str, int, float, bool)):
            tokens.add(f"v:{k}={v}")
        elif isinstance(v, list):
            for item in v[:20]:
                if isinstance(item, (str, int, float)):
                    tokens.add(f"v:{k}={item}")
    return tokens


def compute_overlap(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
) -> dict[str, Any]:
    """Jaccard overlap on context/action token sets."""
    sa = _token_set(a)
    sb = _token_set(b)
    if not sa and not sb:
        return {"ok": True, "overlap": 0.0, "union": 0, "intersection": 0}
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return {
        "ok": True,
        "overlap": round(inter / union, 6),
        "union": union,
        "intersection": inter,
        "size_a": len(sa),
        "size_b": len(sb),
    }


def compute_propensity(
    experiences: list[dict[str, Any]],
    *,
    strategy_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Offline propensity snapshot from Experience rewards + scope overlap.
    Never mutates Strategy status.
    """
    if not experiences:
        return {
            "ok": True,
            "n": 0,
            "mean_reward": None,
            "success_rate": None,
            "mean_scope_overlap": None,
            "propensity_score": 0.0,
        }
    rewards: list[float] = []
    successes = 0
    overlaps: list[float] = []
    scope = dict(strategy_scope or {})
    for exp in experiences:
        r = exp.get("reward")
        if r is None:
            out = exp.get("outcome") if isinstance(exp.get("outcome"), dict) else {}
            if "reward" in out:
                r = out.get("reward")
            elif out.get("success") is True:
                r = 1.0
            elif out.get("success") is False:
                r = 0.0
        if isinstance(r, (int, float)):
            rewards.append(float(r))
            if float(r) > 0.5:
                successes += 1
        if scope:
            ctx = exp.get("context") if isinstance(exp.get("context"), dict) else {}
            act = exp.get("action") if isinstance(exp.get("action"), dict) else {}
            merged = {**ctx, **act}
            overlaps.append(float(compute_overlap(scope, merged)["overlap"]))
    n = len(experiences)
    mean_r = (sum(rewards) / len(rewards)) if rewards else None
    success_rate = (successes / len(rewards)) if rewards else None
    mean_ov = (sum(overlaps) / len(overlaps)) if overlaps else None
    # Simple blend: reward dominates; overlap is a soft prior weight
    base = mean_r if mean_r is not None else 0.0
    ov = mean_ov if mean_ov is not None else 0.0
    score = round(0.7 * base + 0.3 * ov, 6)
    return {
        "ok": True,
        "n": n,
        "n_with_reward": len(rewards),
        "mean_reward": None if mean_r is None else round(mean_r, 6),
        "success_rate": None if success_rate is None else round(success_rate, 6),
        "mean_scope_overlap": None if mean_ov is None else round(mean_ov, 6),
        "propensity_score": score,
        "suitability_score": score,
        "score_semantics": "suitability_not_action_probability",
        "probability_source": "use logged behavior_policy for off-policy evaluation",
    }
