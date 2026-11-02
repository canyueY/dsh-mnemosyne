# -*- coding: utf-8 -*-
"""Capability Gate：条件解锁 · 硬锁执行入口 · 审计。默认全部 LOCKED。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

CAPABILITIES = (
    "SCHEMA_AUTO_EVOLUTION",
    # P1.5：长期 Schema 变更（assimilate/accommodate/split/merge/apply）
    "SCHEMA_MUTATION",
    "BANDIT_EXPERIMENT",
    "RL_OFFLINE_TRAINING",
    "RL_POLICY_DEPLOYMENT",
    "ADVANCED_VALUE_EVALUATION",
    "AUTONOMOUS_VALUE_ADAPTATION",
    # 运维执法：硬删默认 LOCKED，须外部治理解锁
    "MEMORY_HARD_DELETE",
    "MEMORY_BULK_IMPORT",
)

_LEVELS = frozenset({"LOCKED", "EXPERIMENTAL", "CONTROLLED", "ACTIVE", "SUSPENDED"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "cga") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ensure_default_capabilities(db: Database) -> None:
    now = _now()
    with db.transaction() as conn:
        for cap in CAPABILITIES:
            row = conn.execute(
                "SELECT capability FROM capability_permissions WHERE capability=?",
                (cap,),
            ).fetchone()
            if row:
                continue
            conn.execute(
                """
                INSERT INTO capability_permissions(
                  capability, enabled, level, requirements_json,
                  approved_at, expires_at, version, updated_at
                ) VALUES (?, 0, 'LOCKED', '[]', NULL, NULL, 1, ?)
                """,
                (cap, now),
            )


def list_capabilities(db: Database) -> list[dict[str, Any]]:
    ensure_default_capabilities(db)
    with db.exclusive() as conn:
        rows = conn.execute(
            "SELECT * FROM capability_permissions ORDER BY capability"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            reqs = json.loads(r["requirements_json"] or "[]")
        except json.JSONDecodeError:
            reqs = []
        out.append(
            {
                "capability": r["capability"],
                "enabled": bool(r["enabled"]),
                "level": r["level"],
                "requirements": reqs,
                "approved_at": r["approved_at"],
                "expires_at": r["expires_at"],
                "version": r["version"],
                "updated_at": r["updated_at"],
            }
        )
    return out


def check_capability(db: Database, capability: str) -> dict[str, Any]:
    ensure_default_capabilities(db)
    cap = str(capability or "").strip()
    with db.exclusive() as conn:
        row = conn.execute(
            "SELECT * FROM capability_permissions WHERE capability=?", (cap,)
        ).fetchone()
    if not row:
        return {
            "capability": cap,
            "allowed": False,
            "level": "LOCKED",
            "detail": "unknown_capability",
        }
    level = str(row["level"] or "LOCKED")
    enabled = bool(row["enabled"])
    # ACTIVE/CONTROLLED 且 enabled 才允许生产副作用；EXPERIMENTAL 只允许隔离实验标记
    allowed = enabled and level in ("CONTROLLED", "ACTIVE")
    if row["expires_at"]:
        try:
            allowed = allowed and datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
        except (ValueError, TypeError, AttributeError):
            allowed = False
    experimental = level == "EXPERIMENTAL"
    return {
        "capability": cap,
        "allowed": allowed,
        "experimental_ok": experimental or allowed,
        "level": level,
        "enabled": enabled,
        "detail": "ok" if allowed else f"level={level}",
    }


def set_capability_level(
    db: Database,
    capability: str,
    *,
    level: str,
    enabled: bool | None = None,
    actor: str = "external_governance",
) -> dict[str, Any]:
    """仅供外部治理调用；不暴露给 Agent 自改。"""
    ensure_default_capabilities(db)
    cap = str(capability or "").strip()
    if cap not in CAPABILITIES:
        return {"ok": False, "detail": "unknown_capability"}
    lv = str(level or "LOCKED").upper()
    if lv not in _LEVELS:
        return {"ok": False, "detail": "invalid_level"}
    now = _now()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT version, enabled FROM capability_permissions WHERE capability=?",
            (cap,),
        ).fetchone()
        ver = int(row["version"] or 1) + 1 if row else 1
        en = int(row["enabled"] or 0) if row and enabled is None else (1 if enabled else 0)
        if enabled is not None:
            en = 1 if enabled else 0
        # LOCKED/SUSPENDED 强制 enabled=0
        if lv in ("LOCKED", "SUSPENDED"):
            en = 0
        conn.execute(
            """
            UPDATE capability_permissions
            SET level=?, enabled=?, version=?, updated_at=?,
                approved_at=CASE WHEN ? IN ('CONTROLLED','ACTIVE') THEN ? ELSE approved_at END
            WHERE capability=?
            """,
            (lv, en, ver, now, lv, now, cap),
        )
        conn.execute(
            """
            INSERT INTO capability_audit(id, capability, action, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                cap,
                "set_level",
                json.dumps(
                    {"level": lv, "enabled": bool(en), "actor": actor},
                    ensure_ascii=False,
                ),
                now,
            ),
        )
    return check_capability(db, cap) | {"ok": True}


class CapabilityLockedError(PermissionError):
    def __init__(self, capability: str, detail: str = ""):
        super().__init__(f"capability locked: {capability} {detail}".strip())
        self.capability = capability
        self.detail = detail


def require_capability(db: Database, capability: str) -> dict[str, Any]:
    pack = check_capability(db, capability)
    if not pack.get("allowed"):
        raise CapabilityLockedError(capability, str(pack.get("detail") or ""))
    return pack
