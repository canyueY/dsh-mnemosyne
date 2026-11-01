# -*- coding: utf-8 -*-
"""E08 AC30：临时隔离环境干净安装与完整生命周期。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cognitive_memory.tracing.health_probes import clean_bank_lifecycle


class CleanBankLifecycleTests(unittest.TestCase):
    def test_ac30(self) -> None:
        """临时隔离环境：记录、重启、纠正、失效、再查询；初始化与升级备份验证。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            root = Path(scratch) / "clean-bank"
            result = clean_bank_lifecycle(root)
            self.assertTrue(result.get("ok"), result)
            steps = {s["step"]: s for s in result["steps"]}
            self.assertTrue(steps["install"]["ok"], steps["install"])
            self.assertTrue(steps["verify_init"]["ok"], steps["verify_init"])
            self.assertTrue(steps["record"]["ok"])
            self.assertTrue(steps["restart_read"]["ok"], steps["restart_read"])
            self.assertTrue(steps["correct"]["ok"])
            self.assertTrue(steps["invalidate"]["ok"])
            self.assertTrue(steps["requery"]["ok"])
            self.assertEqual(steps["requery"].get("active_hits"), 0)
            self.assertTrue(steps["upgrade_backup"]["ok"], steps["upgrade_backup"])
            self.assertTrue(result.get("capabilities_default_runnable"))
            self.assertTrue(result.get("target_modified_only_temp_bank"))


if __name__ == "__main__":
    unittest.main()
