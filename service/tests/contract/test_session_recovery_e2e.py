# -*- coding: utf-8 -*-
"""E06：会话 checkpoint REST/SDK 恢复契约（真实入口）。"""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from cognitive_memory.bootstrap import build_context
from cognitive_memory.identity import issue_grant, register_agent
from cognitive_memory.sdk.client import MnemosyneClient


class SessionRecoveryE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e06-e2e")
        self.http = TestClient(self.ctx.app)
        self.h = {"Authorization": "Bearer e06-e2e"}
        self.client = MnemosyneClient("http://test", "e06-e2e", session=self.http)

    def tearDown(self) -> None:
        self.client.close()
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_message_idempotency_and_resume_api(self) -> None:
        m1 = self.client.record_session_message(
            {
                "session_id": "s1",
                "bank_id": "personal",
                "role": "user",
                "content": "继续任务",
                "message_id": "dsh:s1:9",
            }
        )
        m2 = self.client.record_session_message(
            {
                "session_id": "s1",
                "bank_id": "personal",
                "role": "user",
                "content": "继续任务",
                "message_id": "dsh:s1:9",
            }
        )
        self.assertFalse(m1.get("deduped"))
        self.assertTrue(m2.get("deduped"))
        self.assertEqual(m2.get("exposure_count"), 1)

        saved = self.client.save_session_checkpoint(
            {
                "session_id": "s1",
                "bank_id": "personal",
                "task_id": "t1",
                "goal": "完成导出",
                "constraints": ["禁止覆盖导出文件"],
                "verified_progress": [
                    {"step": "准备目录", "source": "effect_confirmed"},
                    {"step": "我说好了", "source": "assistant_self_report"},
                ],
                "pending_external_operations": [
                    {
                        "op_id": "export-1",
                        "phase": "requested",
                        "timeout": True,
                        "effect_unknown": True,
                    }
                ],
                "evidence_revisions": [{"source_id": "ev1", "revision": "3"}],
            }
        )
        self.assertTrue(saved.get("ok"))
        cid = saved["checkpoint_id"]
        self.assertEqual(len(saved["manifest"]["verified_progress"]), 1)

        got = self.client.get_session_checkpoint(cid)
        self.assertEqual(got["checkpoint_id"], cid)
        self.assertTrue(got["recovery"]["reauthorized"])

        resumed = self.client.resume_session_checkpoint({"checkpoint_id": cid})
        self.assertEqual(resumed.get("status"), "reconcile_required")
        self.assertFalse(resumed.get("resumed"))
        self.assertIn("blind_retry_forbidden", resumed.get("reason_codes") or [])

    def test_cross_bank_checkpoint_denied(self) -> None:
        register_agent(self.ctx.db, agent_id="agent:bank-a")
        g = issue_grant(
            self.ctx.db,
            "agent:bank-a",
            permissions=["memory.read", "memory.write"],
            banks=["bank_a"],
        )
        agent_headers = {
            "X-Agent-Id": "agent:bank-a",
            "X-Agent-Token": g["agent_token"],
        }

        created = self.http.post(
            "/api/v1/cognition/sessions/checkpoints",
            headers=self.h,
            json={
                "session_id": "sx",
                "bank_id": "bank_b",
                "goal": "秘密",
                "constraints": ["不可外泄"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        cid = created.json()["checkpoint_id"]

        denied = self.http.get(
            f"/api/v1/cognition/sessions/checkpoints/{cid}",
            headers=agent_headers,
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        detail = denied.json().get("detail") or {}
        if isinstance(detail, dict):
            self.assertIn(detail.get("detail") or detail.get("code"), {"bank_denied", "session_isolation"})

        resume = self.http.post(
            "/api/v1/cognition/sessions/checkpoints/resume",
            headers=agent_headers,
            json={"checkpoint_id": cid},
        )
        self.assertEqual(resume.status_code, 403, resume.text)

    def test_openapi_lists_session_paths(self) -> None:
        from pathlib import Path

        text = (Path(__file__).resolve().parents[2] / "schemas" / "openapi.v1.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("/cognition/sessions/checkpoints:", text)
        self.assertIn("/cognition/sessions/checkpoints/resume:", text)
        self.assertIn("/cognition/sessions/messages:", text)


if __name__ == "__main__":
    unittest.main()
