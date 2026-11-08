# -*- coding: utf-8 -*-
"""Executor / Evaluator / Publisher split. Node token is not an implicit publisher."""

from __future__ import annotations

from fastapi import HTTPException, Request

from cognitive_memory.governance.access import AccessContext

EXECUTOR = "executor"
EVALUATOR = "evaluator"
PUBLISHER = "publisher"
VALID = {EXECUTOR, EVALUATOR, PUBLISHER}

OVERLAY_ID_PREFIXES = (
    "exp_",
    "trc_",
    "out_",
    "evl_",
    "hyp_",
    "hrl_",
    "scd_",
    "cpl_",
    "xpr_",
    "fix_",
    "slf_",
    "snp_",
    "ext_",
    "rub_",
)


def is_metacog_overlay_id(value: str | None) -> bool:
    text = str(value or "").strip()
    return text.startswith(OVERLAY_ID_PREFIXES)


FORBIDDEN_TRACE_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_reasoning",
        "verbatim_thinking",
        "cot",
        "private_thoughts",
        "scratchpad",
    }
)


def meta_role(request: Request) -> str:
    return (request.headers.get("x-meta-role") or "").strip().lower()


def meta_actor(request: Request, access: AccessContext) -> str:
    return (request.headers.get("x-meta-actor") or access.actor_id or "").strip()


def require_meta_role(request: Request, access: AccessContext, *roles: str) -> tuple[str, str]:
    role = meta_role(request)
    actor = meta_actor(request, access)
    if role not in VALID:
        raise HTTPException(
            status_code=403,
            detail={"code": "meta_role_denied", "needed": list(roles), "got": role or "missing"},
        )
    if role not in roles:
        raise HTTPException(
            status_code=403,
            detail={"code": "meta_role_denied", "needed": list(roles), "got": role},
        )
    return role, actor


def reject_hidden_thinking(payload: dict) -> None:
    keys = {str(k).lower() for k in (payload or {}).keys()}
    hit = keys & FORBIDDEN_TRACE_KEYS
    if hit:
        raise HTTPException(
            status_code=400,
            detail={"code": "verbatim_thinking_forbidden", "keys": sorted(hit)},
        )
    nested = payload.get("decision_summary") if isinstance(payload, dict) else None
    if isinstance(nested, dict):
        nkeys = {str(k).lower() for k in nested.keys()}
        hit = nkeys & FORBIDDEN_TRACE_KEYS
        if hit:
            raise HTTPException(
                status_code=400,
                detail={"code": "verbatim_thinking_forbidden", "keys": sorted(hit)},
            )
