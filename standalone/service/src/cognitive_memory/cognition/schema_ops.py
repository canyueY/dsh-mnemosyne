# -*- coding: utf-8 -*-
"""图式同化 / 顺应 / CognitiveChangePlan 应用（默认不自动 ACTIVE）。

P1.5：长期 Schema 变更经 LearningProposal + SCHEMA_MUTATION。
confirm=True 本身不是授权。
"""

from __future__ import annotations

from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.governance.capability_gate import check_capability
from cognitive_memory.governance.schema_governance import (
    authorize_schema_mutation,
    proposal_payload,
    schema_transaction,
)


@schema_transaction
def assimilate_schema(
    service: MemoryService,
    schema_id: str,
    *,
    memory_ids: list[str] | None = None,
    note: str = "",
    confirm: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """将新经验关联到现有图式（补充 supporting 证据引用）。

    默认只写入 LearningProposal（dry_run）；落盘需 confirm + SCHEMA_MUTATION。
    """
    sch = service.get_cognitive_schema(schema_id)
    if not sch:
        return {"ok": False, "detail": "schema_not_found"}
    evid = dict(sch.get("evidence") or {})
    supporting = list(evid.get("supporting") or [])
    added: list[str] = []
    for mid in memory_ids or []:
        mid = str(mid).strip()
        if mid and mid not in supporting:
            supporting.append(mid)
            added.append(mid)
    evid_next = dict(evid)
    evid_next["supporting"] = supporting[:64]
    notes = list(sch.get("assimilation_notes") or [])
    if note:
        notes.append(note)
    patch = {
        "evidence": evid_next,
        "assimilation_notes": notes[-20:],
        "confidence": sch.get("confidence"),
        "confidence_kind": "uncalibrated",
    }
    proposal = proposal_payload(
        operation="ASSIMILATE",
        target_schema_ids=[schema_id],
        reason=note or "assimilate",
        risk_level="MEDIUM",
        evidence_refs=added,
        patch=patch,
        payload={"memory_ids": list(memory_ids or []), "note": note},
    )
    plan_id = None if preview else service.store_cognitive_change_plan(proposal)

    if preview or not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "preview": True,
            "change_plan_id": plan_id,
            "schema_id": schema_id,
            "added_evidence": added,
            "proposed_patch": patch,
            "note": "confirm_and_SCHEMA_MUTATION_required_to_persist",
        }

    auth = authorize_schema_mutation(
        service.db,
        confirm=True,
        preview=False,
        operation="ASSIMILATE",
        risk_level="MEDIUM",
    )
    if not auth.get("allowed"):
        return {
            "ok": False,
            "detail": auth.get("detail") or "capability_locked",
            "change_plan_id": plan_id,
            "added_evidence": added,
            "auth": auth,
            "note": "confirm_true_is_not_authorization",
        }

    updated = service.update_cognitive_schema(
        schema_id,
        patch=patch,
        change_type="REFINE",
        reason=f"assimilate:{plan_id}",
    )
    service.update_cognitive_change_plan(
        plan_id,
        status="APPLIED",
        payload_patch={"applied": True, "confirm": True, "auth": auth},
    )
    return {
        "ok": True,
        "dry_run": False,
        "schema": updated,
        "added_evidence": added,
        "change_plan_id": plan_id,
    }


@schema_transaction
def accommodate_schema(
    service: MemoryService,
    schema_id: str,
    *,
    memory_ids: list[str] | None = None,
    counter_note: str = "",
    propose_split: bool = True,
    confirm: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """反例驱动：默认只生成 LearningProposal；落盘需 confirm + SCHEMA_MUTATION。"""
    sch = service.get_cognitive_schema(schema_id)
    if not sch:
        return {"ok": False, "detail": "schema_not_found"}
    evid = dict(sch.get("evidence") or {})
    counter = list(evid.get("counter") or [])
    added: list[str] = []
    for mid in memory_ids or []:
        mid = str(mid).strip()
        if mid and mid not in counter:
            counter.append(mid)
            added.append(mid)
    evid_next = dict(evid)
    evid_next["counter"] = counter[:64]
    app = dict(sch.get("applicability") or {})
    exclusions = list(app.get("exclusions") or [])
    if counter_note and counter_note not in exclusions:
        exclusions.append(counter_note)
    app["exclusions"] = exclusions[-12:]
    new_state = sch.get("state") or "PROPOSED"
    if added and str(new_state).upper() == "ACTIVE":
        new_state = "CONTESTED"
    elif added and str(new_state).upper() == "PROPOSED":
        new_state = "VALIDATING"
    patch = {
        "evidence": evid_next,
        "applicability": app,
        "confidence": sch.get("confidence"),
        "confidence_kind": "uncalibrated",
    }
    proposal = proposal_payload(
        operation="ACCOMMODATE",
        target_schema_ids=[schema_id],
        reason=counter_note or "counterevidence",
        risk_level="MEDIUM",
        evidence_refs=added,
        patch=patch,
        payload={
            "proposed_patch": patch,
            "proposed_state": str(new_state),
            "propose_split": propose_split,
            "memory_ids": list(memory_ids or []),
        },
    )
    if propose_split and added:
        proposal["expectedImpact"] = list(proposal.get("expectedImpact") or []) + [
            "缩小原图式适用范围",
            "可能拆分情境子图式",
        ]
        proposal["operation"] = "SPECIALIZE"
    plan_id = None if preview else service.store_cognitive_change_plan(proposal)

    if preview or not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "preview": True,
            "change_plan_id": plan_id,
            "schema_id": schema_id,
            "added_counter": added,
            "proposed_patch": patch,
            "proposed_state": str(new_state),
            "note": "confirm_and_SCHEMA_MUTATION_required_to_persist",
        }

    auth = authorize_schema_mutation(
        service.db,
        confirm=True,
        preview=False,
        operation=str(proposal.get("operation") or "ACCOMMODATE"),
        risk_level="MEDIUM",
    )
    if not auth.get("allowed"):
        return {
            "ok": False,
            "detail": auth.get("detail") or "capability_locked",
            "change_plan_id": plan_id,
            "added_counter": added,
            "auth": auth,
            "note": "confirm_true_is_not_authorization",
        }

    updated = service.update_cognitive_schema(
        schema_id,
        patch=patch,
        state=str(new_state),
        change_type="SPECIALIZE",
        reason=f"accommodate:{plan_id}",
    )
    service.update_cognitive_change_plan(
        plan_id,
        status="APPLIED",
        payload_patch={"applied": True, "confirm": True, "auth": auth},
    )
    return {
        "ok": True,
        "dry_run": False,
        "schema": updated,
        "added_counter": added,
        "change_plan_id": plan_id,
    }


