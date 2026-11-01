# -*- coding: utf-8 -*-
"""E10 AC35–AC37：会话巩固与用途视图。"""

from __future__ import annotations

import copy
import unittest

from cognitive_memory.cognition.consolidate import (
    BACKGROUND_CONSOLIDATION_ENABLED,
    SessionConsolidationJob,
    build_session_utility_view,
    consolidate_session,
)


def _atoms_ac35() -> list[dict]:
    atoms = []
    for i in range(32):
        atoms.append(
            {
                "id": f"p{i}",
                "result": "passed",
                "content": f"步骤{i}通过",
                "source_ids": [f"src-p{i}"],
            }
        )
    atoms.append(
        {
            "id": "f1",
            "result": "failed",
            "content": "步骤失败：不接受自动支付",
            "negations": ["不"],
            "source_ids": ["src-f1"],
        }
    )
    atoms.append({"id": "sk1", "result": "skipped", "content": "跳过A", "source_ids": ["src-sk1"]})
    atoms.append({"id": "sk2", "result": "skipped", "content": "跳过B", "source_ids": ["src-sk2"]})
    return atoms


class SessionConsolidationTests(unittest.TestCase):
    def test_ac35(self) -> None:
        """32 passed, 1 failed, 2 skipped 含否定 → 计数/否定/来源保留，不改成全通过。"""
        atoms = _atoms_ac35()
        view = build_session_utility_view(atoms)
        counts = view["summary"]["counts"]
        self.assertEqual(counts["passed"], 32)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["skipped"], 2)
        self.assertFalse(view["summary"]["forged_all_pass"])
        self.assertIn("不得表述为全部通过", view["summary"]["text"])
        self.assertTrue(view["negations"])
        self.assertTrue(any(n.get("marker") == "不" for n in view["negations"]))
        anchors = {a["source_id"] for a in view["source_anchors"]}
        self.assertIn("src-f1", anchors)
        out = consolidate_session(session_id="s35", atoms=atoms, topic="支付")
        self.assertTrue(out["published"])
        derived = out["derived"]
        self.assertEqual(derived["utility_view"]["summary"]["counts"]["failed"], 1)
        self.assertTrue(derived["not_new_evidence"])

    def test_ac36(self) -> None:
        """会话只读了部分记录 → 仅用实际允许创建/读取/交付及必要依赖。"""
        atoms = [
            {"id": "in1", "result": "passed", "content": "已读A", "source_ids": ["s1"]},
            {"id": "in2", "result": "passed", "content": "已读B", "source_ids": ["s2"]},
            {"id": "out", "result": "passed", "content": "未授权窥探", "source_ids": ["s3"]},
        ]
        allowed = {"in1", "in2"}
        out = consolidate_session(
            session_id="s36",
            atoms=atoms,
            allowed_input_ids=allowed,
            topic="范围",
        )
        self.assertTrue(out["published"])
        selected = set(out["derived"]["utility_view"]["selected_atom_ids"])
        self.assertEqual(selected, allowed)
        self.assertIn("out", out["out_of_scope"])
        self.assertNotIn("out", selected)
        deps = out["derived"]["dependencies"]
        self.assertEqual(set(deps["atom_ids"]), allowed)
        self.assertIn("out", deps["out_of_scope_excluded"])

    def test_ac37(self) -> None:
        """后台遇前台 → 可中断，不发布半成品，不修改原始记录。"""
        self.assertFalse(BACKGROUND_CONSOLIDATION_ENABLED)
        atoms = _atoms_ac35()
        originals = copy.deepcopy(atoms)
        job = SessionConsolidationJob(session_id="s37", atoms=atoms, topic="抢占")
        paused = job.run(foreground_preempt=True)
        self.assertEqual(paused["status"], "paused")
        self.assertFalse(paused["published"])
        self.assertEqual(paused["reason"], "preempted_by_foreground")
        self.assertIsNone(paused.get("derived"))
        self.assertEqual(atoms, originals)

        job.request_cancel()
        cancelled = job.run()
        self.assertIn(cancelled["status"], {"cancelled", "paused"})
        self.assertFalse(cancelled["published"])

        # 恢复后可完整发布，且仍不改原始
        job2 = SessionConsolidationJob(session_id="s37b", atoms=atoms, topic="完成")
        done = job2.run(foreground_preempt=False)
        self.assertTrue(done["published"])
        self.assertTrue(done["originals_unchanged"])
        self.assertEqual(atoms, originals)
        self.assertFalse(done["background_consolidation_enabled"])


if __name__ == "__main__":
    unittest.main()
