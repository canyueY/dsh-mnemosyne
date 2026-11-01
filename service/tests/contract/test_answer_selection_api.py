# -*- coding: utf-8 -*-
"""E03：AnswerSelection REST / SDK 真实入口。"""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.sdk.client import MnemosyneClient


class AnswerSelectionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e03-api")
        self.http = TestClient(self.ctx.app)
        self.h = {"Authorization": "Bearer e03-api"}
        self.claims = ClaimStore(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_select_endpoint_and_sdk(self) -> None:
        self.claims.propose_from_text("A喜欢咖啡")
        r = self.http.post(
            "/api/v1/cognition/answers/select",
            headers=self.h,
            json={
                "bank_id": "personal",
                "target": {"subject_id": "A", "predicate": "喜欢"},
                "mode": "current",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn(body.get("status"), {"chosen", "partial", "unknown"})
        self.assertFalse(body.get("wrote_verified"))
        self.assertIn("decision_id", body)

        client = MnemosyneClient(
            base_url="http://test",
            token="e03-api",
            session=self.http,
        )
        via_sdk = client.select_answer(
            {
                "bank_id": "personal",
                "target": {"subject_id": "A", "predicate": "喜欢"},
                "mode": "current",
            }
        )
        self.assertEqual(via_sdk.get("status"), body.get("status"))

    def test_openapi_lists_select_path(self) -> None:
        from pathlib import Path

        text = (Path(__file__).resolve().parents[2] / "schemas" / "openapi.v1.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("/cognition/answers/select:", text)

    def test_unsupported_query_via_http(self) -> None:
        r = self.http.post(
            "/api/v1/cognition/answers/select",
            headers=self.h,
            json={"query": "%%%%完全无法解析的符号堆%%%%", "mode": "current"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertTrue(
            "unsupported_query" in body["reason_codes"] or "needs_target" in body["reason_codes"]
        )


if __name__ == "__main__":
    unittest.main()
