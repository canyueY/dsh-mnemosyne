# -*- coding: utf-8 -*-
"""E08 AC29 / AC31：健康探针故障识别与 worker 未知。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus
from cognitive_memory.tracing.health_probes import CognitiveHealthProbes


class CognitiveHealthProbesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e08-token")
        self.probes = CognitiveHealthProbes(self.ctx.db, data_dir=self.tmp.name)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac29(self) -> None:
        """API 可连但索引旧 → 相关维度 WARN/ERROR，不全绿，不破坏用户数据。"""
        before = self.ctx.service.propose_memory(
            MemoryProposeIn(content="user-data-must-survive", status=MemoryStatus.ACTIVE)
        )
        mem_id = before.id

        # 制造陈旧索引
        self.probes.simulate_stale_index()
        report = self.probes.probe(include_write_probe=False)
        self.assertFalse(report["all_green"], report)
        fts = report["dimensions"]["fts_index"]
        self.assertIn(fts["status"], {"WARN", "ERROR"}, fts)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["user_data_modified"])

        # 用户数据仍在
        still = self.ctx.service.get_memory(mem_id)
        self.assertIsNotNone(still)
        self.assertEqual(still["content"], "user-data-must-survive")

        # 写探针只进隔离空间
        with_write = self.probes.probe(
            include_write_probe=True, write_probe_namespace="probe-ac29"
        )
        self.assertFalse(with_write["user_data_modified"])
        self.assertEqual(
            with_write["write_probe"]["evidence"].get("user_data_modified"), False
        )
        # 原记忆仍未改
        still2 = self.ctx.service.get_memory(mem_id)
        self.assertEqual(still2["content"], "user-data-must-survive")

        explained = self.probes.explain()
        self.assertIn("human_text", explained)
        self.assertIn("structured", explained)

    def test_ac31(self) -> None:
        """任务 pending 无 worker 回执 → PENDING/UNKNOWN，不把无报错当完成。"""
        with self.ctx.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks(id, task_type, status, payload_json, attempts, last_error, created_at, updated_at)
                VALUES (?, 'cognitive.meta', 'pending', '{}', 0, '', ?, ?)
                """,
                ("task_pending_e08", "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"),
            )
        report = self.probes.probe(include_write_probe=False)
        qw = report["dimensions"]["queue_worker"]
        self.assertEqual(qw["status"], "PENDING", qw)
        self.assertTrue(qw["evidence"].get("no_error_ne_complete"))
        self.assertIsNone(qw["evidence"].get("worker_receipt"))
        self.assertFalse(report["all_green"])
        # 无报错 ≠ 完成
        self.assertNotEqual(qw["status"], "OK")


if __name__ == "__main__":
    unittest.main()
