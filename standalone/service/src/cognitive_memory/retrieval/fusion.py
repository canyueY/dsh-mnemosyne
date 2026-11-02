# -*- coding: utf-8 -*-
"""词法改进 + 多路召回融合（E09）。

- NFKC + 中文词法规范化
- 范围/时间前过滤；融合后强制复核（防越权 Bank / 不适用时间泄漏）
- 可选向量通道：去重 RRF；默认哈希向量标 hash，不冒充高质量 embedding
- 反证独立通道，预算截断时未检查 → partial/unknown
- 当前 vs 历史模式
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

from cognitive_memory.retrieval.semantic import hashed_embedding, cosine

_CN_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def nfkc_normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def lexical_tokens(text: str) -> list[str]:
    """中文：单字 + bigram；英文：小写词。先 NFKC。"""
    norm = nfkc_normalize(text).lower()
    raw = _WORD_RE.findall(norm)
    out: list[str] = []
    for tok in raw:
        out.append(tok)
        if _CN_RE.fullmatch(tok) and len(tok) >= 2:
            for i in range(len(tok) - 1):
                out.append(tok[i : i + 2])
    # 相邻跨 token bigram（中英混合）
    for a, b in zip(raw, raw[1:]):
        if _CN_RE.fullmatch(a) or _CN_RE.fullmatch(b):
            out.append(f"{a}{b}")
    return out


def lexical_score(query: str, content: str) -> float:
    qt = set(lexical_tokens(query))
    ct = set(lexical_tokens(content))
    if not qt:
        return 0.0
    if not ct:
        return 0.0
    inter = len(qt & ct)
    return min(1.0, inter / max(1, len(qt)))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def prefilter_candidates(
    items: Iterable[dict[str, Any]],
    *,
    allowed_banks: set[str] | None,
    mode: str = "current",
    at: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """召回前尽量过滤越权 Bank / 不适用时间。"""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    at_dt = _parse_ts(at) or datetime.now(timezone.utc)
    for raw in items:
        item = dict(raw)
        bank = str(item.get("bank_id") or (item.get("metadata") or {}).get("bank_id") or "personal")
        if allowed_banks is not None and bank not in allowed_banks:
            dropped.append({**item, "_drop_reason": "bank_denied"})
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        vf = _parse_ts(item.get("validity_from") or meta.get("validity_from"))
        vt = _parse_ts(item.get("validity_to") or meta.get("validity_to") or item.get("expires_at"))
        if mode == "current":
            if vf and at_dt < vf:
                dropped.append({**item, "_drop_reason": "not_yet_valid"})
                continue
            if vt and at_dt >= vt:
                dropped.append({**item, "_drop_reason": "expired_for_current"})
                continue
        kept.append(item)
    return kept, dropped


def postfilter_recheck(
    items: Iterable[dict[str, Any]],
    *,
    allowed_banks: set[str] | None,
    mode: str = "current",
    at: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """输出前必复核，防止融合路径泄漏。"""
    return prefilter_candidates(items, allowed_banks=allowed_banks, mode=mode, at=at)


def rrf_fuse(
    rankings: list[list[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    """去重 Reciprocal Rank Fusion。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, doc_id in enumerate(ranking, start=1):
            if doc_id in seen:
                continue
            seen.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _hash_channel_label(provider: str | None) -> dict[str, Any]:
    if provider in {None, "", "hash", "hashed", "offline_hash"}:
        return {
            "embedding_kind": "hash",
            "quality": "offline_hash_not_semantic_model",
            "do_not_overclaim": True,
        }
    return {
        "embedding_kind": "provider",
        "provider": provider,
        "quality": "external",
        "do_not_overclaim": False,
    }


