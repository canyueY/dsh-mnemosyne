# -*- coding: utf-8 -*-
"""图式网络（E2.4）：拆分 / 合并 / 影响追踪。"""

from __future__ import annotations

from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.governance.schema_governance import authorize_schema_mutation, schema_transaction


def _links(sch: dict[str, Any]) -> list[dict[str, Any]]:
    raw = sch.get("network_links") or sch.get("links") or []
    return list(raw) if isinstance(raw, list) else []


def _append_link(sch: dict[str, Any], link: dict[str, Any]) -> list[dict[str, Any]]:
    links = _links(sch)
    key = (link.get("relation"), link.get("target_id"))
    if not any((x.get("relation"), x.get("target_id")) == key for x in links):
        links.append(link)
    return links[-64:]


def impact_of_schema(service: MemoryService, schema_id: str) -> dict[str, Any]:
    """影响面：证据引用、网络邻居、变更计划。"""
    sch = service.get_cognitive_schema(schema_id)
    if not sch:
        return {"ok": False, "detail": "schema_not_found"}
    evid = sch.get("evidence") or {}
    supporting = list(evid.get("supporting") or [])
    counter = list(evid.get("counter") or [])
    links = _links(sch)
    neighbors: list[dict[str, Any]] = []
    for link in links:
        tid = str(link.get("target_id") or "")
        if not tid:
            continue
        other = service.get_cognitive_schema(tid)
        neighbors.append(
            {
                "relation": link.get("relation"),
                "target_id": tid,
                "target_name": (other or {}).get("name"),
                "target_state": (other or {}).get("state"),
            }
        )
    plans = []
    try:
        for p in service.list_cognitive_change_plans(limit=80):
            targets = p.get("targetSchemaIds") or p.get("target_schema_ids") or []
            if schema_id in [str(x) for x in targets]:
                plans.append({"id": p.get("id"), "operation": p.get("operation"), "status": p.get("status")})
    except Exception:
        plans = []
    tms_pack: dict[str, Any] | None = None
    try:
        from cognitive_memory.tms.schema_bridge import analyze_schema_impact_tms

        tms_pack = analyze_schema_impact_tms(service, schema_id)
    except Exception:
        tms_pack = None
    return {
        "ok": True,
        "schema_id": schema_id,
        "name": sch.get("name"),
        "state": sch.get("state"),
        "evidence_supporting": supporting,
        "evidence_counter": counter,
        "neighbors": neighbors,
        "related_plans": plans,
        "impact_score": len(supporting) + len(counter) + len(neighbors) + len(plans),
        "tms": tms_pack,
        "note": "neighbors+plans 计数 + TMS 重评预览；无自动覆盖",
    }


@schema_transaction
def split_schema(
    service: MemoryService,
    schema_id: str,
    *,
    child_specs: list[dict[str, Any]],
    note: str = "",
    confirm: bool = False,
    existing_plan_id: str | None = None,
    _governance_preauthorized: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """将一图式拆为多个子图式；默认只出变更计划。

    落盘需 confirm + SCHEMA_MUTATION（confirm  alone 不足）。
    """
    parent = service.get_cognitive_schema(schema_id)
    if not parent:
        return {"ok": False, "detail": "schema_not_found"}
    if not child_specs:
        return {"ok": False, "detail": "child_specs_required"}

    plan_id = existing_plan_id
    if not plan_id and not preview:
        plan_id = service.store_cognitive_change_plan(
            {
                "operation": "SPLIT",
                "targetSchemaIds": [schema_id],
                "expectedImpact": [
                    "父图式标记 CONTESTED/RETIRED",
                    f"创建 {len(child_specs)} 个子图式",
                    "证据按 child_specs.memory_ids 分配",
                ],
                "risk_level": "HIGH",
                "approval_required": True,
                "status": "PROPOSED",
                "reason": note or "schema_split",
                "payload": {"child_specs": child_specs, "parent_id": schema_id},
            }
        )
    if preview or not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "preview": True,
            "change_plan_id": plan_id,
            "impact": impact_of_schema(service, schema_id),
            "note": "confirm_and_SCHEMA_MUTATION_required_to_persist",
        }

    if confirm:
        auth = authorize_schema_mutation(
            service.db,
            confirm=True,
            preview=False,
            operation="SPLIT",
            risk_level="HIGH",
        )
        if not auth.get("allowed"):
            return {
                "ok": False,
                "detail": auth.get("detail") or "capability_locked",
                "change_plan_id": plan_id,
                "auth": auth,
                "note": "confirm_true_is_not_authorization",
            }

    children: list[dict[str, Any]] = []
    for spec in child_specs:
        name = str(spec.get("name") or f"{parent.get('name')}-split").strip()
        mem_ids = [str(x) for x in (spec.get("memory_ids") or []) if x]
        child_id = service.store_cognitive_schema(
            {
                "name": name,
                "description": str(spec.get("description") or parent.get("description") or ""),
                "type": parent.get("type") or "KNOWLEDGE",
                "state": "VALIDATING",
                "confidence": float(spec.get("confidence") or max(0.2, float(parent.get("confidence") or 0.4) - 0.05)),
                "evidence": {"supporting": mem_ids, "counter": []},
                "applicability": spec.get("applicability") or parent.get("applicability") or {},
                "network_links": [
                    {"relation": "SPLIT_FROM", "target_id": schema_id},
                ],
                "parent_schema_id": schema_id,
            }
        )
        children.append({"id": child_id, "name": name, "memory_ids": mem_ids})

    parent_links = _append_link(
        parent,
        {"relation": "SPLIT_INTO", "target_id": children[0]["id"] if children else ""},
    )
    for c in children[1:]:
        parent_links = _append_link(parent, {"relation": "SPLIT_INTO", "target_id": c["id"]})
    updated_parent = service.update_cognitive_schema(
        schema_id,
        patch={"network_links": parent_links, "split_children": [c["id"] for c in children]},
        state="CONTESTED",
        change_type="SPECIALIZE",
        reason=f"split:{plan_id}",
    )
    if plan_id:
        service.update_cognitive_change_plan(
            plan_id,
            status="APPLIED",
            payload_patch={"children": children, "confirm": True},
        )
    return {
        "ok": True,
        "dry_run": False,
        "change_plan_id": plan_id,
        "parent": updated_parent,
        "children": children,
        "impact": impact_of_schema(service, schema_id),
    }


