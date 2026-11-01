# -*- coding: utf-8 -*-
"""Context envelope budget（E04）。

分区：stable_prefix / tools / history / header_tags / memory / output_reserve。
无精确 tokenizer 时 measurement=estimated|unknown，不宣称真实精确 token 上限。
先预留固定开销与输出，再裁剪记忆；固定开销已超限 → fits=false。
"""

from __future__ import annotations

from typing import Any, Callable


EstimateFn = Callable[[str], int]


def estimate_tokens_chars(text: str) -> int:
    """保守字符启发式：约 2 字符 ≈ 1 token；空串计 0。"""
    n = len(text or "")
    if n <= 0:
        return 0
    return max(1, (n + 1) // 2)


DEFAULT_SECTION_ORDER = (
    "stable_prefix",
    "tools",
    "history",
    "header_tags",
    "memory",
    "output_reserve",
)


def normalize_budget_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(raw or {})
    window = int(cfg.get("model_window") or cfg.get("window") or 8192)
    conservative = int(cfg.get("conservative_reserve") or max(64, window // 32))
    max_candidates = int(cfg.get("max_candidates") or 32)
    timeout_ms = int(cfg.get("timeout_ms") or 5000)
    return {
        "model_window": max(256, window),
        "conservative_reserve": max(0, conservative),
        "max_candidates": max(1, max_candidates),
        "timeout_ms": max(1, timeout_ms),
        "measurement_default": str(cfg.get("measurement_default") or "estimated"),
    }


def build_context_envelope(
    *,
    token_budget: int,
    sections: dict[str, Any],
    memory_candidates: list[dict[str, Any]] | None = None,
    estimate_fn: EstimateFn | None = None,
    measurement: str | None = None,
    has_accurate_tokenizer: bool = False,
    config: dict[str, Any] | None = None,
    preserve_tags: bool = True,
) -> dict[str, Any]:
    """构建预算信封。

    sections 值可为 str 或 {"text":..., "tokens": int}。
    memory_candidates: [{id, content, tags?, ...}]，按序尝试装入。
    """
    est = estimate_fn or estimate_tokens_chars
    cfg = normalize_budget_config(config)
    budget = int(token_budget or 0)
    if budget <= 0:
        budget = int(cfg["model_window"])

    meas = (measurement or cfg["measurement_default"] or "estimated").strip().lower()
    if has_accurate_tokenizer:
        meas = "measured"
    elif meas not in {"measured", "estimated", "unknown"}:
        meas = "estimated"
    if not has_accurate_tokenizer and meas == "measured":
        # 无 tokenizer 不得宣称 measured
        meas = "estimated"

    def _section_tokens(value: Any) -> tuple[int, str]:
        if value is None:
            return 0, ""
        if isinstance(value, dict):
            text = str(value.get("text") or "")
            if "tokens" in value and value["tokens"] is not None:
                try:
                    return max(0, int(value["tokens"])), text
                except (TypeError, ValueError):
                    pass
            return est(text), text
        text = str(value)
        return est(text), text

    fixed_keys = ("stable_prefix", "tools", "history", "header_tags", "output_reserve")
    breakdown: dict[str, dict[str, Any]] = {}
    fixed_total = 0
    # 先算非预留固定开销，再决定保守预留，避免小 budget 被默认 reserve 单独撑爆
    pre_reserve_fixed = 0
    section_toks: dict[str, tuple[int, str]] = {}
    for key in fixed_keys:
        tok, text = _section_tokens(sections.get(key))
        section_toks[key] = (tok, text)
        if key != "output_reserve":
            pre_reserve_fixed += tok
    reserve = int(cfg["conservative_reserve"])
    if budget > 0 and budget < int(cfg["model_window"]):
        # 小上下文请求：预留最多占剩余预算的一半，仍保留输出安全带
        room = max(0, budget - pre_reserve_fixed)
        reserve = min(reserve, max(0, room // 2))
    for key in fixed_keys:
        tok, text = section_toks[key]
        if key == "output_reserve":
            tok = tok + reserve
        breakdown[key] = {
            "tokens": tok,
            "chars": len(text),
            "kind": "fixed",
        }
        fixed_total += tok

    reason_codes: list[str] = []
    fits: bool | None = True
    selected_memory: list[dict[str, Any]] = []
    memory_tokens = 0
    trimmed = 0

    if fixed_total > budget:
        fits = False
        reason_codes.append("fixed_overhead_exceeds_budget")
        reason_codes.append("context_incompatible")
        available_for_memory = 0
    else:
        available_for_memory = budget - fixed_total
        for cand in list(memory_candidates or [])[: int(cfg["max_candidates"])]:
            if not isinstance(cand, dict):
                continue
            content = str(cand.get("content") or "")
            tags = cand.get("tags") if isinstance(cand.get("tags"), list) else []
            # 语义边界标签必须保留在片段元数据中，不可为省 token 裁掉
            tag_text = ""
            if preserve_tags and tags:
                tag_text = " ".join(f"#{t}" for t in tags if t)
            payload = content if not tag_text else f"{tag_text}\n{content}"
            need = est(payload)
            if memory_tokens + need > available_for_memory:
                trimmed += 1
                continue
            item = dict(cand)
            item["token_estimate"] = need
            if preserve_tags:
                item["tags"] = list(tags)
                # 否定/时效/来源标签显式回传
                for k in ("negated", "expires_at", "source_ids", "epistemic_status", "validity"):
                    if k in cand:
                        item[k] = cand[k]
            selected_memory.append(item)
            memory_tokens += need
        if trimmed and not selected_memory and (memory_candidates or []):
            reason_codes.append("memory_trimmed_none_fit")
        elif trimmed:
            reason_codes.append("memory_trimmed")

    breakdown["memory"] = {
        "tokens": memory_tokens,
        "candidates_considered": len(memory_candidates or []),
        "selected": len(selected_memory),
        "trimmed": trimmed,
        "kind": "variable",
    }

    used = fixed_total + memory_tokens
    remaining = max(0, budget - used) if fits else 0

    # 诚实声明：无准确 tokenizer 时不得宣称精确硬上限已验证
    hard_limit_verified = bool(has_accurate_tokenizer and meas == "measured")
    if not hard_limit_verified:
        reason_codes.append("token_limit_estimated_only")

    status = "ok" if fits else "incompatible"
    if fits and trimmed and not selected_memory:
        status = "partial"

    # 契约别名：tools_descriptions ↔ tools；memory_tags ↔ header_tags
    if "tools" in breakdown and "tools_descriptions" not in breakdown:
        breakdown["tools_descriptions"] = dict(breakdown["tools"])
    if "header_tags" in breakdown and "memory_tags" not in breakdown:
        breakdown["memory_tags"] = dict(breakdown["header_tags"])

    return {
        "ok": True,
        "status": status,
        "fits": fits,
        "measurement": meas,
        "hard_limit_verified": hard_limit_verified,
        "token_budget": budget,
        "tokens_used": used,
        "tokens_remaining": remaining,
        "fixed_overhead": fixed_total,
        "memory_tokens": memory_tokens,
        "available_for_memory": max(0, budget - fixed_total) if fixed_total <= budget else 0,
        "breakdown": breakdown,
        "memory_fragments": selected_memory,
        "config": {
            "model_window": cfg["model_window"],
            "conservative_reserve": cfg["conservative_reserve"],
            "max_candidates": cfg["max_candidates"],
            "timeout_ms": cfg["timeout_ms"],
        },
        "reason_codes": reason_codes,
        "note": "measurement is estimated/unknown without an accurate tokenizer; not a verified hard token ceiling",
    }