def fuse_channels(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    allowed_banks: set[str] | None = None,
    mode: str = "current",
    at: str | None = None,
    limit: int = 8,
    use_vector: bool = True,
    embedding_provider: str | None = None,
    counterevidence: list[dict[str, Any]] | None = None,
    counterevidence_budget: int = 3,
) -> dict[str, Any]:
    """多路融合主入口。"""
    pre, dropped_pre = prefilter_candidates(
        candidates, allowed_banks=allowed_banks, mode=mode, at=at
    )
    # 词法通道排序
    lexical_ranked = sorted(
        pre,
        key=lambda x: lexical_score(query, str(x.get("content") or "")),
        reverse=True,
    )
    lexical_ids = [str(x.get("id")) for x in lexical_ranked if x.get("id")]

    rankings = [lexical_ids]
    channel_meta: list[dict[str, Any]] = [
        {"name": "lexical_nfkc", "count": len(lexical_ids)}
    ]
    emb_meta = _hash_channel_label(embedding_provider)
    if use_vector and pre:
        q_vec = hashed_embedding(nfkc_normalize(query))
        vec_scored = []
        for item in pre:
            text = nfkc_normalize(str(item.get("content") or ""))
            score = max(0.0, cosine(q_vec, hashed_embedding(text)))
            vec_scored.append((str(item["id"]), score))
        vec_scored.sort(key=lambda x: x[1], reverse=True)
        rankings.append([doc_id for doc_id, _ in vec_scored])
        channel_meta.append(
            {
                "name": "vector",
                "count": len(vec_scored),
                **emb_meta,
            }
        )

    fused_scores = rrf_fuse(rankings)
    by_id = {str(x["id"]): dict(x) for x in pre if x.get("id")}
    # 各路显式 rank（MA08）
    channel_ranks: dict[str, dict[str, int]] = {}
    channel_names = [c["name"] for c in channel_meta]
    for ch_name, ranking in zip(channel_names, rankings):
        channel_ranks[ch_name] = {doc_id: i + 1 for i, doc_id in enumerate(ranking)}

    fused_items = []
    for doc_id, score in sorted(fused_scores.items(), key=lambda kv: kv[1], reverse=True):
        item = by_id.get(doc_id)
        if not item:
            continue
        pack = dict(item)
        pack["fusion_score"] = round(float(score), 6)
        pack["sort_score"] = pack["fusion_score"]
        pack["lexical_score"] = round(lexical_score(query, str(item.get("content") or "")), 4)
        # 本地哈希相似度 ≠ 语义理解
        pack["candidate_similarity"] = pack["lexical_score"]
        pack["sources"] = [
            {"channel": ch, "rank": ranks.get(doc_id)}
            for ch, ranks in channel_ranks.items()
            if doc_id in ranks
        ]
        if use_vector:
            pack["embedding_meta"] = emb_meta
        fused_items.append(pack)

    # 反证独立通道（不进入负概率随机游走）
    ce_in = list(counterevidence or [])
    ce_pre, ce_drop = prefilter_candidates(
        ce_in, allowed_banks=allowed_banks, mode=mode, at=at
    )
    ce_sorted = sorted(
        ce_pre,
        key=lambda x: lexical_score(query, str(x.get("content") or "")),
        reverse=True,
    )
    ce_checked = ce_sorted[: max(0, int(counterevidence_budget))]
    ce_unchecked = ce_sorted[max(0, int(counterevidence_budget)) :]
    for item in ce_checked:
        item["channel"] = "counterevidence"
        item["checked"] = True
    for item in ce_unchecked:
        item["channel"] = "counterevidence"
        item["checked"] = False

    truncated = fused_items[: max(1, int(limit))]
    # 融合后强制复核
    safe, leaked = postfilter_recheck(
        truncated, allowed_banks=allowed_banks, mode=mode, at=at
    )
    ce_safe, ce_leaked = postfilter_recheck(
        ce_checked, allowed_banks=allowed_banks, mode=mode, at=at
    )

    status = "ok"
    reason_codes: list[str] = []
    if ce_unchecked:
        status = "partial"
        reason_codes.append("counterevidence_unchecked")
    if not safe and not ce_safe:
        status = "unknown"
        reason_codes.append("no_safe_items")
    if leaked or ce_leaked:
        reason_codes.append("postfilter_blocked_leak")

    # 无真实 embedder 时不得冒充语义模型（MA10）
    semantic_model = (
        "unavailable"
        if emb_meta.get("embedding_kind") == "hash" or not use_vector
        else str(emb_meta.get("provider") or "external")
    )

    return {
        "ok": True,
        "status": status,
        "fusion_method": "rrf" if len(rankings) > 1 else "none",
        "semantic_model": semantic_model,
        "items": safe,
        "counterevidence": ce_safe,
        "counterevidence_unchecked": ce_unchecked,
        "dropped_prefilter": dropped_pre + ce_drop,
        "blocked_postfilter": leaked + ce_leaked,
        "channels": channel_meta,
        "mode": mode,
        "reason_codes": reason_codes,
        "embedding_meta": emb_meta,
        "notes": {
            "rrf_dedup": True,
            "hash_not_hq_embedding": emb_meta.get("embedding_kind") == "hash",
            "counterevidence_independent": True,
            "postfilter_mandatory": True,
            "candidate_similarity_is_not_fact": True,
        },
    }


def ablation_report(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    allowed_banks: set[str] | None = None,
    mode: str = "current",
    limit: int = 5,
) -> dict[str, Any]:
    """可复现消融：基线（原文序）/ 词法 / 融合；离线本地，无付费模型。"""
    baseline_ids = [str(c.get("id")) for c in candidates[:limit]]
    lexical_only = sorted(
        candidates,
        key=lambda x: lexical_score(query, str(x.get("content") or "")),
        reverse=True,
    )
    lexical_ids = [str(c.get("id")) for c in lexical_only[:limit]]
    fused = fuse_channels(
        query=query,
        candidates=candidates,
        allowed_banks=allowed_banks,
        mode=mode,
        limit=limit,
        use_vector=True,
        embedding_provider="hash",
    )
    fusion_ids = [str(c.get("id")) for c in fused["items"]]

    def _overlap(a: list[str], b: list[str]) -> float:
        if not a or not b:
            return 0.0
        return len(set(a) & set(b)) / max(len(a), len(b))

    return {
        "ok": True,
        "query": query,
        "baseline_ids": baseline_ids,
        "lexical_ids": lexical_ids,
        "fusion_ids": fusion_ids,
        "metrics": {
            "baseline_vs_lexical_overlap": round(_overlap(baseline_ids, lexical_ids), 4),
            "lexical_vs_fusion_overlap": round(_overlap(lexical_ids, fusion_ids), 4),
            "baseline_vs_fusion_overlap": round(_overlap(baseline_ids, fusion_ids), 4),
            "candidate_n": len(candidates),
            "cost": {"external_model_calls": 0, "embedding_kind": "hash"},
        },
        "fusion": fused,
        "reproducible": True,
        "offline_only": True,
    }
