# -*- coding: utf-8 -*-
"""E07 AC26–AC28：UsageLedger 调用预算与成本账本。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition.answer_selection import AnswerSelectionService
from cognitive_memory.tracing.cost_ledger import UsageLedger


class RuntimeCostLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e07-token")
        self.ledger = UsageLedger(
            self.ctx.db,
            limits={
                "max_external_calls": 2,
                "max_retrieval_rounds": 2,
                "max_duration_ms": 1000,
            },
        )

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac26(self) -> None:
        """Node 零外部调用，宿主 usage 缺失 → Node 可零，宿主未知，总费用不冒充零。"""
        corr = "corr-ac26"
        self.ledger.record_host_usage_missing(correlation_id=corr)
        report = self.ledger.cost_report(correlation_id=corr)
        self.assertEqual(report["node_cost"]["amount"], 0.0)
        self.assertEqual(report["node_cost"]["measurement"], "measured")
        self.assertEqual(report["host_cost"]["measurement"], "unknown")
        self.assertIsNone(report["host_cost"]["amount"])
        self.assertEqual(report["total"]["measurement"], "unknown")
        self.assertIsNone(report["total"]["amount"])
        self.assertNotEqual(report["total"]["amount"], 0)
        self.assertTrue(report["boundary"]["node_zero_ne_total_zero"])
        self.assertTrue(report["boundary"]["unknown_not_coerced_to_zero"])

    def test_ac27(self) -> None:
        """同 attempt 回执重复 → 只入一次；retry attempt 可区分。"""
        corr = "corr-ac27"
        self.ledger.reserve(
            correlation_id=corr, attempt=1, provider="openai", meter_scope="node", estimated_cost=0.02
        )
        r1 = self.ledger.record_receipt(
            correlation_id=corr,
            attempt=1,
            provider="openai",
            meter_scope="node",
            amount=0.02,
            measurement="measured",
            success=True,
        )
        r1b = self.ledger.record_receipt(
            correlation_id=corr,
            attempt=1,
            provider="openai",
            meter_scope="node",
            amount=0.02,
            measurement="measured",
            success=True,
        )
        self.assertFalse(r1.get("deduped"))
        self.assertTrue(r1b.get("deduped"))

        # 结果未知的付费调用：不因重试伪造成功计费
        unknown = self.ledger.record_receipt(
            correlation_id=corr,
            attempt=2,
            provider="openai",
            meter_scope="retry",
            amount=None,
            measurement="unknown",
            success=None,
            metadata={"retry_of": 1},
        )
        self.assertEqual(unknown["measurement"], "unknown")
        self.assertIsNone(unknown["amount"])

        retry_ok = self.ledger.record_receipt(
            correlation_id=corr,
            attempt=3,
            provider="openai",
            meter_scope="retry",
            amount=0.02,
            measurement="measured",
            success=True,
        )
        self.assertFalse(retry_ok.get("deduped"))
        counters = self.ledger._usage_counters(corr)
        self.assertIn(1, counters["attempt_ids"])
        self.assertIn(3, counters["attempt_ids"])
        report = self.ledger.cost_report(correlation_id=corr)
        # attempt1 once；attempt2 unknown 使 retry scope 未知；attempt3 因同 scope 已 unknown 不冒充实测总和
        self.assertEqual(report["scopes"]["node"]["amount"], 0.02)
        self.assertEqual(report["scopes"]["node"]["measurement"], "measured")
        self.assertEqual(report["scopes"]["retry"]["measurement"], "unknown")
        self.assertIsNone(report["scopes"]["retry"]["amount"])

    def test_ac28(self) -> None:
        """将超过轮数或调用限额 → 入口拒绝/降级且有原因，不只是保存字段。"""
        corr = "corr-ac28"
        self.ctx.service.call_budget_config = {
            "max_external_calls": 1,
            "max_retrieval_rounds": 1,
            "max_duration_ms": 5000,
        }
        sel = AnswerSelectionService(self.ctx.service)

        first = sel.select_answer(
            query="",
            target={"subject_id": "A", "predicate": "喜欢"},
            context={"correlation_id": corr},
            budget={"max_retrieval_rounds": 1, "correlation_id": corr},
        )
        self.assertNotIn("max_retrieval_rounds_exceeded", first.get("reason_codes") or [])

        second = sel.select_answer(
            query="",
            target={"subject_id": "A", "predicate": "喜欢"},
            context={"correlation_id": corr},
            budget={"max_retrieval_rounds": 1, "correlation_id": corr},
        )
        self.assertEqual(second.get("status"), "unknown", second)
        self.assertIn("max_retrieval_rounds_exceeded", second.get("reason_codes") or [])
        self.assertTrue((second.get("budget_usage") or {}).get("degraded"))
        self.assertEqual((second.get("extras") or {}).get("action"), "reject_or_degrade")

        # 外部调用限额：入口拒绝
        gate_ok = self.ledger.check_and_enter(
            correlation_id="corr-ext", call_kind="external", attempt=1
        )
        self.assertTrue(gate_ok["allowed"])
        gate_ok2 = self.ledger.check_and_enter(
            correlation_id="corr-ext", call_kind="external", attempt=1
        )
        self.assertTrue(gate_ok2["allowed"])
        gate_deny = self.ledger.check_and_enter(
            correlation_id="corr-ext", call_kind="external", attempt=1
        )
        self.assertFalse(gate_deny["allowed"])
        self.assertIn("max_external_calls_exceeded", gate_deny["reason_codes"])
        self.assertEqual(gate_deny["action"], "reject_or_degrade")


if __name__ == "__main__":
    unittest.main()
