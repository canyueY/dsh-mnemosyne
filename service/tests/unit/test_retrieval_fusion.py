# -*- coding: utf-8 -*-
"""E09 AC32–AC34：词法与多路召回融合。"""

from __future__ import annotations

import unittest

from cognitive_memory.retrieval.fusion import (
    ablation_report,
    fuse_channels,
    lexical_tokens,
    nfkc_normalize,
)


class RetrievalFusionTests(unittest.TestCase):
    def test_ac32(self) -> None:
        """候选含越权 Bank 和不适用时间 → 召回前过滤，输出前复核，不泄漏。"""
        candidates = [
            {
                "id": "ok",
                "content": "主人喜欢咖啡",
                "bank_id": "personal",
                "metadata": {},
            },
            {
                "id": "leak-bank",
                "content": "秘密项目预算",
                "bank_id": "secret",
                "metadata": {},
            },
            {
                "id": "expired",
                "content": "主人喜欢茶",
                "bank_id": "personal",
                "metadata": {"validity_to": "2020-01-01T00:00:00Z"},
            },
        ]
        out = fuse_channels(
            query="喜欢什么",
            candidates=candidates,
            allowed_banks={"personal"},
            mode="current",
            at="2026-06-01T00:00:00Z",
            limit=5,
        )
        ids = {x["id"] for x in out["items"]}
        self.assertIn("ok", ids)
        self.assertNotIn("leak-bank", ids)
        self.assertNotIn("expired", ids)
        dropped_ids = {x["id"] for x in out["dropped_prefilter"]}
        self.assertIn("leak-bank", dropped_ids)
        self.assertIn("expired", dropped_ids)
        # 后置复核路径：即使混入也要挡
        sneaky = fuse_channels(
            query="秘密",
            candidates=[
                {"id": "ok", "content": "公开笔记", "bank_id": "personal", "metadata": {}},
                {"id": "x", "content": "秘密项目预算", "bank_id": "secret", "metadata": {}},
            ],
            allowed_banks={"personal"},
            mode="current",
            limit=5,
        )
        self.assertTrue(all(i["bank_id"] == "personal" for i in sneaky["items"]))
        self.assertTrue(sneaky["notes"]["postfilter_mandatory"])

    def test_ac33(self) -> None:
        """关键反证排序低 → 反证独立通道；预算内未检查标 partial。"""
        support = [
            {"id": f"s{i}", "content": f"支持陈述{i} 喜欢咖啡", "bank_id": "personal", "metadata": {}}
            for i in range(6)
        ]
        counter = [
            {
                "id": "ce-critical",
                "content": "不喜欢咖啡 反证",
                "bank_id": "personal",
                "metadata": {},
            },
            {
                "id": "ce-2",
                "content": "咖啡过敏 反证",
                "bank_id": "personal",
                "metadata": {},
            },
            {
                "id": "ce-3",
                "content": "改喝茶了 反证",
                "bank_id": "personal",
                "metadata": {},
            },
        ]
        out = fuse_channels(
            query="喜欢咖啡吗",
            candidates=support,
            allowed_banks={"personal"},
            mode="current",
            limit=3,
            counterevidence=counter,
            counterevidence_budget=1,
        )
        self.assertTrue(out["notes"]["counterevidence_independent"])
        self.assertEqual(len(out["counterevidence"]), 1)
        self.assertTrue(out["counterevidence"][0]["checked"])
        self.assertTrue(out["counterevidence_unchecked"])
        self.assertEqual(out["status"], "partial")
        self.assertIn("counterevidence_unchecked", out["reason_codes"])
        # 反证不因主通道截断而消失于未标记状态
        self.assertTrue(all(not x.get("checked") for x in out["counterevidence_unchecked"]))

    def test_ac34(self) -> None:
        """无真实向量 provider 只有 hash → 轻量可用且标 hash；消融可复现。"""
        # NFKC：全角数字归一
        self.assertEqual(nfkc_normalize("喜欢ＣＡＦＥ"), "喜欢CAFE")
        self.assertTrue(lexical_tokens("喜欢咖啡"))

        candidates = [
            {"id": "a", "content": "我喜欢夜航星", "bank_id": "personal", "metadata": {}},
            {"id": "b", "content": "打印机缺墨", "bank_id": "personal", "metadata": {}},
            {"id": "c", "content": "喜欢听夜航星这首歌", "bank_id": "personal", "metadata": {}},
        ]
        out = fuse_channels(
            query="喜欢夜航星",
            candidates=candidates,
            allowed_banks={"personal"},
            mode="current",
            use_vector=True,
            embedding_provider="hash",
            limit=3,
        )
        self.assertEqual(out["embedding_meta"]["embedding_kind"], "hash")
        self.assertTrue(out["embedding_meta"]["do_not_overclaim"])
        self.assertTrue(out["notes"]["hash_not_hq_embedding"])
        vec_ch = [c for c in out["channels"] if c["name"] == "vector"][0]
        self.assertEqual(vec_ch["embedding_kind"], "hash")

        abl = ablation_report(
            query="喜欢夜航星",
            candidates=candidates,
            allowed_banks={"personal"},
            limit=3,
        )
        self.assertTrue(abl["reproducible"])
        self.assertTrue(abl["offline_only"])
        self.assertEqual(abl["metrics"]["cost"]["external_model_calls"], 0)
        self.assertEqual(abl["metrics"]["cost"]["embedding_kind"], "hash")
        self.assertIn("fusion_ids", abl)
        # 二次运行可复现
        abl2 = ablation_report(
            query="喜欢夜航星",
            candidates=candidates,
            allowed_banks={"personal"},
            limit=3,
        )
        self.assertEqual(abl["fusion_ids"], abl2["fusion_ids"])
        self.assertEqual(abl["lexical_ids"], abl2["lexical_ids"])


if __name__ == "__main__":
    unittest.main()
