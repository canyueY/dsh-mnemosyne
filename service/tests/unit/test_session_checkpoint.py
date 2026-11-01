# -*- coding: utf-8 -*-
"""E06 AC22–AC25：会话账本与任务恢复。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition.session_checkpoint import SessionLedger


class SessionCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e06-token")
        self.ledger = SessionLedger(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac22(self) -> None:
        """同会话消息 hook 重放 → 稳定键去重，曝光不增加事实支持。"""
        first = self.ledger.record_message(
            session_id="sess-1",
            bank_id="personal",
            role="user",
            content="你好",
            message_id="dsh:sess-1:1",
        )
        replay = self.ledger.record_message(
            session_id="sess-1",
            bank_id="personal",
            role="user",
            content="你好",
            message_id="dsh:sess-1:1",
        )
        self.assertFalse(first.get("deduped"))
        self.assertTrue(replay.get("deduped"))
        self.assertEqual(first["stable_key"], replay["stable_key"])
        self.assertEqual(replay.get("exposure_count"), 1)
        self.assertIn("replay_does_not_increase_fact_support", replay.get("note", ""))

        d1 = self.ledger.record_delivery(
            session_id="sess-1", bank_id="personal", card_id="card-a"
        )
        d2 = self.ledger.record_delivery(
            session_id="sess-1", bank_id="personal", card_id="card-a"
        )
        self.assertFalse(d1.get("deduped"))
        self.assertTrue(d2.get("deduped"))
        self.assertIn("not_understanding", d2.get("note", ""))

    def test_ac23(self) -> None:
        """压缩/重启恢复：约束保留；助手自报完成不成为验证。"""
        saved = self.ledger.save_checkpoint(
            session_id="sess-2",
            bank_id="personal",
            task_id="task-restore",
            goal="订票",
            constraints=["禁止自动支付", "失败不超过3次"],
            verified_progress=[
                {"step": "查询航班", "source": "effect_confirmed"},
                {"step": "我说已经订好了", "source": "assistant_self_report"},
                {"step": "模型说完成", "source": "model_said_done"},
            ],
            pending_questions=["要不要改签？"],
            assistant_claims=[{"text": "已经订好了", "verified": False}],
            evidence_revisions=[{"source_id": "src-1", "revision": "r2"}],
        )
        self.assertTrue(saved.get("ok"))
        cid = saved["checkpoint_id"]
        pack = self.ledger.get_checkpoint(cid, principal_banks=["personal"])
        self.assertTrue(pack.get("ok"))
        manifest = pack["manifest"]
        self.assertEqual(manifest["constraints"], ["禁止自动支付", "失败不超过3次"])
        verified = manifest["verified_progress"]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["step"], "查询航班")
        self.assertTrue(pack["recovery"]["assistant_self_report_not_verified"])
        self.assertEqual(manifest["pending_questions"], ["要不要改签？"])

        resumed = self.ledger.resume_checkpoint(cid, principal_banks=["personal"])
        self.assertTrue(resumed.get("resumed"))
        self.assertEqual(resumed["constraints"], ["禁止自动支付", "失败不超过3次"])
        self.assertEqual(len(resumed["verified_progress"]), 1)

    def test_ac24(self) -> None:
        """外部执行超时状态不明 → reconcile_required，禁止盲重试。"""
        saved = self.ledger.save_checkpoint(
            session_id="sess-3",
            bank_id="personal",
            task_id="task-side-effect",
            goal="发邮件",
            pending_external_operations=[
                {
                    "op_id": "mail-1",
                    "phase": "requested",
                    "timeout": True,
                    "effect_unknown": True,
                    "action": "send_email",
                }
            ],
        )
        cid = saved["checkpoint_id"]
        ops = saved["manifest"]["pending_external_operations"]
        self.assertEqual(ops[0]["phase"], "reconcile_required")
        self.assertTrue(ops[0].get("blind_retry_forbidden"))

        resumed = self.ledger.resume_checkpoint(cid, principal_banks=["personal"])
        self.assertTrue(resumed.get("ok"))
        self.assertFalse(resumed.get("resumed"))
        self.assertEqual(resumed.get("status"), "reconcile_required")
        self.assertIn("reconcile_required", resumed.get("reason_codes") or [])
        self.assertIn("blind_retry_forbidden", resumed.get("reason_codes") or [])
        self.assertTrue(resumed.get("blocked_operations"))

    def test_ac25(self) -> None:
        """两 Bank 权限不同 → 禁止跨授权范围读取和继承承诺。"""
        saved = self.ledger.save_checkpoint(
            session_id="sess-bank-a",
            bank_id="bank_a",
            task_id="secret-task",
            goal="仅 A 可见",
            constraints=["A专属承诺"],
            verified_progress=[{"step": "A已核验", "source": "effect_confirmed"}],
        )
        cid = saved["checkpoint_id"]
        denied = self.ledger.get_checkpoint(cid, principal_banks=["bank_b"])
        self.assertFalse(denied.get("ok"))
        self.assertEqual(denied.get("detail"), "bank_denied")
        self.assertEqual(denied.get("code"), "session_isolation")

        resume_denied = self.ledger.resume_checkpoint(cid, principal_banks=["bank_b"])
        self.assertFalse(resume_denied.get("ok"))
        self.assertEqual(resume_denied.get("detail"), "bank_denied")

        allowed = self.ledger.get_checkpoint(cid, principal_banks=["bank_a"])
        self.assertTrue(allowed.get("ok"))
        self.assertEqual(allowed["manifest"]["constraints"], ["A专属承诺"])


if __name__ == "__main__":
    unittest.main()
