# -*- coding: utf-8 -*-
"""可解释排序：Score = wr*R + wt*T + ws*S + we*E。"""

from __future__ import annotations

import re
from typing import Any

from cognitive_memory.domain.models import Memory, MemoryStatus
from cognitive_memory.domain.spatiotemporal import spatiotemporal_score

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2}|[A-Za-z0-9_]{3,}")

DEFAULT_WEIGHTS = {"r": 0.45, "t": 0.20, "s": 0.20, "e": 0.15}


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def relevance_score(content: str, query: str) -> float:
    qt, ct = _tokens(query), _tokens(content)
    if not qt or not ct:
        q = (query or "").strip().lower()
        c = (content or "").strip().lower()
        if not q or not c:
            return 0.0
        return 1.0 if q in c else 0.0
    inter = len(qt & ct)
    return min(1.0, inter / max(1, len(qt)))


def status_score(status: MemoryStatus | str) -> float:
    s = status.value if isinstance(status, MemoryStatus) else str(status or "")
    return {
        "active": 1.0,
        "candidate": 0.55,
        "superseded": 0.15,
        "invalidated": 0.0,
    }.get(s, 0.3)


def evidence_score(memory: Memory) -> float:
    n = len(memory.source_ids or [])
    meta = memory.metadata if isinstance(memory.metadata, dict) else {}
    ev = meta.get("evidence_count")
    try:
        if ev is not None:
            n = max(n, int(ev))
    except (TypeError, ValueError):
        pass
    if n <= 0:
        return 0.35
    if n == 1:
        return 0.55
    if n == 2:
        return 0.75
    return 1.0


def score_memory(
    memory: Memory,
    query: str,
    *,
    mode: str = "current",
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    r = relevance_score(memory.content, query)
    t = float(
        spatiotemporal_score(memory.metadata, expires_at=memory.expires_at, mode=mode)
    )
    t = max(0.0, min(1.5, t)) / 1.5  # normalize typical 0..1.5 → 0..1
    s = status_score(memory.status)
    e = evidence_score(memory)
    total = w["r"] * r + w["t"] * t + w["s"] * s + w["e"] * e
    return {
        "total": round(total, 4),
        "R": round(r, 4),
        "T": round(t, 4),
        "S": round(s, 4),
        "E": round(e, 4),
        "weights": w,
    }
