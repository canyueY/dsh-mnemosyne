# -*- coding: utf-8 -*-
"""可选语义空间：字符 n-gram 哈希向量 + 余弦相似度。

这是 P2 的最小实现，不替换现有 FTS/planner：
- 默认零依赖，离线可用；
- 可注入 `EmbeddingProvider` 换成模型向量；
- 输出只作为 recall 的一个加分信号，时间/情境/证据仍由现有链路负责。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol, Sequence

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> Sequence[float]: ...


def _tokens(text: str) -> list[str]:
    raw = _TOKEN_RE.findall((text or "").lower())
    if not raw:
        return []
    # 中文单字 + 相邻 bigram；英文保留词
    out = list(raw)
    for a, b in zip(raw, raw[1:]):
        out.append(f"{a}{b}")
    return out


def hashed_embedding(text: str, *, dims: int = 512) -> list[float]:
    """确定性哈希向量；无需模型，适合测试/离线回退。"""
    vec = [0.0] * max(16, int(dims))
    counts = Counter(_tokens(text))
    for tok, n in counts.items():
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        idx = h % len(vec)
        sign = 1.0 if (h >> 63) & 1 else -1.0
        vec[idx] += sign * (1.0 + math.log1p(n))
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(x) * float(x) for x in a[:n]))
    nb = math.sqrt(sum(float(x) * float(x) for x in b[:n]))
    if na <= 0 or nb <= 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


class SemanticScorer:
    """把语义相似度转为 0..1 的排序加分。"""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider = provider

    def embed(self, text: str) -> list[float]:
        if self._provider is not None:
            return [float(x) for x in self._provider.embed(text)]
        return hashed_embedding(text)

    def score(self, query: str, text: str) -> float:
        return max(0.0, cosine(self.embed(query), self.embed(text)))

    @staticmethod
    def blend(lexical: float, semantic: float, *, weight: float = 0.25) -> float:
        """语义只做加分，不压过证据/时间主排序。"""
        w = max(0.0, min(0.6, float(weight)))
        return (1.0 - w) * float(lexical) + w * float(semantic)