@schema_transaction
def merge_schemas(
    service: MemoryService,
    schema_ids: list[str],
    *,
    name: str | None = None,
    note: str = "",
    confirm: bool = False,
    existing_plan_id: str | None = None,
    _governance_preauthorized: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """合并多个图式为一；默认计划。落盘需 confirm + SCHEMA_MUTATION。"""
    ids = [str(x) for x in schema_ids if x]
    if len(ids) < 2:
        return {"ok": False, "detail": "need_at_least_two_schemas"}
    schemas = []
    for sid in ids:
        sch = service.get_cognitive_schema(sid)
        if not sch:
            return {"ok": False, "detail": f"schema_not_found:{sid}"}
        schemas.append(sch)

    plan_id = existing_plan_id
    if not plan_id and not preview:
        plan_id = service.store_cognitive_change_plan(
            {
                "operation": "MERGE",
                "targetSchemaIds": ids,
                "expectedImpact": [
                    "创建合并图式",
                    "源图式 RETIRED",
                    "证据并集保留",
                ],
                "risk_level": "HIGH",
                "approval_required": True,
                "status": "PROPOSED",
                "reason": note or "schema_merge",
                "payload": {"source_ids": ids},
            }
        )
    if preview or not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "preview": True,
            "change_plan_id": plan_id,
            "impacts": [impact_of_schema(service, s) for s in ids],
            "note": "confirm_and_SCHEMA_MUTATION_required_to_persist",
        }

    if confirm:
        auth = authorize_schema_mutation(
            service.db,
            confirm=True,
            preview=False,
            operation="MERGE",
            risk_level="HIGH",
        )
        if not auth.get("allowed"):
            return {
                "ok": False,
                "detail": auth.get("detail") or "capability_locked",
                "change_plan_id": plan_id,
                "auth": auth,
                "note": "confirm_true_is_not_authorization",
            }

    supporting: list[str] = []
    counter: list[str] = []
    for sch in schemas:
        evid = sch.get("evidence") or {}
        for mid in evid.get("supporting") or []:
            if mid not in supporting:
                supporting.append(str(mid))
        for mid in evid.get("counter") or []:
            if mid not in counter:
                counter.append(str(mid))
    merged_name = (name or f"合并·{schemas[0].get('name')}").strip()
    merged_id = service.store_cognitive_schema(
        {
            "name": merged_name,
            "description": note or "merged schema",
            "type": schemas[0].get("type") or "KNOWLEDGE",
            "state": "VALIDATING",
            "confidence": min(
                0.8,
                sum(float(s.get("confidence") or 0.4) for s in schemas) / len(schemas),
            ),
            "evidence": {"supporting": supporting[:64], "counter": counter[:64]},
            "network_links": [
                {"relation": "MERGED_FROM", "target_id": sid} for sid in ids
            ],
            "merged_from": ids,
        }
    )
    retired = []
    for sid in ids:
        sch = service.get_cognitive_schema(sid) or {}
        links = _append_link(sch, {"relation": "MERGED_INTO", "target_id": merged_id})
        updated = service.update_cognitive_schema(
            sid,
            patch={"network_links": links},
            state="RETIRED",
            change_type="RETIRE",
            reason=f"merge:{plan_id}",
        )
        retired.append({"id": sid, "state": (updated or {}).get("state")})
    if plan_id:
        service.update_cognitive_change_plan(
            plan_id,
            status="APPLIED",
            payload_patch={"merged_id": merged_id, "retired": retired, "confirm": True},
        )
    return {
        "ok": True,
        "dry_run": False,
        "change_plan_id": plan_id,
        "merged_id": merged_id,
        "retired": retired,
        "impact": impact_of_schema(service, merged_id),
    }
