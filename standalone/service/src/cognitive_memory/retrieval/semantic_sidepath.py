# -*- coding: utf-8 -*-
"""P3 semantic side-path — assistive recall only.

Hard rules:
- Never authorizes Verified / lifecycle changes.
- Never replaces Evidence or FTS as sole truth.
- Output is tagged assistive_only / authoritative=false.
"""

from __future__ import annotations

from typing import Any

from cognitive_memory.retrieval.semantic import SemanticScorer


def assist_rerank(
    query: str,
    items: list[dict[str, Any]],
    *,
    weight: float = 0.25,
    scorer: SemanticScorer | None = None,
    content_key: str = "content",
) -> dict[str, Any]:
    """Blend semantic score into existing lexical totals. Does not drop FTS hits."""
    scorer = scorer or SemanticScorer()
    assisted: list[dict[str, Any]] = []
    for it in items:
        row = dict(it)
        text = str(row.get(content_key) or row.get("surface") or "")
        sem = scorer.score(query, text)
        score = dict(row.get("score") or {})
        lexical = float(score.get("total") or score.get("R") or 0.0)
        blended = SemanticScorer.blend(lexical, sem, weight=weight)
        score = {
            **score,
            "total": round(blended, 4),
            "semantic": round(sem, 4),
            "lexical_total": round(lexical, 4),
            "semantic_assist": True,
        }
        row["score"] = score
        row["semantic_assist"] = True
        row["authoritative"] = False
        assisted.append(row)
    assisted.sort(key=lambda x: float((x.get("score") or {}).get("total") or 0), reverse=True)
    return {
        "ok": True,
        "authoritative": False,
        "assistive_only": True,
        "items": assisted,
        "note": "semantic_is_assistive_never_verification",
    }


def pool_suggest(
    query: str,
    corpus: list[dict[str, Any]],
    *,
    limit: int = 5,
    min_score: float = 0.35,
    scorer: SemanticScorer | None = None,
    content_key: str = "content",
    id_key: str = "id",
) -> dict[str, Any]:
    """Scan a candidate pool by semantic similarity (side suggestions only)."""
    scorer = scorer or SemanticScorer()
    scored: list[dict[str, Any]] = []
    for doc in corpus:
        text = str(doc.get(content_key) or doc.get("surface") or "")
        if not text.strip():
            continue
        sem = scorer.score(query, text)
        if sem < min_score:
            continue
        scored.append(
            {
                "id": doc.get(id_key),
                "content": text,
                "semantic": round(sem, 4),
                "authoritative": False,
                "assistive_only": True,
                "source": "semantic_pool",
            }
        )
    scored.sort(key=lambda x: float(x.get("semantic") or 0), reverse=True)
    return {
        "ok": True,
        "authoritative": False,
        "assistive_only": True,
        "items": scored[: max(0, int(limit))],
        "note": "pool_suggestions_do_not_verify_claims",
    }


def evaluate_assist(
    query: str,
    *,
    fts_ids: list[str],
    relevant_ids: list[str],
    pool: list[dict[str, Any]],
    limit: int = 5,
    min_score: float = 0.2,
) -> dict[str, Any]:
    """Offline eval: does assist recover relevant ids FTS missed? Never writes."""
    rel = set(relevant_ids)
    fts = set(fts_ids)
    fts_hit = len(rel & fts)
    sug = pool_suggest(query, pool, limit=limit, min_score=min_score)
    sug_ids = {str(x.get("id")) for x in sug.get("items") or []}
    assist_only = (rel & sug_ids) - fts
    return {
        "ok": True,
        "authoritative": False,
        "fts_recall": fts_hit / max(1, len(rel)),
        "assist_novel_hits": sorted(assist_only),
        "assist_novel_count": len(assist_only),
        "semantic_hit_ids": sorted(rel & sug_ids),
        "note": "eval_only_no_persist",
    }
