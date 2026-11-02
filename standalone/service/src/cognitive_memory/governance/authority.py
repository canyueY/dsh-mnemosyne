# -*- coding: utf-8 -*-
"""Adoption Authority 状态机（五态）；禁止双权威默认。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AuthorityState(str, Enum):
    EXTERNAL_AUTHORITATIVE = "external_authoritative"
    IMPORTING = "importing"
    MNEMOSYNE_PRIMARY = "mnemosyne_primary"
    DUAL_SYNC = "dual_sync"
    RETIRED = "retired"


# 允许的边；dual_sync 必须显式 allow_dual
_TRANSITIONS: dict[AuthorityState, frozenset[AuthorityState]] = {
    AuthorityState.EXTERNAL_AUTHORITATIVE: frozenset(
        {
            AuthorityState.IMPORTING,
            AuthorityState.MNEMOSYNE_PRIMARY,
            AuthorityState.RETIRED,
        }
    ),
    AuthorityState.IMPORTING: frozenset(
        {
            AuthorityState.EXTERNAL_AUTHORITATIVE,
            AuthorityState.MNEMOSYNE_PRIMARY,
            AuthorityState.RETIRED,
        }
    ),
    AuthorityState.MNEMOSYNE_PRIMARY: frozenset(
        {
            AuthorityState.DUAL_SYNC,
            AuthorityState.RETIRED,
            AuthorityState.IMPORTING,
        }
    ),
    AuthorityState.DUAL_SYNC: frozenset(
        {
            AuthorityState.MNEMOSYNE_PRIMARY,
            AuthorityState.RETIRED,
        }
    ),
    AuthorityState.RETIRED: frozenset({AuthorityState.MNEMOSYNE_PRIMARY}),
}

DEFAULT_STATE = AuthorityState.MNEMOSYNE_PRIMARY


class AuthorityTransitionError(ValueError):
    def __init__(self, message: str, *, code: str = "authority_transition_denied"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_authority_row(db: Any, *, default: AuthorityState = DEFAULT_STATE) -> dict[str, Any]:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM authority_state WHERE id='default'"
        ).fetchone()
        if row:
            return _row(row)
        now = _now()
        conn.execute(
            """
            INSERT INTO authority_state(id, state, previous_state, reason, updated_at, metadata_json)
            VALUES ('default', ?, NULL, ?, ?, '{}')
            """,
            (default.value, "bootstrap", now),
        )
        row = conn.execute(
            "SELECT * FROM authority_state WHERE id='default'"
        ).fetchone()
    return _row(row)


def get_authority(db: Any) -> dict[str, Any]:
    return ensure_authority_row(db)


def transition_authority(
    db: Any,
    target: str | AuthorityState,
    *,
    reason: str = "",
    allow_dual: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    dest = (
        target
        if isinstance(target, AuthorityState)
        else AuthorityState(str(target).strip().lower())
    )
    current = get_authority(db)
    cur = AuthorityState(current["state"])
    if cur == dest:
        return current
    allowed = _TRANSITIONS.get(cur, frozenset())
    if dest not in allowed:
        raise AuthorityTransitionError(
            f"cannot transition {cur.value} → {dest.value}",
            code="invalid_transition",
        )
    if dest == AuthorityState.DUAL_SYNC and not allow_dual:
        raise AuthorityTransitionError(
            "dual_sync requires allow_dual=true (dual authority is not default)",
            code="dual_requires_explicit",
        )
    now = _now()
    meta = dict(current.get("metadata") or {})
    meta["last_actor"] = actor
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE authority_state
            SET state=?, previous_state=?, reason=?, updated_at=?, metadata_json=?
            WHERE id='default'
            """,
            (
                dest.value,
                cur.value,
                reason or "",
                now,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        row = conn.execute(
            "SELECT * FROM authority_state WHERE id='default'"
        ).fetchone()
    return _row(row)


def _row(row: Any) -> dict[str, Any]:
    meta = {}
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "state": row["state"],
        "previous_state": row["previous_state"],
        "reason": row["reason"] or "",
        "updated_at": row["updated_at"],
        "metadata": meta,
        "dual_default_forbidden": True,
    }
