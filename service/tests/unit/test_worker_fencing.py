# -*- coding: utf-8 -*-
"""E11：worker fencing 单元覆盖（配合 AC40）。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition import consolidate as consolidate_mod
from cognitive_memory.cognition.consolidate import enable_background_consolidation
from cognitive_memory.domain.models import ObservationIn, SourceKind, TaskCreateIn
from cognitive_memory.events.bus import EventBus
from cognitive_memory.jobs.dispatcher import BackgroundRuntime
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

    def test_stale_worker_cannot_write_memory_after_takeover(self) -> None:
        """F11/C02: takeover inside the real consolidation write window."""
        obs = self.ctx.service.capture_observation(
            ObservationIn(
                source_kind=SourceKind.SYSTEM,
                content="takeover window: consolidated candidate must not land",
                producer="test",
            )
        )
        task = self.ctx.service.create_task(
            TaskCreateIn(
                task_type="memory.consolidation",
                payload={"observation_id": obs.id},
            )
        )
        rt = BackgroundRuntime(
            self.ctx.db, EventBus(), self.ctx.service, poll_interval_ms=50
        )
        original = self.ctx.service.propose_memory

        def takeover(body, **kwargs):
            # A second worker takes the lease exactly before the real insert.
            self.leases.acquire(worker_id="w2")
            return original(body, **kwargs)

        self.ctx.service.propose_memory = takeover
        rt.run_tasks_once()
        rt.stop()

        evidence = self.ctx.db.connect().execute(
            "SELECT COUNT(*) AS n FROM memory_evidence WHERE observation_id=?",
            (obs.id,),
        ).fetchone()["n"]
        self.assertEqual(evidence, 0, "stale worker left a real Memory behind")
        row = self.ctx.db.connect().execute(
            "SELECT status FROM tasks WHERE id=?", (task.id,)
        ).fetchone()
        self.assertIn(row["status"], {"pending", "dead"})

    def test_background_consolidation_gate(self) -> None:
        consolidate_mod.BACKGROUND_CONSOLIDATION_ENABLED = False
        self.assertFalse(consolidate_mod.BACKGROUND_CONSOLIDATION_ENABLED)
        self.assertFalse(enable_background_consolidation(confirm_e11_protections=False))
        self.assertTrue(enable_background_consolidation(confirm_e11_protections=True))
        self.assertTrue(consolidate_mod.BACKGROUND_CONSOLIDATION_ENABLED)


if __name__ == "__main__":
    unittest.main()
