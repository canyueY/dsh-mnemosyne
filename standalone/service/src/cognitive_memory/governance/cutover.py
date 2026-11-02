# -*- coding: utf-8 -*-
"""Adoption Cutover：预览 → 确认 → Authority 切换（禁止默认 dual）。

典型路径：external_authoritative → importing → mnemosyne_primary
已是 mnemosyne_primary 时 preview 标 ready；切 dual 必须 allow_dual。
"""

from __future__ import annotations

from typing import Any

from cognitive_memory.governance.authority import (
    AuthorityState,
    AuthorityTransitionError,
    get_authority,
    transition_authority,
)


def preview_cutover(service: Any) -> dict[str, Any]:
    auth = get_authority(service.db)
    health = service.health() if hasattr(service, "health") else {}
    state = str(auth.get("state") or "")
    checklist = [
        {
            "id": "backup",
            "required": True,
            "hint": "切前备份：mnemosyne backup 或 scripts/mnemosyne_backup_restore_drill.py",
        },
        {
            "id": "staging_approved",
            "required": state in (AuthorityState.EXTERNAL_AUTHORITATIVE.value, AuthorityState.IMPORTING.value),
            "hint": "导入批次须 Staging approve 后再 Commit",
        },
        {
            "id": "no_dual_default",
            "required": True,
            "hint": "dual_sync 默认禁止；须 allow_dual=true",
        },
        {
            "id": "confirm_execute",
            "required": True,
            "hint": "execute 须 confirm=true",
        },
    ]
    suggested: list[str] = []
    if state == AuthorityState.EXTERNAL_AUTHORITATIVE.value:
        suggested = [
            AuthorityState.IMPORTING.value,
            AuthorityState.MNEMOSYNE_PRIMARY.value,
        ]
    elif state == AuthorityState.IMPORTING.value:
        suggested = [AuthorityState.MNEMOSYNE_PRIMARY.value]
    elif state == AuthorityState.MNEMOSYNE_PRIMARY.value:
        suggested = []
    elif state == AuthorityState.DUAL_SYNC.value:
        suggested = [AuthorityState.MNEMOSYNE_PRIMARY.value]
    elif state == AuthorityState.RETIRED.value:
        suggested = [AuthorityState.MNEMOSYNE_PRIMARY.value]

    ready = state in (
        AuthorityState.MNEMOSYNE_PRIMARY.value,
        AuthorityState.IMPORTING.value,
        AuthorityState.EXTERNAL_AUTHORITATIVE.value,
    )
    return {
        "ok": True,
        "ready": ready,
        "authority": auth,
        "health": {
            k: health.get(k)
            for k in ("ok", "memories", "observations", "principals")
            if isinstance(health, dict)
        },
        "suggested_path": suggested,
        "checklist": checklist,
        "note": "preview ≠ cutover；execute 需 confirm",
    }


def execute_cutover(
    service: Any,
    *,
    target: str | None = None,
    confirm: bool = False,
    allow_dual: bool = False,
    reason: str = "",
    actor: str = "cutover",
) -> dict[str, Any]:
    if not confirm:
        return {
            "ok": False,
            "error": "confirm_required",
            "detail": "execute_cutover requires confirm=true",
            "preview": preview_cutover(service),
        }
    auth = get_authority(service.db)
    cur = AuthorityState(str(auth["state"]))
    steps: list[dict[str, Any]] = []

    # 默认目标：走向 mnemosyne_primary
    dest_name = (target or AuthorityState.MNEMOSYNE_PRIMARY.value).strip().lower()
    try:
        dest = AuthorityState(dest_name)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_target", "detail": str(exc)}

    if dest == AuthorityState.DUAL_SYNC and not allow_dual:
        return {
            "ok": False,
            "error": "dual_requires_explicit",
            "detail": "dual_sync requires allow_dual=true",
        }

    path: list[AuthorityState]
    if cur == dest:
        return {
            "ok": True,
            "noop": True,
            "authority": auth,
            "steps": [],
            "note": "already_at_target",
        }

    # 合成路径（最短合法边）
    if cur == AuthorityState.EXTERNAL_AUTHORITATIVE and dest == AuthorityState.MNEMOSYNE_PRIMARY:
        path = [AuthorityState.IMPORTING, AuthorityState.MNEMOSYNE_PRIMARY]
    elif cur == AuthorityState.EXTERNAL_AUTHORITATIVE and dest == AuthorityState.IMPORTING:
        path = [AuthorityState.IMPORTING]
    elif cur == AuthorityState.IMPORTING and dest == AuthorityState.MNEMOSYNE_PRIMARY:
        path = [AuthorityState.MNEMOSYNE_PRIMARY]
    elif cur == AuthorityState.MNEMOSYNE_PRIMARY and dest == AuthorityState.DUAL_SYNC:
        path = [AuthorityState.DUAL_SYNC]
    elif cur == AuthorityState.DUAL_SYNC and dest == AuthorityState.MNEMOSYNE_PRIMARY:
        path = [AuthorityState.MNEMOSYNE_PRIMARY]
    elif cur == AuthorityState.RETIRED and dest == AuthorityState.MNEMOSYNE_PRIMARY:
        path = [AuthorityState.MNEMOSYNE_PRIMARY]
    else:
        # 单步尝试
        path = [dest]

    try:
        current = auth
        for step in path:
            current = transition_authority(
                service.db,
                step,
                reason=reason or f"cutover→{dest.value}",
                allow_dual=allow_dual,
                actor=actor,
            )
            steps.append({"to": step.value, "authority": dict(current)})
        return {
            "ok": True,
            "authority": current,
            "steps": steps,
            "confirm": True,
        }
    except AuthorityTransitionError as exc:
        return {
            "ok": False,
            "error": exc.code,
            "detail": str(exc),
            "steps": steps,
            "authority": get_authority(service.db),
        }
