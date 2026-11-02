# -*- coding: utf-8 -*-
"""Online A/B metrics — exposures/outcomes only.

Requires BANDIT_EXPERIMENT for assign/record/report.
Never deploys Strategy ACTIVE. Never marks Claim Verified.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.governance.capability_gate import (
    CapabilityLockedError,
    check_capability,
    require_capability,
)
from cognitive_memory.storage.db import Database

SEMANTIC_ASSIST_EXPERIMENT = "semantic_assist.v1"
STRATEGY_EXPERIMENT = "strategy.propensity.v1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "abx") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _require_bandit(db: Database) -> dict[str, Any]:
    try:
        pack = require_capability(db, "BANDIT_EXPERIMENT")
        return {"ok": True, **pack}
    except CapabilityLockedError as exc:
        return {
            "ok": False,
            "detail": "capability_locked",
            "capability": exc.capability,
            "gate": check_capability(db, "BANDIT_EXPERIMENT"),
        }


def assign_arm(
    db: Database,
    experiment_id: str,
    *,
    subject_key: str = "",
    context: dict[str, Any] | None = None,
    arms: tuple[str, ...] = ("control", "treatment"),
) -> dict[str, Any]:
    """Deterministic 50/50 arm assignment + exposure log."""
    gate = _require_bandit(db)
    if gate.get("detail") == "capability_locked":
        return gate

    exp = (experiment_id or "").strip() or SEMANTIC_ASSIST_EXPERIMENT
    subject = str(subject_key or "")
    seed = f"{exp}|{subject}|{json.dumps(context or {}, sort_keys=True, ensure_ascii=False)}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    arm_list = list(arms) or ["control", "treatment"]
    arm = arm_list[int(digest[:8], 16) % len(arm_list)]
    eid = _new_id()
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ab_exposures(
              id, experiment_id, arm, subject_key, context_json, created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                eid,
                exp,
                arm,
                subject,
                json.dumps(dict(context or {}), ensure_ascii=False),
                now,
            ),
        )
    return {
        "ok": True,
        "exposure_id": eid,
        "experiment_id": exp,
        "arm": arm,
        "subject_key": subject,
        "created_at": now,
        "note": "exposure only; no policy deploy",
    }


def record_outcome(
    db: Database,
    exposure_id: str,
    *,
    reward: float | None = None,
    label: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = _require_bandit(db)
    if gate.get("detail") == "capability_locked":
        return gate

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT experiment_id FROM ab_exposures WHERE id=?",
            (exposure_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "detail": "exposure_not_found", "exposure_id": exposure_id}
        oid = _new_id("abo")
        now = _now()
        conn.execute(
            """
            INSERT INTO ab_outcomes(
              id, exposure_id, experiment_id, reward, label, detail_json, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                oid,
                exposure_id,
                row["experiment_id"],
                reward,
                label or "",
                json.dumps(dict(detail or {}), ensure_ascii=False),
                now,
            ),
        )
    return {
        "ok": True,
        "outcome_id": oid,
        "exposure_id": exposure_id,
        "experiment_id": row["experiment_id"],
        "reward": reward,
        "label": label,
    }


def report(
    db: Database,
    experiment_id: str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    gate = _require_bandit(db)
    if gate.get("detail") == "capability_locked":
        return gate

    exp = (experiment_id or "").strip() or SEMANTIC_ASSIST_EXPERIMENT
    lim = max(1, min(int(limit), 5000))
    with db.transaction() as conn:
        exposures = conn.execute(
            """
            SELECT arm, COUNT(*) AS n FROM ab_exposures
            WHERE experiment_id=? GROUP BY arm
            """,
            (exp,),
        ).fetchall()
        outcomes = conn.execute(
            """
            SELECT e.arm AS arm, o.reward AS reward
            FROM ab_outcomes o
            JOIN ab_exposures e ON e.id = o.exposure_id
            WHERE o.experiment_id=?
            ORDER BY o.created_at DESC
            LIMIT ?
            """,
            (exp, lim),
        ).fetchall()

    by_arm: dict[str, dict[str, Any]] = {}
    for r in exposures:
        arm = str(r["arm"])
        by_arm[arm] = {
            "exposures": int(r["n"]),
            "outcomes": 0,
            "mean_reward": None,
            "rewards": [],
        }
    for r in outcomes:
        arm = str(r["arm"])
        pack = by_arm.setdefault(
            arm,
            {"exposures": 0, "outcomes": 0, "mean_reward": None, "rewards": []},
        )
        pack["outcomes"] += 1
        if r["reward"] is not None:
            pack["rewards"].append(float(r["reward"]))
    for arm, pack in by_arm.items():
        rewards = pack.pop("rewards")
        pack["mean_reward"] = (
            round(sum(rewards) / len(rewards), 6) if rewards else None
        )

    return {
        "ok": True,
        "experiment_id": exp,
        "arms": by_arm,
        "deploy": {
            "allowed": False,
            "detail": "A/B report never activates Strategy or Verified",
        },
        "note": "metrics only",
    }


def resolve_semantic_assist_arm(
    db: Database,
    *,
    enabled: bool,
    subject_key: str = "",
    query: str = "",
) -> dict[str, Any]:
    """
    If enabled+unlocked: assign arm and return whether assist should run.
    control → assist False; treatment → assist True.
    If disabled or locked: passthrough (caller keeps its semantic_assist flag).
    """
    if not enabled:
        return {"ok": True, "active": False, "override": None}
    assigned = assign_arm(
        db,
        SEMANTIC_ASSIST_EXPERIMENT,
        subject_key=subject_key or query[:64],
        context={"query_len": len(query or "")},
    )
    if not assigned.get("ok"):
        return {
            "ok": False,
            "active": False,
            "override": None,
            "ab": assigned,
        }
    arm = str(assigned.get("arm") or "control")
    return {
        "ok": True,
        "active": True,
        "override": arm == "treatment",
        "arm": arm,
        "exposure_id": assigned.get("exposure_id"),
        "ab": assigned,
    }
