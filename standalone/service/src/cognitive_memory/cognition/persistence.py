# -*- coding: utf-8 -*-
"""P5：持续性 / 跨场景模式候选（只产趋势候选，不自动升格为事实）。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.spatiotemporal import get_spatiotemporal, parse_ts

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_STOP = frozenset(
    {
        "主人",
        "丛雨",
        "微信",
        "对话",
        "片段",
        "系统",
        "今天",
        "昨天",
        "明天",
        "下午",
        "晚上",
        "早上",
        "继续",
        "真的",
        "一个",
        "我们",
        "什么",
        "这个",
        "那个",
        "可以",
        "没有",
        "就是",
        "还是",
        "因为",
        "所以",
        "然后",
        "已经",
        "觉得",
        "知道",
        "一下",
        "到很",
        "很晚",
        "又去",
        "真开",
    }
)


def _day(ts: str | None) -> str:
    dt = parse_ts(ts)
    return dt.strftime("%Y-%m-%d") if dt else "?"


def _extract_topics(content: str, *, limit: int = 16) -> list[str]:
    """CJK 重叠二字/三字，避免贪心块把关键词吞掉（如「滑板」→「下午滑板」）。"""
    out: list[str] = []
    seen: set[str] = set()
    for run in _CJK_RUN.findall(content):
        if len(run) < 2:
            continue
        cands: list[str] = []
        for i in range(len(run) - 1):
            cands.append(run[i : i + 2])
        for i in range(len(run) - 2):
            cands.append(run[i : i + 3])
        for tok in cands:
            if tok in _STOP or tok in seen:
                continue
            seen.add(tok)
            out.append(tok)
            if len(out) >= limit:
                return out
    return out


def scan_persistence_patterns(
    service: MemoryService,
    *,
    limit_obs: int = 400,
    min_days: int = 3,
    min_count: int = 3,
) -> dict[str, Any]:
    """扫描观察：跨日重复主题 → habit/interest 趋势候选；同空间重复 → 场景条件化候选。"""
    with service.db.exclusive() as conn:
        rows = conn.execute(
            """
            SELECT id, content, occurred_at, created_at, metadata_json
            FROM observations
            ORDER BY coalesce(occurred_at, created_at) DESC
            LIMIT ?
            """,
            (int(limit_obs),),
        ).fetchall()
        if conn.in_transaction:
            conn.commit()

    topic_days: dict[str, set[str]] = defaultdict(set)
    topic_count: dict[str, int] = defaultdict(int)
    topic_spaces: dict[str, set[str]] = defaultdict(set)
    topic_samples: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        content = str(row["content"] or "")
        day = _day(row["occurred_at"] or row["created_at"])
        meta = {}
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        space = (get_spatiotemporal(meta).get("spatial") or {}).get("name") or (
            get_spatiotemporal(meta).get("spatial") or {}
        ).get("spaceId")
        for tok in _extract_topics(content):
            topic_days[tok].add(day)
            topic_count[tok] += 1
            if space:
                topic_spaces[tok].add(str(space))
            if len(topic_samples[tok]) < 3:
                topic_samples[tok].append(content[:100])

    habits: list[dict[str, Any]] = []
    scene_conditioned: list[dict[str, Any]] = []
    for topic, days in topic_days.items():
        if topic_count[topic] < min_count:
            continue
        if len(days) >= min_days:
            day_n = len(days)
            # 跨日重复：次数高且天数多 → interest_trend；否则 habit_trend
            interest_like = day_n >= max(min_days, 4) and topic_count[topic] >= max(min_count, 4)
            habits.append(
                {
                    "topic": topic,
                    "kind": "interest_trend" if interest_like else "habit_trend",
                    "status": "STALE_CANDIDATE",  # 沉默≠否定；仅降权候选
                    "days": sorted(d for d in days if d != "?")[-12:],
                    "count": topic_count[topic],
                    "samples": topic_samples[topic],
                    "confidence": min(0.75, 0.35 + 0.05 * day_n + 0.02 * topic_count[topic]),
                    "note": "趋势候选，非已确认偏好；长期无支持仅降权",
                }
            )
        spaces = topic_spaces.get(topic) or set()
        if len(spaces) == 1 and topic_count[topic] >= min_count:
            scene_conditioned.append(
                {
                    "topic": topic,
                    "kind": "scene_conditioned",
                    "space": next(iter(spaces)),
                    "count": topic_count[topic],
                    "samples": topic_samples[topic],
                    "confidence": 0.4,
                    "note": "场景关联候选，不自动等同偏好",
                }
            )

    habits.sort(key=lambda x: (-len(x["days"]), -x["count"]))
    scene_conditioned.sort(key=lambda x: -x["count"])
    interest = [h for h in habits if h.get("kind") == "interest_trend"]
    habit_only = [h for h in habits if h.get("kind") != "interest_trend"]
    return {
        "ok": True,
        "scanned_observations": len(rows),
        "habit_candidates": habits[:20],
        "interest_candidates": interest[:20],
        "habit_only_candidates": habit_only[:20],
        "scene_candidates": scene_conditioned[:20],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
