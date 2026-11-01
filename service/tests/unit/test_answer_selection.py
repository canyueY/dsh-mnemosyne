# -*- coding: utf-8 -*-
"""E03 AC10–AC16：AnswerSelection 有限选择。"""

from __future__ import annotations

import json
import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.cognition.answer_selection import AnswerSelectionService


class AnswerSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e03-token")
        self.claims = ClaimStore(self.ctx.db)
        self.sel = AnswerSelectionService(self.ctx.service)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def _set_validity(self, claim_id: str, *, vf: str | None, vt: str | None) -> None:
        with self.ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE claims SET validity_from=?, validity_to=? WHERE id=?",
                (vf, vt, claim_id),
            )

    def _set_scope(self, claim_id: str, scope: dict) -> None:
        pack = self.claims.get(claim_id, follow_redirect=False)
        ast = dict(pack["ast"])
        merged_ast = {**(ast.get("scope") or {}), **scope}
        ast["scope"] = merged_ast
        row_scope = {**(pack.get("scope") or {}), **scope}
        with self.ctx.db.transaction() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()}
            if "scope_json" in cols:
                conn.execute(
                    "UPDATE claims SET ast_json=?, scope_json=? WHERE id=?",
                    (
                        json.dumps(ast, ensure_ascii=False),
                        json.dumps(row_scope, ensure_ascii=False),
                        claim_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE claims SET ast_json=? WHERE id=?",
                    (json.dumps(ast, ensure_ascii=False), claim_id),
                )

    def test_ac10(self) -> None:
        """A 到 t2 失效，B 从 t2 生效 → t1 选 A，t3 选 B。"""
        a = self.claims.propose_from_text("A喜欢咖啡")
        b = self.claims.propose_from_text("A喜欢茶")
        self._set_validity(a["claim_id"], vf="2026-01-01T00:00:00Z", vt="2026-06-01T00:00:00Z")
        self._set_validity(b["claim_id"], vf="2026-06-01T00:00:00Z", vt=None)

        t1 = self.sel.select_answer(
            query="",
            target={"subject_id": "A", "predicate": "喜欢"},
            at="2026-03-01T00:00:00Z",
            mode="history",
        )
        self.assertEqual(t1["status"], "chosen", t1)
        self.assertEqual(t1["selected"][0]["claim_id"], a["claim_id"])
        self.assertEqual(t1["selected"][0]["validity_to"], "2026-06-01T00:00:00Z")

        t3 = self.sel.select_answer(
            query="",
            target={"subject_id": "A", "predicate": "喜欢"},
            at="2026-09-01T00:00:00Z",
            mode="current",
        )
        self.assertEqual(t3["status"], "chosen", t3)
        self.assertEqual(t3["selected"][0]["claim_id"], b["claim_id"])
        self.assertEqual(t3["selected"][0]["validity_from"], "2026-06-01T00:00:00Z")

    def test_ac11(self) -> None:
        """较旧事件晚入库 → 不按接收时间覆盖新状态。"""
        newer = self.claims.propose_from_text("A喜欢茶")
        self._set_validity(newer["claim_id"], vf="2026-06-01T00:00:00Z", vt=None)
        # 迟到的旧事实
        older = self.claims.propose_from_text("A喜欢咖啡")
        self._set_validity(older["claim_id"], vf="2026-01-01T00:00:00Z", vt="2026-06-01T00:00:00Z")

        cur = self.sel.select_answer(
            target={"subject_id": "A", "predicate": "喜欢"},
            at="2026-07-01T00:00:00Z",
            mode="current",
        )
        self.assertEqual(cur["status"], "chosen", cur)
        self.assertEqual(cur["selected"][0]["claim_id"], newer["claim_id"])

    def test_ac12(self) -> None:
        """A 否定与作者 B 观点不合并。"""
        a = self.claims.propose_from_text("A不喜欢咖啡")
        b = self.claims.propose_from_text("作者B喜欢咖啡")
        out = self.sel.select_answer(
            target={"subject_id": "A", "predicate": "喜欢"},
            mode="current",
        )
        self.assertEqual(out["status"], "chosen", out)
        self.assertEqual(out["selected"][0]["claim_id"], a["claim_id"])
        self.assertTrue(out["selected"][0]["negated"])
        alt_ids = {x["claim_id"] for x in out["alternatives"]}
        self.assertNotIn(b["claim_id"], alt_ids)
        self.assertNotIn(b["claim_id"], {out["selected"][0]["claim_id"]})

    def test_ac13(self) -> None:
        """仅晚上声明 → 问白天永久则未知。"""
        c = self.claims.propose_from_text("我晚上不喜欢咖啡")
        out = self.sel.select_answer(
            target={"subject_id": "我晚上", "predicate": "喜欢"},
            context={"query_scope": "daytime_permanent"},
            mode="current",
        )
        self.assertEqual(out["status"], "unknown", out)
        self.assertIn("context_mismatch", out["reason_codes"])
        self.assertEqual(out["selected"], [])
        self.assertTrue(any(x["claim_id"] == c["claim_id"] for x in out["alternatives"]))

    def test_ac14(self) -> None:
        """预算耗尽且反证未检查完 → unknown/partial + budget_exhausted。"""
        self.claims.propose_from_text("A喜欢咖啡")
        for i in range(6):
            self.claims.propose_from_text(f"A不喜欢咖啡变体{i}")
        out = self.sel.select_answer(
            target={"subject_id": "A", "predicate": "喜欢", "negated": False},
            budget={"max_candidates": 1, "max_steps": 3},
            mode="current",
        )
        self.assertIn(out["status"], {"unknown", "partial"}, out)
        self.assertIn("budget_exhausted", out["reason_codes"])
        self.assertFalse(out["checks_complete"])

    def test_ac15(self) -> None:
        """自述偏好可引用，但不写客观 verified。"""
        c = self.claims.propose_from_text("我喜欢夜航星")
        self._set_scope(c["claim_id"], {"self_report": True})
        out = self.sel.select_answer(
            target={"subject_id": "我", "predicate": "喜欢"},
            mode="current",
        )
        self.assertEqual(out["status"], "chosen", out)
        self.assertEqual(out["selected"][0]["epistemic_tag"], "self_report")
        self.assertIn("self_report", out["reason_codes"])
        self.assertFalse(out.get("wrote_verified"))
        pack = self.claims.get(c["claim_id"], follow_redirect=False)
        self.assertNotEqual(pack.get("lifecycle"), "verified")

    def test_ac16(self) -> None:
        """无法解析目标 → unsupported_query/needs_target。"""
        out = self.sel.select_answer(query="请随便讲讲宇宙和命运的关系吧？？", mode="current")
        self.assertEqual(out["status"], "unknown", out)
        self.assertTrue(
            "unsupported_query" in out["reason_codes"] or "needs_target" in out["reason_codes"]
        )
        self.assertEqual(out["selected"], [])


if __name__ == "__main__":
    unittest.main()
