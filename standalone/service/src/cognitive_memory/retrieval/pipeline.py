# -*- coding: utf-8 -*-
"""分层检索管线：plan → recall → filter → score → optional deepen。"""

from __future__ import annotations

import time
import traceback
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.models import (
    ContextBuildIn,
    Memory,
    MemoryStatus,
    SearchQuery,
)
from cognitive_memory.retrieval.planner import QueryPlan, plan_retrieval
from cognitive_memory.retrieval.scoring import score_memory

RESULT_FOUND = "FOUND"
RESULT_PARTIAL = "PARTIAL"
RESULT_NO_MATCH = "NO_MATCH"
RESULT_FAILED = "FAILED"
RESULT_NOT_NEEDED = "NOT_NEEDED"


def _brief_from_fragments(fragments: list[dict[str, Any]]) -> str:
    """Render the same brief format as MemoryService.build_context."""
    if not fragments:
        return "【认知记忆】暂无相关条目"
    lines = ["【认知记忆·仅供参考】"]
    for frag in fragments:
        tag = f"{frag.get('type')}|{frag.get('epistemic_status')}"
        if frag.get("via_relation"):
            tag = f"{tag}|rel"
        if "negated" in (frag.get("tags") or []):
            tag = f"{tag}|neg"
        if frag.get("expires_at"):
            tag = f"{tag}|exp"
        lines.append(f"- [{tag}] {frag.get('content')}")
    return "\n".join(lines)


def _filter_candidates(memories: list[Memory], plan: QueryPlan) -> list[Memory]:
    out: list[Memory] = []
    for m in memories:
        if m.status == MemoryStatus.INVALIDATED:
            continue
        if plan.mode == "current" and m.status == MemoryStatus.SUPERSEDED:
            continue
        out.append(m)
    return out


def _rank(
    memories: list[Memory],
    query: str,
    *,
    mode: str,
) -> list[tuple[Memory, dict[str, Any]]]:
    scored: list[tuple[Memory, dict[str, Any]]] = []
    for m in memories:
        sc = score_memory(m, query, mode=mode)
        scored.append((m, sc))
    scored.sort(key=lambda x: x[1]["total"], reverse=True)
    return scored


