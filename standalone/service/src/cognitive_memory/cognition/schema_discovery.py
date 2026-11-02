# -*- coding: utf-8 -*-
"""离线图式发现：从记忆文本聚类生成 CognitiveSchema 候选（不写第二套 LT 事实）。"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.core.service import MemoryService

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2}|[A-Za-z0-9_]{3,}")
_STOP = frozenset(
    {
        "主人",
        "丛雨",
        "微信",
        "对话",
        "片段",
        "他人",
        "视野",
        "拓宽",
        "眼界",
        "今天",
        "昨天",
        "可以",
        "没有",
        "什么",
        "这个",
        "那个",
        "我们",
        "还是",
        "因为",
        "所以",
        "然后",
        "已经",
        "觉得",
        "知道",
        "一下",
        "QQ群",
        "episodic",
        "working",
        "semantic",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        t = m.group(0).lower()
        if t in _STOP or len(t) < 2:
            continue
        out.add(t)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def discover_schema_candidates(
    service: MemoryService,
    *,
    limit_memories: int = 200,
    min_cluster: int = 3,
    min_similarity: float = 0.22,
    persist: bool = True,
) -> dict[str, Any]:
    """规则聚类发现图式候选；默认仅 PROPOSED，不自动 ACTIVE。"""
    conn = service.db.connect()
    rows = conn.execute(
        """
        SELECT id, content, type, status, source_ids_json
        FROM memories
        WHERE status IN ('active','candidate')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (int(limit_memories),),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        content = str(r["content"] or "").strip()
        if len(content) < 8:
            continue
        toks = _tokens(content)
        if len(toks) < 2:
            continue
        items.append(
            {
                "id": str(r["id"]),
                "content": content,
                "tokens": toks,
                "type": str(r["type"] or ""),
            }
        )

    # 贪心聚类：以未分配项为种子，吸收高重叠项
    used: set[str] = set()
    clusters: list[list[dict[str, Any]]] = []
    for seed in items:
        if seed["id"] in used:
            continue
        group = [seed]
        used.add(seed["id"])
        for other in items:
            if other["id"] in used:
                continue
            if _jaccard(seed["tokens"], other["tokens"]) >= min_similarity:
                group.append(other)
                used.add(other["id"])
        if len(group) >= min_cluster:
            clusters.append(group)

    candidates: list[dict[str, Any]] = []
    for group in clusters[:40]:
        # 主题：簇内最高频 token
        freq: dict[str, int] = defaultdict(int)
        for g in group:
            for t in g["tokens"]:
                freq[t] += 1
        top_tokens = sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:6]
        topic = "、".join(t for t, _ in top_tokens[:3]) or "未命名模式"
        mem_ids = [g["id"] for g in group]
        conf = min(0.72, 0.28 + 0.06 * len(group) + 0.02 * len(top_tokens))
        schema = {
            "id": f"sch_{uuid.uuid4().hex[:14]}",
            "name": f"模式·{topic}",
            "description": (
                f"从 {len(group)} 条相关记忆中观察到围绕「{topic}」的重复主题；"
                "此为认知假设，非正式个人事实。"
            ),
            "type": "CONTEXTUAL",
            "conditions": [f"上下文涉及 {t}" for t, _ in top_tokens[:3]],
            "expectations": [f"相关对话可能再次出现 {topic}"],
            "conclusions": [
                f"经验簇显示「{topic}」具有跨条目重复性（候选，待验证）"
            ],
            "applicability": {
                "scope": [t for t, _ in top_tokens[:4]],
                "exclusions": ["单次提及不足以否定或确认"],
            },
            "evidence": {
                "supporting": mem_ids[:12],
                "counter": [],
            },
            "relations": {
                "supports": [],
                "contradicts": [],
                "generalizes": [],
                "specializes": [],
                "dependsOn": [],
                "refines": [],
            },
            "confidence": round(conf, 3),
            "state": "PROPOSED",
            "version": 1,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if persist:
            sid = service.store_cognitive_schema(schema)
            schema["id"] = sid
        candidates.append(schema)

    return {
        "ok": True,
        "scanned_memories": len(items),
        "clusters": len(clusters),
        "candidates": candidates,
        "count": len(candidates),
        "generated_at": _now(),
        "note": "候选图式仅引用 memory_id；不另建个人 LT 权威库",
    }
