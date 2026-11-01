# -*- coding: utf-8 -*-
"""E11 AC38–AC40：消费水位与 fencing（崩溃重放 / 独立水位 / 旧租约）。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.strategy.fencing import FencingError, WorkerLeaseManager
from cognitive_memory.strategy.watermarks import ConsumerWatermarkStore


class ConsumerWatermarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e11-token")
        self.wm = ConsumerWatermarkStore(self.ctx.db)
        self.leases = WorkerLeaseManager(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac38(self) -> None:
        """效果写后水位前异常 → 整体回滚；重放等价一次。"""
        with self.assertRaises(RuntimeError):
            self.wm.apply_effect_with_watermark(
                consumer="utility",
                event_id="evt-1",
                effect_payload={"delta": 1},
                next_position=1,
                crash_after_effect=True,
            )
        self.assertEqual(self.wm.get_watermark("utility"), 0)
        self.assertEqual(self.wm.effect_count("utility"), 0)

        first = self.wm.apply_effect_with_watermark(
            consumer="utility",
            event_id="evt-1",
            effect_payload={"delta": 1},
            next_position=1,
        )
        self.assertFalse(first.get("deduped"))
        self.assertEqual(self.wm.get_watermark("utility"), 1)
        self.assertEqual(self.wm.effect_count("utility"), 1)

        replay = self.wm.apply_effect_with_watermark(
            consumer="utility",
            event_id="evt-1",
            effect_payload={"delta": 1},
            next_position=1,
        )
        self.assertTrue(replay.get("deduped"))
        self.assertEqual(self.wm.effect_count("utility"), 1)
        self.assertEqual(self.wm.get_watermark("utility"), 1)

    def test_ac39(self) -> None:
        """utility 到 N，co_retrieval 到 M → 各用各水位，互不跳过/重复强化。"""
        self.wm.apply_effect_with_watermark(
            consumer="utility",
            event_id="u-1",
            effect_payload={"u": 1},
            next_position=5,
        )
        self.wm.apply_effect_with_watermark(
            consumer="co_retrieval",
            event_id="c-1",
            effect_payload={"c": 1},
            next_position=2,
        )
        self.assertEqual(self.wm.get_watermark("utility"), 5)
        self.assertEqual(self.wm.get_watermark("co_retrieval"), 2)

        # 同 event 不同 consumer 互不影响
        self.wm.apply_effect_with_watermark(
            consumer="utility",
            event_id="shared-evt",
            effect_payload={"u": 2},
            next_position=6,
        )
        self.wm.apply_effect_with_watermark(
            consumer="co_retrieval",
            event_id="shared-evt",
            effect_payload={"c": 2},
            next_position=3,
        )
        self.assertEqual(self.wm.get_watermark("utility"), 6)
        self.assertEqual(self.wm.get_watermark("co_retrieval"), 3)
        self.assertEqual(self.wm.effect_count("utility"), 2)
        self.assertEqual(self.wm.effect_count("co_retrieval"), 2)

    def test_ac40(self) -> None:
        """旧 worker 暂停后新 worker 取租约 → 旧处理器真实产物写层 fencing 拒绝。"""
        old = self.leases.acquire(worker_id="old")
        new = self.leases.acquire(worker_id="new")
        self.assertNotEqual(old["fencing_token"], new["fencing_token"])
        self.assertGreater(new["generation"], old["generation"])

        # 新租约可写
        ok = self.leases.commit_artifact(
            fencing_token=new["fencing_token"],
            expected_generation=new["generation"],
            artifact={"summary": "fresh"},
        )
        self.assertTrue(ok.get("fenced"))

        # 旧租约在写层被拒（不是只靠最终 task UPDATE）
        with self.assertRaises(FencingError) as ctx:
            self.leases.commit_artifact(
                fencing_token=old["fencing_token"],
                expected_generation=old["generation"],
                artifact={"summary": "stale"},
            )
        self.assertIn(ctx.exception.code, {"fencing_token_mismatch", "generation_mismatch"})

        # 水位效果写同样受 fencing
        with self.assertRaises(FencingError):
            self.wm.apply_effect_with_watermark(
                consumer="utility",
                event_id="stale-write",
                effect_payload={"x": 1},
                next_position=10,
                fencing_token=old["fencing_token"],
                expected_generation=old["generation"],
            )


if __name__ == "__main__":
    unittest.main()
