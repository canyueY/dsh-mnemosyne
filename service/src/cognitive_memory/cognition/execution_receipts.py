# -*- coding: utf-8 -*-
"""Action / ExecutionReceipt / Outcome（E12）。

计划、配置保存、运行采用、执行确认、效果观察分别记账。
真实执行权留给宿主/执行器；FakeExecutor 仅协议验收。
效果未知不写 task_success=true；版本不匹配拒绝 apply/rollback。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from cognitive_memory.storage.db import Database

PHASES = (
    "planned",
    "saved",
    "adopted",
    "execution_confirmed",
    "effect_observed",
    "reconcile_required",
    "rolled_back",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ExecutionReceiptStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def plan_action(
        self,
        *,
        actor: str,
        bank_id: str,
        capability: str,
        payload: dict[str, Any],
        expected_state_version: int,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        proposal_id = _new_id("prop")
        execution_id = _new_id("exec")
        corr = correlation_id or _new_id("corr")
        idem = idempotency_key or f"{proposal_id}:{execution_id}"
        now = _now()
        with self.db.transaction() as conn:
            dup = conn.execute(
                "SELECT proposal_id FROM execution_actions WHERE idempotency_key=?",
                (idem,),
            ).fetchone()
            if dup:
                return self.get_action(dup["proposal_id"])
            conn.execute(
                """
                INSERT INTO execution_actions(
                  proposal_id, execution_id, correlation_id, actor, bank_id, capability,
                  expected_state_version, state_version, idempotency_key, phase,
                  payload_json, receipt_json, outcome_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, '{}', '{}', ?, ?)
                """,
                (
                    proposal_id,
                    execution_id,
                    corr,
                    actor,
                    bank_id,
                    capability,
                    int(expected_state_version),
                    int(expected_state_version),
                    idem,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_action(proposal_id)

    def save_config(self, proposal_id: str) -> dict[str, Any]:
        return self._advance(proposal_id, to_phase="saved", extra_receipt={"saved": True})

    def adopt_runtime(self, proposal_id: str) -> dict[str, Any]:
        """运行采用与配置保存分开；仅 saved 不能算 execution_success。"""
        return self._advance(proposal_id, to_phase="adopted", extra_receipt={"adopted": True})

    def confirm_execution(
        self,
        proposal_id: str,
        *,
        receipt_source: str = "executor",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        return self._advance(
            proposal_id,
            to_phase="execution_confirmed",
            extra_receipt={
                "execution_confirmed": True,
                "receipt_source": receipt_source,
                "observed_at": observed_at or _now(),
            },
        )

    def observe_effect(
        self,
        proposal_id: str,
        *,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """outcome: success|failure|unknown。unknown 不得永久改偏好/verified。"""
        oc = str(outcome or "unknown").lower()
        if oc not in {"success", "failure", "unknown"}:
            oc = "unknown"
        pack = {
            "outcome": oc,
            "task_success": oc == "success",
            "preference_mutated": False,
            "verified_mutated": False,
            "details": dict(details or {}),
            "observed_at": _now(),
        }
        if oc == "unknown":
            pack["task_success"] = False
            pack["note"] = "effect_unknown_no_permanent_preference_or_verified"
        return self._advance(
            proposal_id,
            to_phase="effect_observed",
            extra_receipt={"effect_observed": True},
            outcome=pack,
        )

    def apply(
        self,
        proposal_id: str,
        *,
        current_state_version: int,
        mutator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        action = self.get_action(proposal_id)
        if not action.get("ok"):
            return action
        if int(action["expected_state_version"]) != int(current_state_version):
            return {
                "ok": False,
                "code": "version_precondition_failed",
                "detail": "apply refused: expected_state_version mismatch",
                "expected": action["expected_state_version"],
                "current": current_state_version,
            }
        state = {"version": int(current_state_version), "payload": dict(action.get("payload") or {})}
        if mutator:
            state = mutator(state)
        new_version = int(state.get("version", current_state_version) + 1)
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE execution_actions
                SET state_version=?, updated_at=?,
                    receipt_json=?
                WHERE proposal_id=?
                """,
                (
                    new_version,
                    _now(),
                    json.dumps(
                        {
                            **(action.get("receipt") or {}),
                            "config_write_prepared": True,
                            "state_version": new_version,
                        },
                        ensure_ascii=False,
                    ),
                    proposal_id,
                ),
            )
        out = self.get_action(proposal_id)
        out["applied_state"] = state
        out["new_state_version"] = new_version
        return out

    def rollback(
        self,
        proposal_id: str,
        *,
        current_state_version: int,
        expected_rollback_version: int,
    ) -> dict[str, Any]:
        """回滚也需要版本前置条件，不能覆盖后续合法更改。"""
        action = self.get_action(proposal_id)
        if not action.get("ok"):
            return action
        if int(current_state_version) != int(expected_rollback_version):
            return {
                "ok": False,
                "code": "version_precondition_failed",
                "detail": "rollback refused: state moved by later authorized ops",
                "expected_rollback_version": expected_rollback_version,
                "current": current_state_version,
                "preserved": True,
            }
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE execution_actions
                SET phase='rolled_back', updated_at=?,
                    receipt_json=?
                WHERE proposal_id=?
                """,
                (
                    _now(),
                    json.dumps(
                        {**(action.get("receipt") or {}), "rolled_back": True},
                        ensure_ascii=False,
                    ),
                    proposal_id,
                ),
            )
        return self.get_action(proposal_id)

    def get_action(self, proposal_id: str) -> dict[str, Any]:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT * FROM execution_actions WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        if not row:
            return {"ok": False, "detail": "not_found"}
        try:
            payload = json.loads(row["payload_json"] or "{}")
            receipt = json.loads(row["receipt_json"] or "{}")
            outcome = json.loads(row["outcome_json"] or "{}")
        except json.JSONDecodeError:
            payload, receipt, outcome = {}, {}, {}
        saved = bool(receipt.get("saved"))
        adopted = bool(receipt.get("adopted") or receipt.get("applied"))
        execution_success = bool(
            receipt.get("execution_confirmed") and (outcome.get("outcome") == "success")
        )
        return {
            "ok": True,
            "proposal_id": row["proposal_id"],
            "execution_id": row["execution_id"],
            "correlation_id": row["correlation_id"],
            "actor": row["actor"],
            "bank_id": row["bank_id"],
            "capability": row["capability"],
            "expected_state_version": row["expected_state_version"],
            "state_version": row["state_version"],
            "idempotency_key": row["idempotency_key"],
            "phase": row["phase"],
            "payload": payload,
            "receipt": receipt,
            "outcome": outcome,
            "distinctions": {
                "saved": saved,
                "adopted": adopted,
                "execution_success": execution_success,
                "saved_ne_execution_success": True,
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _advance(
        self,
        proposal_id: str,
        *,
        to_phase: str,
        extra_receipt: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = self.get_action(proposal_id)
        if not action.get("ok"):
            return action
        receipt = {**(action.get("receipt") or {}), **(extra_receipt or {})}
        oc = outcome if outcome is not None else (action.get("outcome") or {})
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE execution_actions
                SET phase=?, receipt_json=?, outcome_json=?, updated_at=?
                WHERE proposal_id=?
                """,
                (
                    to_phase,
                    json.dumps(receipt, ensure_ascii=False),
                    json.dumps(oc, ensure_ascii=False),
                    _now(),
                    proposal_id,
                ),
            )
        return self.get_action(proposal_id)


