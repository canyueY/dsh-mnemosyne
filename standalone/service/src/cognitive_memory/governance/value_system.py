# -*- coding: utf-8 -*-
"""价值与伦理决策辅助：V-A 常开；V-B/C 受 Gate；硬约束不可改。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.governance.capability_gate import (
    CapabilityLockedError,
    check_capability,
    require_capability,
)
from cognitive_memory.storage.db import Database

VALUE_DIMENSIONS = (
    "harm_avoidance",
    "fairness",
    "honesty",
    "autonomy",
    "privacy",
    "commitment",
    "user_preference",
)

HARD_CONSTRAINTS = (
    "safety_rules",
    "privacy_boundary",
    "user_authorization",
    "system_permissions",
    "capability_gate",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def analyze_value_conflict_basic(
    intent: str,
    *,
    stakeholders: list[str] | None = None,
    proposed_action: str = "",
) -> dict[str, Any]:
    """V-A：基础价值冲突说明（无模型训练）。"""
    dims = list(VALUE_DIMENSIONS)
    conflicts: list[dict[str, str]] = []
    text = f"{intent} {proposed_action}".lower()
    if any(k in text for k in ("隐私", "privacy", "泄露", "分享联系人")):
        conflicts.append(
            {
                "between": "privacy vs user_preference",
                "note": "可能涉及隐私硬约束，不可被软偏好覆盖",
            }
        )
    if any(k in text for k in ("授权", "权限", "绕过", "bypass")):
        conflicts.append(
            {
                "between": "user_authorization vs autonomy",
                "note": "授权边界属硬约束",
            }
        )
    return {
        "ok": True,
        "phase": "V-A",
        "intent": intent,
        "stakeholders": stakeholders or ["user"],
        "dimensions": dims,
        "hard_constraints": list(HARD_CONSTRAINTS),
        "conflicts": conflicts,
        "uncertainty": "high" if not conflicts else "medium",
        "note": "解释冲突，不输出单一道德总分",
    }


def run_advanced_value_evaluation(
    db: Database,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """V-B：多维度加权报告（非单一道德分决策）；Gate 默认 LOCKED。"""
    try:
        require_capability(db, "ADVANCED_VALUE_EVALUATION")
    except CapabilityLockedError as exc:
        return {
            "ok": False,
            "detail": "capability_locked",
            "capability": exc.capability,
            "gate": check_capability(db, "ADVANCED_VALUE_EVALUATION"),
        }

    body = dict(payload or {})
    basic = analyze_value_conflict_basic(
        str(body.get("intent") or ""),
        stakeholders=body.get("stakeholders") if isinstance(body.get("stakeholders"), list) else None,
        proposed_action=str(body.get("proposed_action") or ""),
    )
    prefs = _load_soft_prefs(db)
    weights = {d: float(prefs.get(d, 1.0)) for d in VALUE_DIMENSIONS}
    # Heuristic pressure scores 0..1 per dimension from keyword hits
    text = f"{body.get('intent','')} {body.get('proposed_action','')}".lower()
    pressure = {d: 0.1 for d in VALUE_DIMENSIONS}
    if any(k in text for k in ("隐私", "privacy")):
        pressure["privacy"] = 0.9
    if any(k in text for k in ("伤害", "harm", "危险")):
        pressure["harm_avoidance"] = 0.85
    if any(k in text for k in ("公平", "fair")):
        pressure["fairness"] = 0.7
    if basic["conflicts"]:
        pressure["honesty"] = max(pressure["honesty"], 0.6)

    weighted = {
        d: round(pressure[d] * weights.get(d, 1.0), 4) for d in VALUE_DIMENSIONS
    }
    hard_block = any(
        c.get("between", "").startswith("privacy")
        or "authorization" in c.get("between", "")
        for c in basic["conflicts"]
    )
    return {
        "ok": True,
        "phase": "V-B",
        "basic": basic,
        "weights": weights,
        "pressure": pressure,
        "weighted_pressure": weighted,
        "hard_block_recommended": hard_block,
        "single_moral_score": None,
        "note": "multi-dimension report; refuses single moral scalar as authority",
        "payload": body,
    }


def adapt_value_model(db: Database, patch: dict[str, Any] | None = None) -> dict[str, Any]:
    """V-C：软偏好写入；硬约束拒绝；Gate 默认 LOCKED。"""
    try:
        require_capability(db, "AUTONOMOUS_VALUE_ADAPTATION")
    except CapabilityLockedError as exc:
        return {
            "ok": False,
            "detail": "capability_locked",
            "capability": exc.capability,
            "gate": check_capability(db, "AUTONOMOUS_VALUE_ADAPTATION"),
        }
    patch = dict(patch or {})
    actor = str(patch.pop("_actor", None) or "governance")
    bad = [k for k in patch if k in HARD_CONSTRAINTS]
    if bad:
        return {"ok": False, "detail": "immutable_hard_constraint", "keys": bad}
    soft_keys = [k for k in patch if k in VALUE_DIMENSIONS]
    unknown = [k for k in patch if k not in VALUE_DIMENSIONS and k not in HARD_CONSTRAINTS]
    if unknown:
        return {"ok": False, "detail": "unknown_soft_keys", "keys": unknown}
    if not soft_keys:
        return {"ok": False, "detail": "empty_soft_patch"}

    now = _now()
    with db.transaction() as conn:
        for k in soft_keys:
            val = patch[k]
            try:
                f = float(val)
            except (TypeError, ValueError):
                return {"ok": False, "detail": "soft_pref_must_be_numeric", "key": k}
            # clamp soft weights
            f = max(0.0, min(f, 5.0))
            conn.execute(
                """
                INSERT INTO value_soft_prefs(key, value_json, updated_at, actor)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at,
                  actor=excluded.actor
                """,
                (k, json.dumps(f), now, actor),
            )
    return {
        "ok": True,
        "phase": "V-C",
        "updated": soft_keys,
        "prefs": _load_soft_prefs(db),
        "note": "soft prefs only; hard constraints untouched",
    }


def _load_soft_prefs(db: Database) -> dict[str, float]:
    db.ensure_schema()
    out: dict[str, float] = {}
    with db.transaction() as conn:
        rows = conn.execute("SELECT key, value_json FROM value_soft_prefs").fetchall()
    for r in rows:
        try:
            out[str(r["key"])] = float(json.loads(r["value_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return out