@schema_transaction
def apply_cognitive_change_plan(
    service: MemoryService,
    plan_id: str,
    *,
    confirm: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """应用认知变更计划。P1.5：confirm ≠ 授权；须 SCHEMA_MUTATION。"""
    plan = service.get_cognitive_change_plan(plan_id)
    if not plan:
        return {"ok": False, "detail": "plan_not_found"}
    if str(plan.get("status") or "").upper() == "APPLIED":
        return {"ok": False, "detail": "already_applied"}
    risk = str(plan.get("risk_level") or "MEDIUM").upper()
    if preview or not confirm:
        return {
            "ok": False,
            "detail": "confirm_required",
            "preview": True,
            "risk_level": risk,
            "note": "confirm_and_SCHEMA_MUTATION_required_to_persist",
        }

    auth = authorize_schema_mutation(
        service.db,
        confirm=True,
        preview=False,
        operation=str(plan.get("operation") or "APPLY"),
        risk_level=risk,
    )
    if not auth.get("allowed"):
        return {
            "ok": False,
            "detail": auth.get("detail") or "capability_locked",
            "auth": auth,
            "note": "confirm_true_is_not_authorization",
        }

    op = str(plan.get("operation") or "UPDATE").upper()
    targets = list(plan.get("targetSchemaIds") or plan.get("target_schema_ids") or [])

    if op in ("SPLIT", "MERGE"):
        from cognitive_memory.cognition.schema_network import merge_schemas, split_schema

        payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
        if not payload:
            payload = {
                k: plan.get(k)
                for k in ("child_specs", "parent_id", "source_ids")
                if plan.get(k) is not None
            }
        if op == "SPLIT":
            parent_id = str(
                (payload.get("parent_id") or (targets[0] if targets else "")) or ""
            )
            return split_schema(
                service,
                parent_id,
                child_specs=list(payload.get("child_specs") or []),
                note=str(plan.get("reason") or ""),
                confirm=True,
                existing_plan_id=plan_id,
                _governance_preauthorized=True,
            )
        return merge_schemas(
            service,
            list(payload.get("source_ids") or targets),
            note=str(plan.get("reason") or ""),
            confirm=True,
            existing_plan_id=plan_id,
            _governance_preauthorized=True,
        )

    if (
        op in ("CREATE", "UPDATE")
        and risk == "LOW"
        and auth.get("capability") != "SCHEMA_MUTATION"
        and auth.get("capability") != "SCHEMA_AUTO_EVOLUTION"
    ):
        gate = check_capability(service.db, "SCHEMA_AUTO_EVOLUTION")
        if not gate.get("allowed"):
            return {
                "ok": False,
                "detail": "capability_locked",
                "capability": "SCHEMA_AUTO_EVOLUTION",
                "gate": gate,
            }

    applied: list[dict[str, Any]] = []
    for sid in targets:
        sch = service.get_cognitive_schema(str(sid))
        if not sch:
            continue
        if op == "RETIRE":
            updated = service.update_cognitive_schema(
                sid, state="RETIRED", change_type="RETIRE", reason=f"plan:{plan_id}"
            )
        elif op in (
            "CREATE",
            "UPDATE",
            "SPECIALIZE",
            "GENERALIZE",
            "REFINE",
            "ASSIMILATE",
            "ACCOMMODATE",
        ):
            next_state = "ACTIVE" if risk in ("LOW", "MEDIUM") else "VALIDATING"
            if risk in ("HIGH", "CRITICAL"):
                next_state = "VALIDATING"
            payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
            if isinstance(payload.get("proposed_state"), str) and payload.get("proposed_state"):
                next_state = str(payload["proposed_state"])
            patch: dict[str, Any] = {}
            if isinstance(plan.get("proposed_patch"), dict):
                patch = dict(plan.get("proposed_patch") or {})
            elif isinstance(payload.get("proposed_patch"), dict):
                patch = dict(payload.get("proposed_patch") or {})
            updated = service.update_cognitive_schema(
                sid,
                patch=patch or None,
                state=next_state,
                change_type=op
                if op not in ("CREATE", "ASSIMILATE", "ACCOMMODATE")
                else "REFINE",
                reason=f"plan:{plan_id}",
            )
        else:
            updated = service.update_cognitive_schema(
                sid,
                state="VALIDATING",
                change_type=op,
                reason=f"plan:{plan_id}",
            )
        if updated:
            applied.append({"schema_id": sid, "state": updated.get("state")})

    service.update_cognitive_change_plan(
        plan_id,
        status="APPLIED",
        payload_patch={"applied_schemas": applied, "confirm": confirm, "auth": auth},
    )
    return {"ok": True, "plan_id": plan_id, "applied": applied, "operation": op}
