# -*- coding: utf-8 -*-
"""E13 AC44–AC46：归因反馈与有限策略学习。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition.attributed_feedback import (
    LimitedStrategyLearner,
    map_execution_to_task_feedback,
)


class AttributedFeedbackLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e13-token")
        self.learner = LimitedStrategyLearner(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac44(self) -> None:
        """满意、自评、重复 task_success → utility 可记，verification 不变，归因 unresolved。"""
        for kwargs in (
            {"satisfaction": True, "outcome": "success"},
            {"self_eval_passed": True, "outcome": "success"},
            {"outcome": "success", "repeated_task_success": True},
        ):
            pack = map_execution_to_task_feedback(**kwargs)
            self.assertTrue(pack["utility"]["recorded"])
            self.assertFalse(pack["verification"]["changed"])
            self.assertFalse(pack["verification"]["promote_eligible"])
            self.assertEqual(pack["attribution"], "unresolved")
            self.assertTrue(pack["boundary"]["satisfaction_ne_verified"])

    def test_ac45(self) -> None:
        """训练与评估根来源重叠 → 拒绝污染采用；合成收益不宣称真实效果。"""
        refused = self.learner.shadow_evaluate(
            strategy_id="det-policy-1",
            train_root_sources=["src-a", "src-b"],
            eval_root_sources=["src-b", "src-c"],
            synthetic_gain=0.42,
            suite_passed=True,
        )
        self.assertFalse(refused.get("ok"))
        self.assertEqual(refused.get("code"), "train_eval_source_overlap")
        self.assertFalse(refused.get("adopted"))
        self.assertTrue(refused["report"]["contamination"]["detected"])
        self.assertEqual(refused["report"]["user_effect"], "not_measured")
        self.assertFalse(refused["report"]["heldout_claimed"])
        self.assertTrue(refused["report"]["synthetic_gain_not_real_effect"])

        clean = self.learner.shadow_evaluate(
            strategy_id="det-policy-1",
            train_root_sources=["src-a"],
            eval_root_sources=["src-z"],
            synthetic_gain=0.42,
            suite_passed=True,
            expected_version=1,
        )
        self.assertTrue(clean.get("ok"))
        self.assertFalse(clean.get("adopted"))
        self.assertEqual(clean["report"]["user_effect"], "not_measured")

    def test_ac46(self) -> None:
        """候选测试通过后版本变化 → 采用/回滚仍 CAS，无半采用。"""
        clean = self.learner.shadow_evaluate(
            strategy_id="det-policy-2",
            train_root_sources=["t1"],
            eval_root_sources=["e1"],
            suite_passed=True,
            expected_version=1,
            authorized=True,
        )
        cid = clean["candidate_id"]
        # 版本已变
        denied = self.learner.adopt(
            cid, expected_version=1, current_version=2, authorized=True
        )
        self.assertFalse(denied.get("ok"))
        self.assertEqual(denied.get("code"), "cas_version_mismatch")
        self.assertFalse(denied.get("partial_adopt"))

        ok = self.learner.adopt(cid, expected_version=1, current_version=1, authorized=True)
        self.assertTrue(ok.get("adopted"))
        self.assertFalse(ok.get("partial_adopt"))

        # 回滚时版本又变 → 拒绝
        rb_denied = self.learner.rollback(
            cid, expected_version=1, current_version=3, authorized=True
        )
        self.assertFalse(rb_denied.get("ok"))
        self.assertEqual(rb_denied.get("code"), "cas_version_mismatch")
        self.assertFalse(rb_denied.get("partial_adopt"))

        rb = self.learner.rollback(cid, expected_version=1, current_version=1, authorized=True)
        self.assertTrue(rb.get("rolled_back"))
        self.assertFalse(rb.get("partial_adopt"))


if __name__ == "__main__":
    unittest.main()
