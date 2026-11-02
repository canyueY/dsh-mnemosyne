# -*- coding: utf-8 -*-
"""Schema mutation governance — LearningProposal entry (P1.5).

confirm=True is never sufficient authorization by itself.
Persistent schema mutations require capability SCHEMA_MUTATION (or
SCHEMA_AUTO_EVOLUTION for low-risk auto paths).
"""

from __future__ import annotations

import json
from functools import wraps
from typing import Any

from cognitive_memory.governance.capability_gate import check_capability
from cognitive_memory.tracing.service import JudgmentTraceService

# Shared process-local audit sink for unit tests / node process
_TRACE = JudgmentTraceService()


def governance_traces() -> JudgmentTraceService:
    return _TRACE


def authorize_schema_mutation(
    db: Any,
    *,
    confirm: bool,
    preview: bool = False,
    operation: str = "",
    actor: str = "",
    risk_level: str = "MEDIUM",
) -> dict[str, Any]:
    """Unified authorization check for long-term schema mutations."""
    trace = JudgmentTraceService(db)
    if preview:
        return {
            "ok": True,
            "preview": True,
            "allowed": True,
            "detail": "preview_no_persist",
        }
    if not confirm:
        return {
            "ok": False,
            "allowed": False,
            "detail": "confirm_required",
            "note": "confirm_is_not_authorization",
        }
    # confirm alone is insufficient — need capability
    gate = check_capability(db, "SCHEMA_MUTATION")
    if gate.get("allowed"):
        trace.record(
            "schema_mutation_authorized",
            governance_critical=True,
            reason_codes=["schema_mutation_capability", operation],
            input_refs=[actor or "unknown_actor"],
        )
        return {
            "ok": True,
            "allowed": True,
            "detail": "ok",
            "capability": "SCHEMA_MUTATION",
            "confirm": True,
            "risk_level": risk_level,
        }
    # Low-risk auto evolution still needs SCHEMA_AUTO_EVOLUTION, never confirm alone
    if str(risk_level).upper() == "LOW":
        auto = check_capability(db, "SCHEMA_AUTO_EVOLUTION")
        if auto.get("allowed"):
            return {
                "ok": True,
                "allowed": True,
                "detail": "ok",
                "capability": "SCHEMA_AUTO_EVOLUTION",
                "confirm": True,
                "risk_level": risk_level,
            }
    trace.record(
        "schema_mutation_denied",
        governance_critical=True,
        reason_codes=["capability_locked", "confirm_insufficient", operation],
        input_refs=[actor or "unknown_actor"],
    )
    return {
        "ok": False,
        "allowed": False,
        "detail": "capability_locked",
        "capability": "SCHEMA_MUTATION",
        "confirm_provided": bool(confirm),
        "note": "confirm_true_is_not_authorization",
    }


def proposal_payload(
    *,
    operation: str,
    target_schema_ids: list[str],
    reason: str = "",
    risk_level: str = "MEDIUM",
    evidence_refs: list[str] | None = None,
    patch: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "targetSchemaIds": list(target_schema_ids),
        "evidenceRefs": list(evidence_refs or []),
        "expectedImpact": [f"schema_mutation:{operation}"],
        "risk_level": risk_level,
        "approval_required": True,
        "status": "PROPOSED",
        "reason": reason or operation,
        "payload": payload or {},
        "proposed_patch": patch or {},
        "governance": {
            "entry": "schema_governance.learning_proposal",
            "confirm_alone_insufficient": True,
        },
    }


def dump_audit_note(auth: dict[str, Any]) -> str:
    return json.dumps(auth, ensure_ascii=False)[:500]


def schema_transaction(fn):
    """Serialize proposal checks, schema writes, audit and idempotent receipt."""
    @wraps(fn)
    def wrapped(service, *args, **kwargs):
        with service.db.transaction() as conn:
            plan_id = (args[0] if fn.__name__ == "apply_cognitive_change_plan" and args
                       else kwargs.get("existing_plan_id"))
            if plan_id and kwargs.get("confirm") and not kwargs.get("preview"):
                cached = conn.execute(
                    "SELECT result_json FROM schema_proposal_commits WHERE plan_id=?", (plan_id,)
                ).fetchone()
                if cached:
                    return {**json.loads(cached[0]), "idempotent":True}
                plan = service.get_cognitive_change_plan(plan_id) or {}
                versions = plan.get("expected_versions") or {}
                if not versions and plan.get("targetSchemaIds"):
                    return {"ok":False, "detail":"proposal_version_required"}
                for sid, expected in versions.items():
                    current = service.get_cognitive_schema(sid)
                    if not current or current["version"] != expected:
                        return {"ok":False, "detail":"version_conflict"}
            result = fn(service, *args, **kwargs)
            if result.get("ok") and kwargs.get("confirm") and not kwargs.get("preview"):
                pid = plan_id or result.get("change_plan_id")
                if pid:
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_proposal_commits VALUES (?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                        (pid, json.dumps(result, ensure_ascii=False)),
                    )
                JudgmentTraceService(service.db).record(
                    "schema.commit", input_refs=[str(pid or fn.__name__)],
                    reason_codes=[fn.__name__], governance_critical=True,
                )
            return result
    return wrapped
