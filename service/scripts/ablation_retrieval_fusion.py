#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E09 消融：基线 / 词法 / 融合（离线 hash，无付费模型）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cognitive_memory.retrieval.fusion import ablation_report  # noqa: E402


def main() -> int:
    query = "喜欢夜航星"
    candidates = [
        {"id": "a", "content": "我喜欢夜航星", "bank_id": "personal", "metadata": {}},
        {"id": "b", "content": "今天天气不错", "bank_id": "personal", "metadata": {}},
        {"id": "c", "content": "喜欢听夜航星这首歌", "bank_id": "personal", "metadata": {}},
        {"id": "d", "content": "打印机缺墨", "bank_id": "personal", "metadata": {}},
        {"id": "secret", "content": "越权秘密", "bank_id": "other", "metadata": {}},
    ]
    report = ablation_report(
        query=query,
        candidates=candidates,
        allowed_banks={"personal"},
        limit=3,
    )
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ablation_retrieval_fusion.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(path), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
