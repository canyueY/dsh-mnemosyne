# -*- coding: utf-8 -*-
"""图式演化：同化 / 顺应 / 冲突 / 平衡。

对应笔记中的 Piaget 图式理论，但不引入新的数据库表：
- 复用 `cognitive_schemas` + `schema_versions` + `network_links`；
- 只在 schema payload 内记录 `evolution_history`；
- 不静默覆盖冲突：冲突必须进入 CONTESTED，由后续计划/人工处理。
"""
from __future__ import annotations

from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.governance.schema_governance import schema_transaction, authorize_schema_mutation

ASSIMILATION = "assimilation"
ACCOMMODATION = "accommodation"
CONFLICT = "conflict"


def classify_evolution(
    *,
    supporting_count: int,
    counter_count: int,
    applicability_changed: bool = False,
) -> str:
    """按证据结构判断新经验与旧图式的关系。

    - 没有反证：同化；
    - 有反证但仍是少数：顺应（修正图式，不丢弃）；
    - 反证达到/超过支持证据：冲突（进入 CONTESTED）。
    """
    s = max(0, int(supporting_count))
    c = max(0, int(counter_count))
    if c <= 0:
        return ASSIMILATION
    if c >= s:
        return CONFLICT
    # applicability 改变意味着图式边界改变，属于顺应
    return ACCOMMODATION if (applicability_changed or c > 0) else ASSIMILATION


@schema_transaction
def evolve_schema(
    service: MemoryService,
    schema_id: str,
    *,
    supporting: list[str] | None = None,
    counter: list[str] | None = None,
    applicability: dict[str, Any] | None = None,
    confidence_delta: float = 0.0,
    reason: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """把一条新经验并入图式；默认 dry-run，confirm 后写版本。"""
    schema = service.get_cognitive_schema(schema_id)
    if not schema:
        return {"ok": False, "detail": "schema_not_found"}

    evidence = dict(schema.get("evidence") or {})
    old_support = [str(x) for x in (evidence.get("supporting") or [])]
    old_counter = [str(x) for x in (evidence.get("counter") or [])]
    new_support = [str(x) for x in (supporting or []) if x]
    new_counter = [str(x) for x in (counter or []) if x]
    merged_support = list(dict.fromkeys(old_support + new_support))[:64]
    merged_counter = list(dict.fromkeys(old_counter + new_counter))[:64]

    mode = classify_evolution(
        supporting_count=len(merged_support),
        counter_count=len(merged_counter),
        applicability_changed=applicability is not None,
    )
    old_conf = schema.get("confidence")
    # Association/repetition is not a calibrated epistemic update.
    delta = 0.0
    new_conf = old_conf

    history = list(schema.get("evolution_history") or [])[-19:]
    history.append(
        {
            "mode": mode,
            "from_confidence": old_conf,
            "to_confidence": new_conf,
            "supporting_added": new_support,
            "counter_added": new_counter,
            "reason": reason,
        }
    )
    next_state = "CONTESTED" if merged_counter else str(schema.get("state") or "PROPOSED")
    preview = {
        "ok": True,
        "dry_run": True,
        "schema_id": schema_id,
        "mode": mode,
        "state": next_state,
        "confidence": new_conf,
        "confidence_delta_applied": 0.0,
        "evidence": {"supporting": merged_support, "counter": merged_counter},
    }
    if not confirm:
        return preview

    gate = authorize_schema_mutation(service.db, confirm=confirm, operation="schema_evolution")
    if not gate["ok"]:
        return gate

    links = list(schema.get("network_links") or schema.get("links") or [])
    relation = {
        ASSIMILATION: "ASSIMILATED",
        ACCOMMODATION: "ACCOMMODATED",
        CONFLICT: "CONFLICTED",
    }[mode]
    links.append(
        {
            "relation": relation,
            "target_id": (new_support + new_counter)[0] if (new_support + new_counter) else "",
            "note": reason,
        }
    )
    patch: dict[str, Any] = {
        "evidence": {"supporting": merged_support, "counter": merged_counter},
        "confidence": new_conf,
        "evolution_history": history,
        "network_links": links[-64:],
        "last_evolution": mode,
    }
    if applicability is not None:
        patch["applicability"] = dict(applicability)
    updated = service.update_cognitive_schema(
        schema_id,
        patch=patch,
        state=next_state,
        change_type=mode.upper(),
        reason=reason or f"schema_evolution:{mode}",
    )
    return {
        "ok": bool(updated),
        "dry_run": False,
        "schema_id": schema_id,
        "mode": mode,
        "schema": updated,
    }
