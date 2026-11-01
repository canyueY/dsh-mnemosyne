# -*- coding: utf-8 -*-
"""E12 AC41–AC43：执行与效果回执。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition.execution_receipts import ExecutionReceiptStore, FakeExecutor


class ExecutionReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e12-token")
        self.store = ExecutionReceiptStore(self.ctx.db)
        self.fake = FakeExecutor(self.store)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac41(self) -> None:
        """配置保存但 runtime 未采用 → saved 与 adopted 分开，不能 execution_success。"""
        preview = self.fake.preview(
            actor="agent:x",
            bank_id="personal",
            capability="config.write",
            payload={"config": {"theme": "dark"}},
        )
        pid = preview["action"]["proposal_id"]
        saved = self.fake.apply(pid)
        self.assertTrue(saved["distinctions"]["saved"])
        self.assertFalse(saved["distinctions"]["adopted"])
        self.assertFalse(saved["distinctions"]["execution_success"])
        self.assertFalse(saved.get("runtime_adopted"))

        adopted = self.fake.adopt(pid)
        self.assertTrue(adopted["distinctions"]["saved"])
        self.assertTrue(adopted["distinctions"]["adopted"])
        # 仍无执行确认+成功效果 → 还不是 execution_success
        self.assertFalse(adopted["distinctions"]["execution_success"])

    def test_ac42(self) -> None:
        """执行完成无效果观察 → outcome=unknown，不永久改偏好或 verified。"""
        preview = self.fake.preview(
            actor="agent:x",
            bank_id="personal",
            capability="tool.run",
            payload={"op": "ping"},
        )
        pid = preview["action"]["proposal_id"]
        self.fake.apply(pid)
        self.fake.adopt(pid)
        self.fake.verify_execution(pid)
        observed = self.fake.observe(pid, "unknown")
        oc = observed["outcome"]
        self.assertEqual(oc.get("outcome"), "unknown")
        self.assertFalse(oc.get("task_success"))
        self.assertFalse(oc.get("preference_mutated"))
        self.assertFalse(oc.get("verified_mutated"))

    def test_ac43(self) -> None:
        """后续授权操作已改状态 → 旧计划 apply/rollback 因版本拒绝，保留新状态。"""
        p1 = self.fake.preview(
            actor="agent:a",
            bank_id="personal",
            capability="config.write",
            payload={"config": {"v": 1}},
        )
        old_pid = p1["action"]["proposal_id"]
        # 新操作推进版本
        p2 = self.fake.preview(
            actor="agent:b",
            bank_id="personal",
            capability="config.write",
            payload={"config": {"v": 2}},
        )
        new_pid = p2["action"]["proposal_id"]
        applied_new = self.fake.apply(new_pid)
        self.assertTrue(applied_new.get("ok") is not False or applied_new.get("distinctions", {}).get("saved"))
        self.assertTrue(applied_new["distinctions"]["saved"])
        current_version = self.fake.state_version

        # 旧计划按旧 expected 版本 apply → 拒绝
        refused = self.store.apply(
            old_pid,
            current_state_version=current_version,
        )
        self.assertFalse(refused.get("ok"))
        self.assertEqual(refused.get("code"), "version_precondition_failed")

        # 旧 rollback 也拒绝，保留新合法状态
        rb = self.fake.rollback(old_pid, expected_version=1)
        self.assertFalse(rb.get("ok"))
        self.assertEqual(rb.get("code"), "version_precondition_failed")
        self.assertTrue(rb.get("preserved"))
        # 新配置仍在
        self.assertEqual(self.fake._state["cfg"].get("v"), 2)


if __name__ == "__main__":
    unittest.main()
