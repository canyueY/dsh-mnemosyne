# -*- coding: utf-8 -*-
"""E12：执行回执 REST/SDK 契约。"""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from cognitive_memory.bootstrap import build_context
from cognitive_memory.sdk.client import MnemosyneClient


class ExecutionOutcomeE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e12-e2e")
        self.http = TestClient(self.ctx.app)
        self.client = MnemosyneClient("http://test", "e12-e2e", session=self.http)

    def tearDown(self) -> None:
        self.client.close()
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_fake_executor_protocol_via_api(self) -> None:
        planned = self.client.plan_execution(
            {
                "actor": "agent:e2e",
                "bank_id": "personal",
                "capability": "config.write",
                "payload": {"config": {"x": 1}},
                "expected_state_version": 1,
            }
        )
        self.assertTrue(planned.get("ok"), planned)
        pid = planned["proposal_id"]
        saved = self.client.execution_save(pid)
        self.assertTrue(saved["distinctions"]["saved"])
        self.assertFalse(saved["distinctions"]["execution_success"])
        adopted = self.client.execution_adopt(pid)
        self.assertTrue(adopted["distinctions"]["adopted"])
        confirmed = self.client.execution_confirm(pid)
        self.assertEqual(confirmed["phase"], "execution_confirmed")
        unknown = self.client.execution_observe(pid, {"outcome": "unknown"})
        self.assertEqual(unknown["outcome"]["outcome"], "unknown")
        self.assertFalse(unknown["outcome"]["task_success"])


if __name__ == "__main__":
    unittest.main()