def run_retrieve(
    service: MemoryService,
    *,
    query: str,
    force: bool = False,
    limit: int = 8,
    mode: str = "current",
    spatiotemporal_rerank: bool = True,
    emotion_rerank: bool = False,
    semantic_assist: bool = True,
    semantic_weight: float = 0.25,
    ab_experiment: bool = False,
    ab_subject_key: str = "",
    token_budget: int | None = None,
    types: list | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    plan = plan_retrieval(
        query,
        force=force,
        default_mode=mode,
        default_limit=limit,
    )
    trace: dict[str, Any] = {
        "plan": plan.to_dict(),
        "domain": domain,
        "channels": [],
        "deepened": False,
        "error": None,
        "ab": None,
    }
    assist_flag = bool(semantic_assist)
    if ab_experiment:
        from cognitive_memory.governance.ab_experiment import resolve_semantic_assist_arm

        ab = resolve_semantic_assist_arm(
            service.db,
            enabled=True,
            subject_key=ab_subject_key or "",
            query=query,
        )
        trace["ab"] = ab
        if ab.get("active") and ab.get("override") is not None:
            assist_flag = bool(ab["override"])

    if not plan.should_retrieve:
        return {
            "status": RESULT_NOT_NEEDED,
            "plan": plan.to_dict(),
            "items": [],
            "count": 0,
            "brief": "",
            "fragments": [],
            "token_estimate": 0,
            "persisted_ok": True,
            "retrieval_ready": False,
            "trace": {**trace, "elapsed_ms": int((time.perf_counter() - t0) * 1000)},
        }

    try:
        fetch_n = max(plan.limit, min(40, plan.limit * 2))
        hits = service.search(
            SearchQuery(
                query=query,
                types=types,
                limit=fetch_n,
                mode=plan.mode,
                spatiotemporal_rerank=spatiotemporal_rerank,
                emotion_rerank=emotion_rerank,
                domain=domain,
            )
        )
        trace["channels"].append({"name": "fts_like", "raw_hits": len(hits)})
        filtered = _filter_candidates(hits, plan)
        ranked = _rank(filtered, query, mode=plan.mode)

        # 按需加深：命中不足且 depth!=shallow
        if len(ranked) < plan.expected_min_hits and plan.depth in ("normal", "deep"):
            deep_hits = service.search(
                SearchQuery(
                    query=query,
                    types=types,
                    limit=min(50, fetch_n * 2),
                    mode="any",
                    spatiotemporal_rerank=spatiotemporal_rerank,
                    emotion_rerank=emotion_rerank,
                domain=domain,
                )
            )
            trace["deepened"] = True
            trace["channels"].append({"name": "deepen_any", "raw_hits": len(deep_hits)})
            seen = {m.id for m, _ in ranked}
            for m in _filter_candidates(deep_hits, plan):
                if m.id not in seen:
                    ranked.append((m, score_memory(m, query, mode="any")))
                    seen.add(m.id)
            ranked.sort(key=lambda x: x[1]["total"], reverse=True)

        ranked = ranked[: plan.limit]
        items = []
        for m, sc in ranked:
            items.append(
                {
                    "id": m.id,
                    "type": m.type.value,
                    "status": m.status.value,
                    "content": m.content,
                    "version": m.version,
                    "source_ids": m.source_ids,
                    "score": sc,
                    "metadata": m.metadata,
                    "authoritative": True,
                    "authority_scope": "local_record_only",
                    "truth_status": "unverified_record",
                    "score_kind": "retrieval_ranking_not_probability",
                }
            )

        if assist_flag and items:
            from cognitive_memory.retrieval.semantic_sidepath import assist_rerank

            assisted = assist_rerank(
                query, items, weight=float(semantic_weight)
            )
            items = assisted.get("items") or items
            trace["channels"].append(
                {
                    "name": "semantic_assist",
                    "assistive_only": True,
                    "authoritative": False,
                    "weight": float(semantic_weight),
                    "note": "never_verification",
                }
            )
        elif ab_experiment and items:
            trace["channels"].append(
                {
                    "name": "semantic_assist",
                    "skipped": True,
                    "reason": "ab_control_or_locked",
                    "assistive_only": True,
                }
            )

        # E09：词法/向量 RRF 融合 + 前后过滤复核（默认 hash 向量不冒充高质量 embedding）
        fusion_pack = None
        if items:
            from cognitive_memory.retrieval.fusion import fuse_channels

            fusion_candidates = []
            for it in items:
                fusion_candidates.append(
                    {
                        "id": it.get("id"),
                        "content": it.get("content"),
                        "bank_id": (it.get("metadata") or {}).get("bank_id")
                        or it.get("bank_id")
                        or "personal",
                        "metadata": it.get("metadata") or {},
                        "expires_at": (it.get("metadata") or {}).get("expires_at"),
                        "status": it.get("status"),
                    }
                )
            fusion_pack = fuse_channels(
                query=query,
                candidates=fusion_candidates,
                allowed_banks=None,  # search 已按 bank 执法；此处复核时间/模式
                mode=plan.mode,
                limit=plan.limit,
                use_vector=True,
                embedding_provider="hash",
            )
            fused_ids = {x["id"] for x in fusion_pack.get("items") or []}
            # 安全过滤为空时禁止 or items 回退过期/越权候选
            safe_items = [it for it in items if it.get("id") in fused_ids]
            fusion_status = str(fusion_pack.get("status") or "")
            if not safe_items and fusion_status in {"unknown", "partial", "blocked"}:
                items = []
            else:
                items = safe_items
            # 按融合分重排
            order = {x["id"]: i for i, x in enumerate(fusion_pack.get("items") or [])}
            items.sort(key=lambda it: order.get(it.get("id"), 10_000))
            for it in items:
                it["fusion"] = {
                    "embedding_kind": "hash",
                    "status": fusion_pack.get("status"),
                    "reason_codes": fusion_pack.get("reason_codes") or [],
                }
            trace["channels"].append(
                {
                    "name": "fusion_rrf",
                    "status": fusion_pack.get("status"),
                    "embedding_kind": "hash",
                    "do_not_overclaim": True,
                    "counterevidence_independent": True,
                }
            )

        # 复用 context 打包（与 brief 一致）
        ctx = service.build_context(
            ContextBuildIn(
                query=query,
                token_budget=token_budget,
                types=types,
                limit=plan.limit,
                mode=plan.mode,
                spatiotemporal_rerank=spatiotemporal_rerank,
                emotion_rerank=emotion_rerank,
                domain=domain,
            )
        )

        # build_context re-retrieves independently; never publish its brief or
        # fragments when they contain records filtered out of `items` (expired,
        # unauthorized, or unsafe). One safe snapshot must drive every field.
        if items:
            safe_ids = {str(it.get("id")) for it in items}
            ctx.fragments = [
                frag for frag in (ctx.fragments or [])
                if str(frag.get("id")) in safe_ids
            ]
            ctx.brief = _brief_from_fragments(list(ctx.fragments))
        else:
            ctx.fragments = []
            ctx.brief = ""

        if not items:
            status = RESULT_NO_MATCH
        elif len(items) < plan.expected_min_hits or (
            items and items[0]["score"]["total"] < 0.25
        ):
            status = RESULT_PARTIAL
        else:
            status = RESULT_FOUND

        out = {
            "status": status,
            "plan": plan.to_dict(),
            "items": items,
            "count": len(items),
            "brief": ctx.brief if items else "",
            "fragments": ctx.fragments if items else [],
            "token_estimate": ctx.token_estimate if items else 0,
            "persisted_ok": True,
            "retrieval_ready": True,
            "trace": {**trace, "elapsed_ms": int((time.perf_counter() - t0) * 1000)},
        }
        if fusion_pack is not None:
            out["fusion"] = {
                "status": fusion_pack.get("status"),
                "reason_codes": fusion_pack.get("reason_codes") or [],
                "embedding_meta": fusion_pack.get("embedding_meta"),
                "counterevidence": fusion_pack.get("counterevidence") or [],
                "notes": fusion_pack.get("notes") or {},
            }
            if fusion_pack.get("status") in {"partial", "unknown"} and status == RESULT_FOUND:
                out["status"] = RESULT_PARTIAL
        return out
    except Exception as exc:
        trace["error"] = f"{type(exc).__name__}: {exc}"
        trace["traceback"] = traceback.format_exc(limit=3)
        return {
            "status": RESULT_FAILED,
            "plan": plan.to_dict(),
            "items": [],
            "count": 0,
            "brief": "",
            "fragments": [],
            "token_estimate": 0,
            "persisted_ok": True,
            "retrieval_ready": False,
            "trace": {**trace, "elapsed_ms": int((time.perf_counter() - t0) * 1000)},
            "error": str(exc),
        }
