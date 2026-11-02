# -*- coding: utf-8 -*-
"""UsageLedger：调用级成本账本（E07）。

meter_scope: host | node | embedding | retry
measurement: measured | estimated | unknown
预留 ≠ 回执 ≠ 最终费用；unknown 不转零；零 Node 成本 ≠ 总成本零。
同 correlation/attempt/provider/meter_scope 去重；retry attempt 可区分。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

METER_SCOPES = ("host", "node", "embedding", "retry")
MEASUREMENTS = ("measured", "estimated", "unknown")

DEFAULT_LIMITS: dict[str, Any] = {
    "max_external_calls": 8,
    "max_retrieval_rounds": 4,
    "max_duration_ms": 30_000,
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return f"usage_{uuid.uuid4().hex[:12]}"


def stable_usage_key(
    *,
    correlation_id: str,
    attempt: int,
    provider: str,
    meter_scope: str,
    kind: str = "receipt",
    unit_key: str = "",
) -> str:
    # Billing dedupe: correlation/attempt/provider/meter_scope (+ optional unit).
    # unit_key distinguishes retrieval rounds / sequenced calls without collapsing retries.
    base = f"{correlation_id}|{int(attempt)}|{provider}|{meter_scope}|{kind}"
    return f"{base}|{unit_key}" if unit_key else base


def _normalize_measurement(value: str | None, *, amount: float | None) -> str:
    m = str(value or "").strip().lower()
    if m in MEASUREMENTS:
        return m
    if amount is None:
        return "unknown"
    return "measured"


class UsageLedger:
    def __init__(self, db: Database, *, limits: dict[str, Any] | None = None) -> None:
        self.db = db
        self.db.ensure_schema()
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}

    def reserve(
        self,
        *,
        correlation_id: str,
        attempt: int = 1,
        provider: str = "node",
        meter_scope: str = "node",
        estimated_cost: float | None = None,
        unit_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = meter_scope if meter_scope in METER_SCOPES else "node"
        key = stable_usage_key(
            correlation_id=correlation_id,
            attempt=attempt,
            provider=provider,
            meter_scope=scope,
            kind="reserve",
            unit_key=unit_key,
        )
        now = _now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM usage_ledger_entries WHERE stable_key=?", (key,)
            ).fetchone()
            if row:
                return {"ok": True, "deduped": True, "entry_id": row["id"], "phase": "reserve"}
            eid = _new_id()
            conn.execute(
                """
                INSERT INTO usage_ledger_entries(
                  id, correlation_id, attempt, provider, meter_scope, stable_key,
                  phase, measurement, amount, currency, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserve', ?, ?, 'USD', ?, ?, ?)
                """,
                (
                    eid,
                    correlation_id,
                    int(attempt),
                    provider,
                    scope,
                    key,
                    "estimated" if estimated_cost is not None else "unknown",
                    estimated_cost,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return {"ok": True, "deduped": False, "entry_id": eid, "phase": "reserve"}

    def record_receipt(
        self,
        *,
        correlation_id: str,
        attempt: int = 1,
        provider: str = "node",
        meter_scope: str = "node",
        amount: float | None = None,
        measurement: str | None = None,
        success: bool | None = None,
        unit_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """入账回执。结果未知的付费调用不得因重试伪造一次成功计费。"""
        scope = meter_scope if meter_scope in METER_SCOPES else "node"
        key = stable_usage_key(
            correlation_id=correlation_id,
            attempt=attempt,
            provider=provider,
            meter_scope=scope,
            kind="receipt",
            unit_key=unit_key,
        )
        meas = _normalize_measurement(measurement, amount=amount)
        # 未知结果：amount 保持 unknown，不可记作成功一次计费
        if success is False and amount is None:
            meas = "unknown"
        if success is None and amount is None:
            meas = "unknown"
        now = _now()
        meta = dict(metadata or {})
        if success is not None:
            meta["success"] = success
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id, amount, measurement FROM usage_ledger_entries WHERE stable_key=?",
                (key,),
            ).fetchone()
            if row:
                return {
                    "ok": True,
                    "deduped": True,
                    "entry_id": row["id"],
                    "phase": "receipt",
                    "amount": row["amount"],
                    "measurement": row["measurement"],
                    "note": "same_attempt_receipt_counted_once",
                }
            eid = _new_id()
            conn.execute(
                """
                INSERT INTO usage_ledger_entries(
                  id, correlation_id, attempt, provider, meter_scope, stable_key,
                  phase, measurement, amount, currency, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'receipt', ?, ?, 'USD', ?, ?, ?)
                """,
                (
                    eid,
                    correlation_id,
                    int(attempt),
                    provider,
                    scope,
                    key,
                    meas,
                    amount,
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return {
            "ok": True,
            "deduped": False,
            "entry_id": eid,
            "phase": "receipt",
            "amount": amount,
            "measurement": meas,
        }

    def record_host_usage_missing(self, *, correlation_id: str, attempt: int = 1) -> dict[str, Any]:
        """宿主未提供 usage → 显式 unknown，不转零。"""
        return self.record_receipt(
            correlation_id=correlation_id,
            attempt=attempt,
            provider="host",
            meter_scope="host",
            amount=None,
            measurement="unknown",
            success=None,
            metadata={"reason": "host_usage_missing"},
        )

    def check_and_enter(
        self,
        *,
        correlation_id: str,
        call_kind: str = "external",
        attempt: int = 1,
        provider: str = "node",
        meter_scope: str = "node",
        duration_ms_so_far: float = 0.0,
        retrieval_round: int | None = None,
    ) -> dict[str, Any]:
        """正常链路入口：超额则拒绝/降级并给原因（不只是写字段）。"""
        usage = self._usage_counters(correlation_id)
        reasons: list[str] = []
        max_calls = int(self.limits.get("max_external_calls") or 0)
        max_rounds = int(self.limits.get("max_retrieval_rounds") or 0)
        max_ms = float(self.limits.get("max_duration_ms") or 0)

        external_calls = int(usage.get("external_calls") or 0)
        rounds = int(usage.get("retrieval_rounds") or 0)

        if call_kind in {"external", "embedding", "llm"} and max_calls > 0:
            if external_calls >= max_calls:
                reasons.append("max_external_calls_exceeded")
        if call_kind == "retrieval" and max_rounds > 0:
            next_round = retrieval_round if retrieval_round is not None else rounds + 1
            if next_round > max_rounds:
                reasons.append("max_retrieval_rounds_exceeded")
        if max_ms > 0 and float(duration_ms_so_far) >= max_ms:
            reasons.append("max_duration_ms_exceeded")

        if reasons:
            return {
                "ok": False,
                "allowed": False,
                "degraded": True,
                "reason_codes": reasons,
                "action": "reject_or_degrade",
                "usage": usage,
                "limits": dict(self.limits),
                "note": "limit_enforced_at_entry_not_field_only",
            }

        # 允许：记一次 node 侧调用计数（receipt amount 0 measured for node-local）
        if call_kind in {"external", "embedding", "llm"}:
            seq = external_calls + 1
            self.record_receipt(
                correlation_id=correlation_id,
                attempt=attempt,
                provider=provider,
                meter_scope="embedding" if call_kind == "embedding" else meter_scope,
                amount=0.0,
                measurement="measured",
                success=True,
                unit_key=f"{call_kind}:{seq}",
                metadata={"call_kind": call_kind, "counts_as_external": True, "seq": seq},
            )
        elif call_kind == "retrieval":
            round_no = retrieval_round if retrieval_round is not None else rounds + 1
            self.record_receipt(
                correlation_id=correlation_id,
                attempt=attempt,
                provider=provider,
                meter_scope="node",
                amount=0.0,
                measurement="measured",
                success=True,
                unit_key=f"retrieval:{round_no}",
                metadata={"call_kind": "retrieval", "retrieval_round": round_no},
            )
        return {
            "ok": True,
            "allowed": True,
            "degraded": False,
            "reason_codes": [],
            "usage": self._usage_counters(correlation_id),
            "limits": dict(self.limits),
        }

    def _usage_counters(self, correlation_id: str) -> dict[str, Any]:
        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT attempt, provider, meter_scope, phase, measurement, amount, metadata_json
                FROM usage_ledger_entries
                WHERE correlation_id=? AND phase='receipt'
                ORDER BY attempt, created_at
                """,
                (correlation_id,),
            ).fetchall()
        external_calls = 0
        retrieval_rounds = 0
        attempts = set()
        for row in rows:
            attempts.add(int(row["attempt"]))
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            if meta.get("counts_as_external") or row["meter_scope"] in {"embedding", "retry"}:
                if row["meter_scope"] != "host":
                    external_calls += 1
            if meta.get("call_kind") == "retrieval":
                retrieval_rounds = max(retrieval_rounds, int(meta.get("retrieval_round") or 0))
        return {
            "receipt_count": len(rows),
            "external_calls": external_calls,
            "retrieval_rounds": retrieval_rounds,
            "attempt_ids": sorted(attempts),
        }

    def cost_report(self, *, correlation_id: str) -> dict[str, Any]:
        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT * FROM usage_ledger_entries
                WHERE correlation_id=?
                ORDER BY attempt, created_at
                """,
                (correlation_id,),
            ).fetchall()
        by_scope: dict[str, dict[str, Any]] = {
            s: {"amount": None, "measurement": None, "entries": 0} for s in METER_SCOPES
        }
        for row in rows:
            if row["phase"] != "receipt":
                continue
            scope = row["meter_scope"] if row["meter_scope"] in by_scope else "node"
            slot = by_scope[scope]
            slot["entries"] += 1
            meas = row["measurement"]
            amount = row["amount"]
            if meas == "unknown" or amount is None:
                # unknown 不转零；一旦未知则整 scope 金额不可当作实测总和
                slot["measurement"] = "unknown"
                slot["amount"] = None
                continue
            if slot["measurement"] == "unknown":
                continue
            prev = 0.0 if slot["amount"] is None else float(slot["amount"])
            slot["amount"] = prev + float(amount)
            if meas == "estimated" or slot["measurement"] == "estimated":
                slot["measurement"] = "estimated"
            else:
                slot["measurement"] = "measured"

        # Node 可零；宿主缺失 → unknown；总费用不冒充零
        node = by_scope["node"]
        host = by_scope["host"]
        embedding = by_scope["embedding"]
        retry = by_scope["retry"]
        if node["entries"] == 0:
            node["amount"] = 0.0
            node["measurement"] = "measured"
            node["note"] = "zero_external_node_calls"
        elif node["measurement"] is None:
            node["measurement"] = "measured"
            node["amount"] = node["amount"] if node["amount"] is not None else 0.0
        if host["entries"] == 0:
            host["measurement"] = "unknown"
            host["amount"] = None
            host["note"] = "host_usage_missing"
        for optional in (embedding, retry):
            if optional["entries"] == 0:
                optional["measurement"] = "measured"
                optional["amount"] = 0.0

        total_measurement = "measured"
        total_amount: float | None = 0.0
        contributing = [node, embedding, retry, host]
        for slot in contributing:
            if slot["measurement"] == "unknown":
                total_measurement = "unknown"
                total_amount = None
                break
            if slot["measurement"] == "estimated" and total_measurement == "measured":
                total_measurement = "estimated"
            if slot["amount"] is not None:
                total_amount = (total_amount or 0.0) + float(slot["amount"])

        if total_measurement == "unknown":
            total_amount = None

        return {
            "ok": True,
            "correlation_id": correlation_id,
            "scopes": by_scope,
            "node_cost": {
                "amount": node["amount"],
                "measurement": node["measurement"],
                "zero_ok": True,
            },
            "host_cost": {
                "amount": host["amount"],
                "measurement": host["measurement"],
                "zero_ok": False,
            },
            "total": {
                "amount": total_amount,
                "measurement": total_measurement,
                "note": (
                    "total_not_pretend_zero_when_host_unknown"
                    if total_measurement == "unknown"
                    else "finalized"
                ),
            },
            "counters": self._usage_counters(correlation_id),
            "limits": dict(self.limits),
            "boundary": {
                "node_zero_ne_total_zero": True,
                "unknown_not_coerced_to_zero": True,
                "node_does_not_cap_user_account": True,
            },
        }
