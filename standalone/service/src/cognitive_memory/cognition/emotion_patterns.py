# -*- coding: utf-8 -*-
"""P4：个体情绪—行为模式扫描（趋势候选；默认不升格 LT）。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.emotion import get_mnemosyne
from cognitive_memory.domain.spatiotemporal import parse_ts


def _day(ts: str | None) -> str:
    dt = parse_ts(ts)
    return dt.strftime("%Y-%m-%d") if dt else "?"


def scan_emotion_behavior_patterns(
    service: MemoryService,
    *,
    limit_obs: int = 400,
    min_days: int = 2,
    min_count: int = 2,
) -> dict[str, Any]:
    """从观察 mnemosyne / emotion_candidates 汇总 (emotion, behavior) 跨日共现。"""
    pairs: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"days": set(), "count": 0, "obs_ids": []}
    )

    with service.db.exclusive() as conn:
        rows = conn.execute(
            """
            SELECT id, occurred_at, created_at, metadata_json
            FROM observations
            ORDER BY coalesce(occurred_at, created_at) DESC
            LIMIT ?
            """,
            (int(limit_obs),),
        ).fetchall()
        if conn.in_transaction:
            conn.commit()

    for r in rows:
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        pack = get_mnemosyne(meta)
        state = pack.get("emotion_state") if isinstance(pack.get("emotion_state"), dict) else {}
        assessment = pack.get("assessment") if isinstance(pack.get("assessment"), dict) else {}
        event = pack.get("emotional_event") if isinstance(pack.get("emotional_event"), dict) else {}
        dominant = str(assessment.get("dominant_emotion") or "").strip()
        if not dominant and state:
            vec = state.get("emotion_vector") or {}
            dominant = max(vec.items(), key=lambda kv: float(kv[1] or 0), default=("", 0))[0]
        behaviors = list(event.get("behavior_refs") or [])
        if not behaviors:
            # 兼容候选 payload 结构
            continue
        day = _day(r["occurred_at"] or r["created_at"])
        oid = str(r["id"])
        for beh in behaviors:
            key = (dominant or "unknown", str(beh))
            pairs[key]["days"].add(day)
            pairs[key]["count"] += 1
            if len(pairs[key]["obs_ids"]) < 8:
                pairs[key]["obs_ids"].append(oid)

    # 也扫 emotion_candidates 表（可能比 observation metadata 更新）
    for item in service.list_emotion_candidates(limit=min(200, int(limit_obs)), written=None):
        payload = item.get("payload") or {}
        assessment = payload.get("assessment") or {}
        dominant = str(assessment.get("dominant_emotion") or "").strip() or "unknown"
        for beh in payload.get("behaviors") or []:
            key = (dominant, str(beh))
            day = _day(item.get("created_at"))
            pairs[key]["days"].add(day)
            pairs[key]["count"] += 1
            oid = str(item.get("observation_id") or "")
            if oid and oid not in pairs[key]["obs_ids"] and len(pairs[key]["obs_ids"]) < 8:
                pairs[key]["obs_ids"].append(oid)

    candidates: list[dict[str, Any]] = []
    for (emo, beh), info in pairs.items():
        days = sorted(d for d in info["days"] if d != "?")
        if len(days) < int(min_days) or int(info["count"]) < int(min_count):
            continue
        candidates.append(
            {
                "kind": "emotion_behavior",
                "emotion": emo,
                "behavior": beh,
                "days": days,
                "day_count": len(days),
                "count": int(info["count"]),
                "observation_ids": list(info["obs_ids"]),
                "note": "个体反应趋势候选，非已确认因果",
            }
        )
    candidates.sort(key=lambda c: (c["day_count"], c["count"]), reverse=True)
    return {
        "ok": True,
        "pattern_candidates": candidates,
        "count": len(candidates),
        "scanned_limit": int(limit_obs),
        "min_days": int(min_days),
        "min_count": int(min_count),
    }


def lookup_pattern_match(
    service: MemoryService,
    *,
    emotion: str,
    behaviors: list[str],
    limit_obs: int = 200,
) -> dict[str, Any]:
    """给评估用：若历史中有同情绪+行为跨日共现，返回 match∈[0,1]。"""
    pack = scan_emotion_behavior_patterns(
        service, limit_obs=limit_obs, min_days=2, min_count=2
    )
    emo = (emotion or "").strip()
    beh_set = {str(b) for b in (behaviors or [])}
    best = 0.0
    hit = None
    for c in pack.get("pattern_candidates") or []:
        if c.get("emotion") != emo:
            continue
        if str(c.get("behavior")) not in beh_set:
            continue
        # 天数越多 match 越高，封顶 1
        score = min(1.0, 0.35 + 0.2 * float(c.get("day_count") or 0))
        if score > best:
            best = score
            hit = c
    return {
        "has_pattern": hit is not None,
        "pattern_match": best if hit else None,
        "pattern": hit,
    }