class FakeExecutor:
    """隔离假执行器：preview/apply/verify/reconcile/rollback，不触发真实平台操作。"""

    def __init__(self, store: ExecutionReceiptStore) -> None:
        self.store = store
        self._state_version = 1
        self._state: dict[str, Any] = {"cfg": {}}

    @property
    def state_version(self) -> int:
        return self._state_version

    def preview(self, *, actor: str, bank_id: str, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = self.store.plan_action(
            actor=actor,
            bank_id=bank_id,
            capability=capability,
            payload=payload,
            expected_state_version=self._state_version,
        )
        return {"ok": True, "preview": True, "real_platform_ops": False, "action": action}

    def apply(self, proposal_id: str) -> dict[str, Any]:
        def _mutate(state: dict[str, Any]) -> dict[str, Any]:
            payload = state.get("payload") or {}
            self._state["cfg"] = dict(payload.get("config") or payload)
            return state

        out = self.store.apply(
            proposal_id,
            current_state_version=self._state_version,
            mutator=_mutate,
        )
        if not out.get("ok"):
            return out
        self._state_version = int(out["new_state_version"])
        saved = self.store.save_config(proposal_id)
        # runtime 未采用前不得 execution_success
        saved["runtime_adopted"] = False
        saved["real_platform_ops"] = False
        return saved

    def adopt(self, proposal_id: str) -> dict[str, Any]:
        out = self.store.adopt_runtime(proposal_id)
        out["runtime_adopted"] = True
        out["real_platform_ops"] = False
        return out

    def verify_execution(self, proposal_id: str) -> dict[str, Any]:
        return self.store.confirm_execution(proposal_id, receipt_source="fake_executor")

    def observe(self, proposal_id: str, outcome: str) -> dict[str, Any]:
        return self.store.observe_effect(proposal_id, outcome=outcome)

    def reconcile(self, proposal_id: str) -> dict[str, Any]:
        action = self.store.get_action(proposal_id)
        if not action.get("ok"):
            return action
        return self.store._advance(
            proposal_id,
            to_phase="reconcile_required",
            extra_receipt={"reconcile_required": True},
            outcome={
                **(action.get("outcome") or {}),
                "outcome": "unknown",
                "task_success": False,
            },
        )

    def rollback(self, proposal_id: str, *, expected_version: int | None = None) -> dict[str, Any]:
        expected = self._state_version if expected_version is None else int(expected_version)
        out = self.store.rollback(
            proposal_id,
            current_state_version=self._state_version,
            expected_rollback_version=expected,
        )
        out["real_platform_ops"] = False
        return out
