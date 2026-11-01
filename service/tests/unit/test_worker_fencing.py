# -*- coding: utf-8 -*-
"""E11：worker fencing 单元覆盖（配合 AC40）。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition import consolidate as consolidate_mod
from cognitive_memory.cognition.consolidate import enable_background_consolidation
from cognitive_memory.strategy.fencing import FencingError, WorkerLeaseManager


class WorkerFencingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e11-fence")
        self.leases = WorkerLeaseManager(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_stale_generation_rejected_at_write_layer(self) -> None:
        first = self.leases.acquire(worker_id="w1")
        second = self.leases.acquire(worker_id="w2")
        with self.assertRaises(FencingError):
            self.leases.commit_artifact(
                fencing_token=first["fencing_token"],
                expected_generation=first["generation"],
                artifact={"x": 1},
            )
        ok = self.leases.commit_artifact(
            fencing_token=second["fencing_token"],
            expected_generation=second["generation"],
            artifact={"x": 2},
        )
        self.assertTrue(ok["ok"])

    def test_background_consolidation_gate(self) -> None:
        consolidate_mod.BACKGROUND_CONSOLIDATION_ENABLED = False
        self.assertFalse(consolidate_mod.BACKGROUND_CONSOLIDATION_ENABLED)
        self.assertFalse(enable_background_consolidation(confirm_e11_protections=False))
        self.assertTrue(enable_background_consolidation(confirm_e11_protections=True))
        self.assertTrue(consolidate_mod.BACKGROUND_CONSOLIDATION_ENABLED)


if __name__ == "__main__":
    unittest.main()
