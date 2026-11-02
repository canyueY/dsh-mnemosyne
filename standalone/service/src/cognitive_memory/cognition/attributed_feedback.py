# -*- coding: utf-8 -*-
"""归因反馈 → 有限策略学习（E13）。

满意/自评/重复 task_success 可记 utility，不改 verification。
训练与评估根来源重叠 → 拒绝污染采用。
公开合成收益标 not_measured，不宣称真实 heldout 效果。
采用/回滚检查权限与 CAS，无半采用。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.cognition.feedback_learning import (
    SELF_EVAL,
    USER_EXPLICIT,
    classify_feedback,
)
from cognitive_memory.cognition.loop_models import TaskFeedback
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_execution_to_task_feedback(
    *,
    outcome: str | None,
    satisfaction: bool | None = None,
    self_eval_passed: bool | None = None,
    repeated_task_success: bool = False,
    attribution_evidence: list[str] | None = None,
) -> dict[str, Any]:
    """真实回执映射 TaskFeedback；默认归因 unresolved。"""
    kind = "unclassified"
    source = "execution"
    if satisfaction is True:
        kind = "expression_preference"
        source = "user"
    elif self_eval_passed is True:
        kind = "task_outcome"
        source = "agent_self_eval"
    elif outcome in {"success", "failure"}:
        kind = "task_outcome"
        source = "execution"
    elif outcome == "unknown":
        kind = "unclassified"
        source = "execution"

    evidence = list(attribution_evidence or [])
    attribution = "resolved" if evidence else "unresolved"
    plan = classify_feedback(
        source=USER_EXPLICIT if source == "user" else (SELF_EVAL if source == "agent_self_eval" else "outcome"),
        useful=True if satisfaction else None,
        outcome=outcome if outcome in {"success", "failure"} else None,
        self_score=1.0 if self_eval_passed else None,
    )
    # 好评/自评/重复成功不晋升 verified
    verification_delta = 0
    feedback = TaskFeedback(
        kind=kind,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        note="attributed_feedback_e13",
        possible_stages=["strategy", "execution", "preference"],
    )
    return {
        "ok": True,
        "task_feedback": feedback.model_dump(),
        "utility": {
            "recorded": True,
            "signal": plan["utility_signal"],
            "repeated_task_success": repeated_task_success,
        },
        "verification": {
            "changed": False,
            "delta": verification_delta,
            "promote_eligible": False,
        },
        "attribution": attribution,
        "attribution_evidence": evidence,
        "plan": plan,
        "boundary": {
            "satisfaction_ne_verified": True,
            "self_eval_ne_verified": True,
            "repeated_success_ne_verified": True,
        },
    }


class LimitedStrategyLearner:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def shadow_evaluate(
        self,
        *,
        strategy_id: str,
        train_root_sources: list[str],
        eval_root_sources: list[str],
        synthetic_gain: float | None = None,
        suite_passed: bool = True,
        expected_version: int = 1,
        actor: str = "node",
        authorized: bool = True,
    ) -> dict[str, Any]:
        train = set(train_root_sources)
        eval_s = set(eval_root_sources)
        overlap = sorted(train & eval_s)
        contaminated = bool(overlap)
        report = {
            "strategy_id": strategy_id,
            "suite_passed": suite_passed,
            "independent_eval_net_gain": None if contaminated else synthetic_gain,
            "regression_loss": 0.0 if suite_passed else 1.0,
            "user_effect": "not_measured",
            "public_case_mechanism_only": True,
            "heldout_claimed": False,
            "contamination": {
                "detected": contaminated,
                "overlap_roots": overlap,
            },
            "synthetic_gain_not_real_effect": True,
        }
        if contaminated:
            return {
                "ok": False,
                "adopted": False,
                "code": "train_eval_source_overlap",
                "detail": "拒绝污染采用",
                "report": report,
            }
        if not suite_passed:
            return {
                "ok": False,
                "adopted": False,
                "code": "validation_suite_failed",
                "report": report,
            }
        if not authorized:
            return {
                "ok": False,
                "adopted": False,
                "code": "permission_denied",
                "report": report,
            }
        # 影子评估通过：写入候选，尚未 adopt
        cid = f"learn_{uuid.uuid4().hex[:10]}"
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO strategy_learning_candidates(
                  id, strategy_id, version, status, actor, report_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'evaluated', ?, ?, ?, ?)
                """,
                (
                    cid,
                    strategy_id,
                    int(expected_version),
                    actor,
                    json.dumps(report, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return {
            "ok": True,
            "adopted": False,
            "candidate_id": cid,
            "report": report,
        }

    def adopt(
        self,
        candidate_id: str,
        *,
        expected_version: int,
        authorized: bool = True,
        current_version: int | None = None,
    ) -> dict[str, Any]:
        if not authorized:
            return {"ok": False, "code": "permission_denied", "partial_adopt": False}
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_learning_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "code": "not_found", "partial_adopt": False}
            if row["status"] == "adopted":
                return {"ok": True, "adopted": True, "idempotent": True, "partial_adopt": False}
            if int(row["version"]) != int(expected_version):
                return {
                    "ok": False,
                    "code": "cas_version_mismatch",
                    "partial_adopt": False,
                    "expected": expected_version,
                    "actual": row["version"],
                }
            if current_version is not None and int(current_version) != int(expected_version):
                return {
                    "ok": False,
                    "code": "cas_version_mismatch",
                    "partial_adopt": False,
                    "detail": "version changed after candidate passed",
                }
            report = json.loads(row["report_json"] or "{}")
            if report.get("contamination", {}).get("detected"):
                return {
                    "ok": False,
                    "code": "train_eval_source_overlap",
                    "partial_adopt": False,
                }
            conn.execute(
                """
                UPDATE strategy_learning_candidates
                SET status='adopted', updated_at=?
                WHERE id=? AND version=? AND status='evaluated'
                """,
                (_now(), candidate_id, int(expected_version)),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            if not changed:
                return {"ok": False, "code": "cas_race", "partial_adopt": False}
        return {"ok": True, "adopted": True, "candidate_id": candidate_id, "partial_adopt": False}

    def rollback(
        self,
        candidate_id: str,
        *,
        expected_version: int,
        authorized: bool = True,
        current_version: int | None = None,
    ) -> dict[str, Any]:
        if not authorized:
            return {"ok": False, "code": "permission_denied", "partial_adopt": False}
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_learning_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "code": "not_found", "partial_adopt": False}
            if current_version is not None and int(current_version) != int(expected_version):
                return {
                    "ok": False,
                    "code": "cas_version_mismatch",
                    "partial_adopt": False,
                }
            if int(row["version"]) != int(expected_version) and row["status"] == "adopted":
                # 版本已变：拒绝覆盖后续合法状态
                return {
                    "ok": False,
                    "code": "cas_version_mismatch",
                    "partial_adopt": False,
                }
            if row["status"] != "adopted":
                return {"ok": False, "code": "not_adopted", "partial_adopt": False}
            conn.execute(
                """
                UPDATE strategy_learning_candidates
                SET status='rolled_back', updated_at=?
                WHERE id=? AND status='adopted'
                """,
                (_now(), candidate_id),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            if not changed:
                return {"ok": False, "code": "cas_race", "partial_adopt": False}
        return {"ok": True, "rolled_back": True, "partial_adopt": False}
