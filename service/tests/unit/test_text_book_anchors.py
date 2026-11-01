# -*- coding: utf-8 -*-
"""E14 AC48–AC49：书籍定位与作者观点。"""

from __future__ import annotations

import unittest

from cognitive_memory.domains.text_book import (
    author_claim_from_paragraph,
    parse_text_book,
)


SAMPLE = """第一章 开端

这是第一段观点：世界是平的。

这是第二段。

第二章 发展

作者认为：时间是循环的。
"""


class TextBookAnchorTests(unittest.TestCase):
    def test_ac48(self) -> None:
        """UTF-8 章段无可靠页码 → 章段/hash/revision 可定位，page=null。"""
        parsed = parse_text_book(SAMPLE, book_id="book-demo", revision="rev1", title="演示")
        self.assertGreaterEqual(parsed["chapter_count"], 2)
        self.assertGreaterEqual(parsed["paragraph_count"], 2)
        for para in parsed["paragraphs"]:
            anchor = para["anchor"]
            self.assertEqual(anchor["book_id"], "book-demo")
            self.assertIsInstance(anchor["chapter"], int)
            self.assertIsInstance(anchor["paragraph"], int)
            self.assertEqual(anchor["revision"], "rev1")
            self.assertTrue(anchor["hash"])
            self.assertIsNone(anchor["page"])
            self.assertIsNone(para["page"])

    def test_ac49(self) -> None:
        """作者未核验观点 → 保留 author_claim，不自动成自我信念或 verified。"""
        parsed = parse_text_book(SAMPLE, book_id="book-demo", revision="rev1")
        claim = author_claim_from_paragraph(parsed["paragraphs"][0], author="某作者")
        self.assertEqual(claim["kind"], "author_claim")
        self.assertEqual(claim["epistemic"], "author_claim")
        self.assertFalse(claim["verified"])
        self.assertFalse(claim["self_belief"])
        self.assertIn("reading_ne_adopting_author_views", claim["note"])


if __name__ == "__main__":
    unittest.main()
