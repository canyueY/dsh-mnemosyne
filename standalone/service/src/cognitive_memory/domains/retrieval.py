# -*- coding: utf-8 -*-
"""Domain-aware retrieval gate — music catalog must not pollute unrelated context."""

from __future__ import annotations

import re
from typing import Any, Iterable

MUSIC_INTENT_RE = re.compile(
    r"(音乐|听歌|听的歌|歌曲|单曲|专辑|歌单|网易云|播放|点歌|歌手|乐队|曲风|旋律|"
    r"首歌|几首|放首|来首|BGM|bgm|"
    r"playlist|album|artist|track|song|music|spotify|netease)",
    re.I,
)


def detect_music_intent(query: str) -> bool:
    return bool(MUSIC_INTENT_RE.search(query or ""))


def _memory_domain(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return ""
    d = str(meta.get("domain") or "").strip().lower()
    if d:
        return d
    mnemo = meta.get("mnemosyne") if isinstance(meta.get("mnemosyne"), dict) else {}
    return str(mnemo.get("domain") or "").strip().lower()


def apply_domain_gate(
    memories: Iterable[Any],
    *,
    domain: str | None = None,
    query: str = "",
) -> list[Any]:
    """Filter memories by domain.

    Rules:
    - domain=music (explicit) → keep music + undomain'd (generic) hits
    - domain set to other → exclude music
    - domain empty + no music intent → exclude music-tagged memories
    - domain empty + music intent → keep all (music boosted later by caller if needed)
    """
    items = list(memories)
    dom = str(domain or "").strip().lower()
    music_intent = detect_music_intent(query)

    if dom == "music":
        return [
            m
            for m in items
            if _memory_domain(getattr(m, "metadata", None) or {}) in ("", "music")
            or not _memory_domain(getattr(m, "metadata", None) or {})
        ]

    if dom and dom != "music":
        return [
            m
            for m in items
            if _memory_domain(getattr(m, "metadata", None) or {}) != "music"
        ]

    if not music_intent:
        return [
            m
            for m in items
            if _memory_domain(getattr(m, "metadata", None) or {}) != "music"
        ]

    return items


def boost_music_domain(memories: list[Any], *, query: str = "", domain: str | None = None) -> list[Any]:
    """Stable sort: music-domain memories first when music intent / domain=music."""
    if not memories:
        return []
    if str(domain or "").lower() != "music" and not detect_music_intent(query):
        return memories
    scored: list[tuple[int, int, Any]] = []
    for i, m in enumerate(memories):
        is_music = 1 if _memory_domain(getattr(m, "metadata", None) or {}) == "music" else 0
        scored.append((is_music, -i, m))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [m for _, _, m in scored]
