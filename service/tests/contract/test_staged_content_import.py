# -*- coding: utf-8 -*-
"""E14 AC47 / AC50：分阶段导入重放与内容重导入。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.migration.text_book_import import TextBookImportPipeline

BOOK_V1 = """第一章

旧段落 alpha。
"""

BOOK_V2 = """第一章

新段落 beta。
"""


class StagedContentImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e14-token")
        self.pipe = TextBookImportPipeline(self.ctx.db, self.ctx.service)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac47(self) -> None:
        """staging 导入中断 → 恢复幂等，对账明确，无双正式注入。"""
        staged = self.pipe.stage(
            book_id="b1",
            text=BOOK_V1 + "\n\n第二章\n\n又一段。\n",
            revision="rev1",
            batch_id="batch-ac47",
        )
        self.assertEqual(staged["status"], "staged")
        interrupted = self.pipe.commit("batch-ac47", crash_after=1)
        self.assertTrue(interrupted.get("interrupted"))
        wm = interrupted["watermark"]
        self.assertGreaterEqual(wm, 1)

        resumed = self.pipe.commit("batch-ac47")
        self.assertTrue(resumed.get("ok"))
        self.assertEqual(resumed.get("status"), "committed")
        self.assertFalse(resumed.get("double_inject"))

        again = self.pipe.commit("batch-ac47")
        self.assertTrue(again.get("deduped") or again.get("status") == "committed")
        # 幂等：同 batch 再 stage/commit 不双注入
        restage = self.pipe.stage(
            book_id="b1", text=BOOK_V1, revision="rev1", batch_id="batch-ac47"
        )
        self.assertTrue(restage.get("deduped") or restage.get("status") == "committed")

    def test_ac50(self) -> None:
        """书段 rev1→rev2：走 E02 失效，权限/tombstone 不被绕过。"""
        s1 = self.pipe.stage(book_id="b2", text=BOOK_V1, revision="rev1", batch_id="b2-r1")
        c1 = self.pipe.commit("b2-r1", bank_id="personal", actor_banks=["personal"])
        self.assertTrue(c1.get("ok"))

        denied = self.pipe.reimport_revision(
            book_id="b2",
            text=BOOK_V2,
            old_revision="rev1",
            new_revision="rev2",
            bank_id="personal",
            actor_banks=["other"],
        )
        self.assertEqual(denied.get("code"), "bank_denied")
        self.assertFalse(denied.get("ok", False))

        bumped = self.pipe.reimport_revision(
            book_id="b2",
            text=BOOK_V2,
            old_revision="rev1",
            new_revision="rev2",
            bank_id="personal",
            actor_banks=["personal"],
        )
        self.assertTrue(bumped.get("ok"), bumped)
        self.assertTrue(bumped.get("e02_path"))
        self.assertTrue(bumped.get("tombstone_honored"))
        self.assertFalse(bumped.get("permission_bypassed"))
        self.assertTrue(bumped.get("invalidated_roots"))


if __name__ == "__main__":
    unittest.main()
