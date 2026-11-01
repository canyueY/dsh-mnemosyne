# -*- coding: utf-8 -*-
"""E05 AC20–AC21：DSH 薄适配器真实调用答案选择 + 故障降级。"""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from cognitive_memory.adapters.dsh_http import DshHttpAdapter
from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.domain.models import MemoryProposeIn
from cognitive_memory.sdk import MnemosyneClient
from cognitive_memory.sdk.client import MnemosyneClientError


class AnswerSelectionAdapterE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e05-token")
        self.http = TestClient(self.ctx.app)
        self.client = MnemosyneClient("http://test", "e05-token", session=self.http)
        self.adapter = DshHttpAdapter(self.client)
        self.claims = ClaimStore(self.ctx.db)

    def tearDown(self) -> None:
        self.client.close()
        self.ctx.db.close()
        self.tmp.cleanup()

    def _set_validity(self, claim_id: str, *, vf: str | None, vt: str | None) -> None:
        with self.ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE claims SET validity_from=?, validity_to=? WHERE id=?",
                (vf, vt, claim_id),
            )

    def test_ac20(self) -> None:
        """Top1 搜索过时项存在时，选择服务交付当前项与适用范围。"""
        source = self.ctx.service.propose_memory(
            MemoryProposeIn(content="adapter e2e fixture source")
        )
        old = self.claims.propose_from_text("A喜欢咖啡", source_memory_id=source.id)
        new = self.claims.propose_from_text("A喜欢茶", source_memory_id=source.id)
        self._set_validity(old["claim_id"], vf="2026-01-01T00:00:00Z", vt="2026-06-01T00:00:00Z")
        self._set_validity(new["claim_id"], vf="2026-06-01T00:00:00Z", vt=None)

        # 记忆检索可能仍命中旧文本；选择服务按有效区间取当前
        search = self.adapter.search("A喜欢")
        self.assertGreaterEqual(search.get("count", 0), 0)

        delivery = self.adapter.select_answer(
            {
                "bank_id": "personal",
                "target": {"subject_id": "A", "predicate": "喜欢"},
                "mode": "current",
                "at": "2026-09-01T00:00:00Z",
            }
        )
        self.assertEqual(delivery.get("availability"), "available", delivery)
        self.assertEqual(delivery.get("status"), "chosen", delivery)
        self.assertTrue(delivery.get("selected"), delivery)
        self.assertEqual(delivery["selected"][0]["claim_id"], new["claim_id"])
        self.assertIn("applicability", delivery)
        self.assertTrue(delivery.get("evidence_refs") or delivery["selected"][0].get("ref"))
        self.assertFalse(delivery.get("wrote_verified"))
        # 不得把旧 Top1 当权威
        self.assertNotEqual(delivery["selected"][0]["claim_id"], old["claim_id"])

    def test_ac21(self) -> None:
        """Node 503/timeout → unavailable，不当作无反证或退回确定性旧事实。"""
        with mock.patch.object(
            self.client,
            "select_answer",
            side_effect=MnemosyneClientError("HTTP 503", status_code=503, body={"detail": "down"}),
        ):
            delivery = self.adapter.select_answer(
                {"query": "A喜欢什么", "target": {"subject_id": "A", "predicate": "喜欢"}}
            )
        self.assertEqual(delivery.get("availability"), "unavailable")
        self.assertEqual(delivery.get("status"), "unavailable")
        self.assertEqual(delivery.get("selected"), [])
        self.assertIn("node_unavailable", delivery.get("reason_codes") or [])
        boundary = delivery.get("boundary") or {}
        self.assertTrue(boundary.get("not_top1_fact"))
        self.assertTrue(boundary.get("not_evidence_absence"))
        self.assertFalse(delivery.get("wrote_verified"))


if __name__ == "__main__":
    unittest.main()
