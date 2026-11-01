# -*- coding: utf-8 -*-
"""E04 AC17–AC19：完整上下文预算信封。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.domain.models import ContextBuildIn, MemoryProposeIn, MemoryStatus, MemoryType
from cognitive_memory.retrieval.context_budget import (
    build_context_envelope,
    estimate_tokens_chars,
)


class ContextEnvelopeBudgetTests(unittest.TestCase):
    def test_ac17(self) -> None:
        """全部固定分区计入，先预留再裁剪记忆。"""
        memories = [
            {"id": "m1", "content": "alpha " * 40, "tags": ["sourced", "expiry"]},
            {"id": "m2", "content": "beta " * 40, "tags": ["negated"]},
            {"id": "m3", "content": "gamma " * 40, "tags": ["sourced"]},
        ]
        env = build_context_envelope(
            token_budget=200,
            sections={
                "stable_prefix": "PREFIX_STABLE",
                "tools": "toolA toolB",
                "history": "user: hi\nassistant: hello",
                "header_tags": "source|negation|expiry",
                "output_reserve": "OUT",
            },
            memory_candidates=memories,
            config={"conservative_reserve": 20, "max_candidates": 10},
            has_accurate_tokenizer=False,
        )
        self.assertTrue(env["ok"])
        self.assertTrue(env["fits"])
        b = env["breakdown"]
        for key in ("stable_prefix", "tools", "history", "header_tags", "output_reserve", "memory"):
            self.assertIn(key, b)
        # 输出预留含 conservative_reserve
        self.assertGreaterEqual(b["output_reserve"]["tokens"], 20)
        fixed = env["fixed_overhead"]
        self.assertEqual(
            fixed,
            sum(b[k]["tokens"] for k in ("stable_prefix", "tools", "history", "header_tags", "output_reserve")),
        )
        # 记忆在固定开销之后才占用
        self.assertEqual(env["available_for_memory"], env["token_budget"] - fixed)
        self.assertLessEqual(env["memory_tokens"], env["available_for_memory"])
        # 标签保留
        for frag in env["memory_fragments"]:
            self.assertTrue(frag.get("tags"))

    def test_ac18(self) -> None:
        """固定开销已超限 → 不适配，fits 不为 true。"""
        env = build_context_envelope(
            token_budget=30,
            sections={
                "stable_prefix": "X" * 40,
                "tools": "Y" * 40,
                "history": "Z" * 40,
                "header_tags": "tags",
                "output_reserve": "OUT" * 10,
            },
            memory_candidates=[{"id": "m1", "content": "should not appear", "tags": ["sourced"]}],
            config={"conservative_reserve": 50},
        )
        self.assertIs(env["fits"], False)
        self.assertEqual(env["status"], "incompatible")
        self.assertIn("fixed_overhead_exceeds_budget", env["reason_codes"])
        self.assertIn("context_incompatible", env["reason_codes"])
        self.assertEqual(env["memory_fragments"], [])

        # 真实 build_context 入口
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            ctx = build_context(data_dir=tmp.name, token="e04")
            ctx.service.propose_memory(
                MemoryProposeIn(
                    type=MemoryType.SEMANTIC,
                    status=MemoryStatus.ACTIVE,
                    content="memory that must not fake-fit",
                )
            )
            out = ctx.service.build_context(
                ContextBuildIn(
                    query="memory",
                    token_budget=40,
                    envelope={
                        "sections": {
                            "stable_prefix": "P" * 30,
                            "tools": "T" * 30,
                            "history": "H" * 30,
                            "header_tags": "source|negation|expiry",
                            "output_reserve": "O" * 20,
                        },
                        "budget_config": {"conservative_reserve": 40},
                    },
                )
            )
            self.assertIs(out.fits, False)
            self.assertNotEqual(out.fits, True)
            self.assertEqual(out.status, "incompatible")
            self.assertEqual(out.fragments, [])
            self.assertIn("fixed_overhead_exceeds_budget", out.reason_codes)
            ctx.db.close()
        finally:
            tmp.cleanup()

    def test_ac19(self) -> None:
        """无准确 tokenizer → estimated，不宣称精确硬上限。"""
        env = build_context_envelope(
            token_budget=500,
            sections={"stable_prefix": "s", "tools": "", "history": "", "header_tags": "", "output_reserve": ""},
            memory_candidates=[{"id": "m1", "content": "hello world", "tags": ["sourced"]}],
            has_accurate_tokenizer=False,
            measurement="measured",  # 试图谎报也应被降级
        )
        self.assertEqual(env["measurement"], "estimated")
        self.assertFalse(env["hard_limit_verified"])
        self.assertIn("token_limit_estimated_only", env["reason_codes"])
        self.assertIn("estimated", env["note"].lower() + env["measurement"])

        # 入口默认也不宣称 measured
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            ctx = build_context(data_dir=tmp.name, token="e04b")
            out = ctx.service.build_context(ContextBuildIn(query="x", token_budget=800))
            self.assertEqual(out.measurement, "estimated")
            self.assertFalse(out.hard_limit_verified)
            ctx.db.close()
        finally:
            tmp.cleanup()

    def test_estimate_fn_basic(self) -> None:
        self.assertEqual(estimate_tokens_chars(""), 0)
        self.assertGreaterEqual(estimate_tokens_chars("ab"), 1)


if __name__ == "__main__":
    unittest.main()
