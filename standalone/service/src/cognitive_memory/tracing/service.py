# -*- coding: utf-8 -*-
"""JudgmentTrace service — governance events are never sampled away."""

from __future__ import annotations

import secrets
import json
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.tracing.models import JudgmentTrace


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JudgmentTraceService:
    def __init__(self, db=None) -> None:
        self.db = db
        if db is not None:
            db.ensure_schema()
        self._traces: list[JudgmentTrace] = []
        self._write_failures: list[dict[str, Any]] = []

    def record(
        self,
        judgment_type: str,
        *,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        reason_codes: list[str] | None = None,
        algorithm_version: str = "",
        rule_version: str = "",
        governance_critical: bool = False,
        uncertainty: str = "unknown",
        duration_ms: float | None = None,
        fail_write: bool = False,
    ) -> JudgmentTrace | None:
        trace = JudgmentTrace(
            trace_id=f"jt_{secrets.token_hex(8)}",
            judgment_type=judgment_type,
            input_refs=list(input_refs or []),
            output_refs=list(output_refs or []),
            evidence_refs=list(evidence_refs or []),
            reason_codes=list(reason_codes or []),
            algorithm_version=algorithm_version,
            rule_version=rule_version,
            governance_critical=governance_critical,
            uncertainty=uncertainty,
            duration_ms=duration_ms,
            timestamp=_now(),
            trace_completeness="complete" if algorithm_version and rule_version else "partial",
        )
        if fail_write:
            self._write_failures.append(
                {"error": "trace_write_failed", "trace": trace.model_dump(mode="json")}
            )
            if governance_critical and self.db is not None:
                raise RuntimeError("critical_trace_write_failed")
            return None
        if self.db is not None:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO judgment_traces VALUES (?, ?, ?, ?, ?)",
                    (trace.trace_id, judgment_type, int(governance_critical),
                     trace.model_dump_json(), trace.timestamp),
                )
        self._traces.append(trace)
        return trace

    def list_traces(self, *, governance_only: bool = False) -> list[JudgmentTrace]:
        if self.db is not None:
            with self.db.exclusive() as conn:
                rows = conn.execute(
                    "SELECT payload_json FROM judgment_traces "
                    + ("WHERE governance_critical=1 " if governance_only else "")
                    + "ORDER BY created_at, rowid"
                ).fetchall()
            return [JudgmentTrace.model_validate_json(row[0]) for row in rows]
        if governance_only:
            return [t for t in self._traces if t.governance_critical]
        return list(self._traces)

    def failures(self) -> list[dict[str, Any]]:
        return list(self._write_failures)
