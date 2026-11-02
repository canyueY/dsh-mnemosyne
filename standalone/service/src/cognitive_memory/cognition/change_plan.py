# -*- coding: utf-8 -*-
"""ChangePlan：把变化检测候选提升为可审计、默认可 dry-run 的变更计划。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.cognition.change_detect import detect_changes_for_observation
from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.models import MemoryStatus, MemoryUpdateIn

_DETECT_TO_PLAN = {
    "new": "ADD",
    "continues": "UPDATE",
    "evolves": "UPDATE",
    "corrects": "SUPERSEDE",
    "uncertain": "NO_CHANGE",
    "ends": "UPDATE",
    "supersedes": "SUPERSEDE",
    "conflicts": "NO_CHANGE",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_plan_id() -> str:
    return f"cpl_{uuid.uuid4().hex[:16]}"


def _risk_and_approval(change_type: str, confidence: float) -> tuple[str, str]:
    if change_type in ("NO_CHANGE",) or confidence < 0.45:
        return "high", "human"
    if change_type in ("SUPERSEDE", "INVALIDATE", "MERGE") or confidence < 0.7:
        return "medium", "human"
    if change_type == "ADD" and confidence >= 0.7:
        return "low", "auto_ok"
    return "medium", "human"


def candidate_to_plan_payload(
    observation_id: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    raw = str(candidate.get("change_type") or "uncertain")
    plan_type = _DETECT_TO_PLAN.get(raw, "NO_CHANGE")
    if raw == "corrects" and float(candidate.get("confidence") or 0) >= 0.75:
        plan_type = "INVALIDATE"
    conf = float(candidate.get("confidence") or 0.0)
    risk, approval = _risk_and_approval(plan_type, conf)
    targets = []
    if candidate.get("memory_id"):
        targets.append(str(candidate["memory_id"]))
    if candidate.get("new_memory_id"):
        targets.append(str(candidate["new_memory_id"]))
    return {
        "change_id": _new_plan_id(),
        "observation_id": observation_id,
        "target_memory_ids": targets,
        "change_type": plan_type,
        "detect_change_type": raw,
        "evidence_refs": [observation_id],
        "reason": str(candidate.get("reason") or ""),
        "risk_level": risk,
        "confidence": conf,
        "approval_requirement": approval,
        "expected_changes": {
            "relation": candidate.get("suggested_relation"),
            "similarity": candidate.get("similarity"),
            "preview": candidate.get("memory_content_preview"),
        },
        "execution_status": "proposed",
        "verification_status": "pending",
        "rollback_reference": None,
        "source_candidate": candidate,
    }


def build_plans_for_observation(
    service: MemoryService,
    observation_id: str,
    *,
    dry_run: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    detected = detect_changes_for_observation(
        service,
        observation_id,
        dry_run=True,
        apply_l2=False,
    )
    if not detected.get("ok"):
        return {"ok": False, "detail": detected.get("detail"), "plans": []}

    plans = [
        candidate_to_plan_payload(observation_id, c)
        for c in (detected.get("candidates") or [])
    ]
    service.store_change_candidates(
        observation_id=observation_id,
        candidates=list(detected.get("candidates") or []),
        dry_run=dry_run,
        applied=[],
    )
    if persist:
        for p in plans:
            service.store_change_plan(p)

    return {
        "ok": True,
        "observation_id": observation_id,
        "dry_run": dry_run,
        "plans": plans,
        "count": len(plans),
    }


def apply_change_plan(
    service: MemoryService,
    plan_id: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """显式执行计划。高风险或未 confirm 一律拒绝。"""
    plan = service.get_change_plan(plan_id)
    if not plan:
        return {"ok": False, "detail": "plan_not_found"}
    if not confirm:
        return {"ok": False, "detail": "confirm_required", "plan": plan}
    if plan.get("execution_status") == "applied":
        return {"ok": True, "detail": "already_applied", "plan": plan}
    if plan.get("approval_requirement") == "human" and plan.get("risk_level") == "high":
        return {"ok": False, "detail": "high_risk_blocked", "plan": plan}

    change_type = str(plan.get("change_type") or "")
    targets = list(plan.get("target_memory_ids") or [])
    conf = float(plan.get("confidence") or 0)
    if conf < 0.7 and change_type in ("SUPERSEDE", "INVALIDATE", "UPDATE", "MERGE"):
        return {"ok": False, "detail": "confidence_too_low", "plan": plan}

    applied: list[dict[str, Any]] = []
    rollback_ref: list[dict[str, Any]] = []
    try:
        if change_type in ("SUPERSEDE", "INVALIDATE") and len(targets) >= 1:
            old_id = targets[0]
            before = service.get_memory(old_id)
            rollback_ref.append(
                {
                    "memory_id": old_id,
                    "version": before.get("version"),
                    "status": before.get("status"),
                    "content": before.get("content"),
                }
            )
            new_status = (
                MemoryStatus.INVALIDATED
                if change_type == "INVALIDATE"
                else MemoryStatus.SUPERSEDED
            )
            updated = service.update_memory(old_id, MemoryUpdateIn(status=new_status))
            applied.append({"memory_id": old_id, "status": updated.status.value})
            if len(targets) >= 2 and plan.get("expected_changes", {}).get("relation"):
                rel = plan["expected_changes"]["relation"]
                if rel in ("supersedes", "contradicts", "continues"):
                    service.link_memories(
                        from_id=targets[1],
                        to_id=old_id,
                        relation="supersedes" if rel == "continues" else rel,
                        note=f"change_plan:{plan_id}",
                    )
        elif change_type == "MERGE" and len(targets) >= 2:
            keep_id, drop_id = targets[1], targets[0]
            for mid in (keep_id, drop_id):
                before = service.get_memory(mid)
                rollback_ref.append(
                    {
                        "memory_id": mid,
                        "version": before.get("version"),
                        "status": before.get("status"),
                        "content": before.get("content"),
                    }
                )
            drop = service.get_memory(drop_id)
            keep = service.get_memory(keep_id)
            merged = str(keep.get("content") or "").strip()
            extra = str(drop.get("content") or "").strip()
            if extra and extra not in merged:
                merged = f"{merged}\n（合并自旧条）{extra[:400]}"
            service.update_memory(keep_id, MemoryUpdateIn(content=merged))
            updated = service.update_memory(
                drop_id, MemoryUpdateIn(status=MemoryStatus.SUPERSEDED)
            )
            service.link_memories(
                from_id=keep_id,
                to_id=drop_id,
                relation="supersedes",
                note=f"change_plan_merge:{plan_id}",
            )
            applied.append(
                {
                    "keep": keep_id,
                    "superseded": drop_id,
                    "status": updated.status.value,
                }
            )
        elif change_type in ("ADD", "UPDATE", "NO_CHANGE"):
            applied.append({"noop": True, "change_type": change_type})
        else:
            return {"ok": False, "detail": "unsupported_change_type", "plan": plan}

        plan["execution_status"] = "applied"
        plan["verification_status"] = "verified" if applied else "pending"
        plan["rollback_reference"] = json.dumps(rollback_ref, ensure_ascii=False)
        service.update_change_plan(
            plan_id,
            execution_status="applied",
            verification_status=plan["verification_status"],
            rollback_reference=plan["rollback_reference"],
            payload_patch={"applied": applied},
        )
        return {"ok": True, "plan_id": plan_id, "applied": applied, "plan": service.get_change_plan(plan_id)}
    except Exception as exc:  # noqa: BLE001
        service.update_change_plan(
            plan_id,
            execution_status="failed",
            verification_status="failed",
            payload_patch={"error": str(exc)},
        )
        return {"ok": False, "detail": str(exc), "plan_id": plan_id}


def rollback_change_plan(service: MemoryService, plan_id: str) -> dict[str, Any]:
    """按 rollback_reference 恢复 status / content。"""
    plan = service.get_change_plan(plan_id)
    if not plan:
        return {"ok": False, "detail": "plan_not_found"}
    if plan.get("execution_status") != "applied":
        return {"ok": False, "detail": "not_applied", "plan": plan}
    raw = plan.get("rollback_reference") or "[]"
    try:
        refs = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    except json.JSONDecodeError:
        refs = []
    if not refs:
        return {"ok": False, "detail": "no_rollback_reference", "plan": plan}
    restored: list[dict[str, Any]] = []
    try:
        for ref in refs:
            mid = str(ref.get("memory_id") or "")
            if not mid:
                continue
            status_raw = str(ref.get("status") or "active")
            try:
                st = MemoryStatus(status_raw)
            except ValueError:
                st = MemoryStatus.ACTIVE
            kwargs: dict[str, Any] = {"status": st}
            if ref.get("content") is not None:
                kwargs["content"] = str(ref.get("content"))
            mem = service.update_memory(mid, MemoryUpdateIn(**kwargs))
            restored.append({"memory_id": mid, "status": mem.status.value})
        service.update_change_plan(
            plan_id,
            execution_status="rolled_back",
            verification_status="rolled_back",
            payload_patch={"rollback_restored": restored},
        )
        return {
            "ok": True,
            "plan_id": plan_id,
            "restored": restored,
            "plan": service.get_change_plan(plan_id),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc), "plan_id": plan_id}
