# -*- coding: utf-8 -*-
"""E15：跨 REST/SDK/薄宿主临时 Bank 连续性。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def _load_continuity():
    script = Path(__file__).resolve().parents[2] / "scripts" / "verify_memory_continuity.py"
    spec = importlib.util.spec_from_file_location("verify_memory_continuity", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class MemoryContinuityE2ETests(unittest.TestCase):
    def test_continuity_temp_bank(self) -> None:
        mod = _load_continuity()
        with tempfile.TemporaryDirectory(prefix="cont-e2e-", ignore_cleanup_errors=True) as scratch:
            report = mod.run_continuity(Path(scratch) / "bank")
            self.assertTrue(report.get("ok"), report)
            self.assertFalse(report.get("production_path_used"))
            names = [s["step"] for s in report["steps"]]
            for required in (
                "record",
                "restart_read",
                "recall_select",
                "correct",
                "source_change_invalidate",
                "next_answer_rest",
                "next_answer_sdk",
                "next_answer_host",
            ):
                self.assertIn(required, names)
            vias = {s.get("via") for s in report["steps"]}
            self.assertTrue({"service", "rest", "sdk", "thin_host_adapter"} <= vias)


if __name__ == "__main__":
    unittest.main()
