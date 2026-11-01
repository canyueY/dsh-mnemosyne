#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E15：临时 Bank 记忆连续性验收（不接受默认生产路径）。

链路：Service 直调 → REST → SDK → 薄宿主适配器（DshHttpAdapter）。
场景：记录 → 重启 → 召回/选择 → 纠正 → 源变更派生失效 → 下一轮回答。
"""

from __future__ import annotations

import json
import sys
import tempfile
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# PowerShell run_checks 会把 stderr 警告当成失败；导入前压制第三方弃用告警。
warnings.simplefilter("ignore", DeprecationWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

from cognitive_memory.adapters.dsh_http import DshHttpAdapter  # noqa: E402
from cognitive_memory.bootstrap import build_context  # noqa: E402
from cognitive_memory.cognition.answer_selection import AnswerSelectionService  # noqa: E402
from cognitive_memory.cognition.execution_receipts import (  # noqa: E402
    ExecutionReceiptStore,
    FakeExecutor,
)
from cognitive_memory.claims.store import ClaimStore  # noqa: E402
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryUpdateIn  # noqa: E402
from cognitive_memory.evidence.derivation import DerivationStore  # noqa: E402
from cognitive_memory.sdk.client import MnemosyneClient  # noqa: E402

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient  # noqa: E402


def _set_validity(db, claim_id: str, *, vf: str | None, vt: str | None) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE claims SET validity_from=?, validity_to=? WHERE id=?",
            (vf, vt, claim_id),
        )


def run_continuity(data_dir: Path) -> dict:
    steps: list[dict] = []
    data_dir = Path(data_dir)
    # 拒绝空路径/明显生产默认名
    resolved = data_dir.resolve()
    banned = {"personal", "default", "production", "prod"}
    if any(part.lower() in banned for part in resolved.parts):
        return {
            "ok": False,
            "error": "refused_suspicious_bank_path",
            "data_dir": str(resolved),
            "production_path_used": True,
            "steps": [],
        }

    ctx = build_context(data_dir=str(data_dir), token="continuity-token")
    http = TestClient(ctx.app)
    client = MnemosyneClient("http://test", "continuity-token", session=http)
    adapter = DshHttpAdapter(client)
    try:
        # 1 record (service)
        mem = ctx.service.propose_memory(
            MemoryProposeIn(content="连续性：A喜欢咖啡", status=MemoryStatus.ACTIVE)
        )
        steps.append({"step": "record", "ok": True, "memory_id": mem.id, "via": "service"})

        # 2 restart context
        mem_id = mem.id
        ctx.db.close()
        ctx = build_context(data_dir=str(data_dir), token="continuity-token")
        http = TestClient(ctx.app)
        client = MnemosyneClient("http://test", "continuity-token", session=http)
        adapter = DshHttpAdapter(client)
        got = ctx.service.get_memory(mem_id)
        steps.append(
            {
                "step": "restart_read",
                "ok": got.get("content") == "连续性：A喜欢咖啡",
                "via": "service",
            }
        )

        # 3 recall / select (service AnswerSelection)
        claims = ClaimStore(ctx.db)
        old = claims.propose_from_text("A喜欢咖啡")
        new = claims.propose_from_text("A喜欢茶")
        _set_validity(ctx.db, old["claim_id"], vf="2026-01-01T00:00:00Z", vt="2026-06-01T00:00:00Z")
        _set_validity(ctx.db, new["claim_id"], vf="2026-06-01T00:00:00Z", vt=None)
        sel = AnswerSelectionService(ctx.service).select_answer(
            target={"subject_id": "A", "predicate": "喜欢"},
            at="2026-09-01T00:00:00Z",
            mode="current",
        )
        steps.append(
            {
                "step": "recall_select",
                "ok": sel.get("status") == "chosen"
                and sel["selected"][0]["claim_id"] == new["claim_id"],
                "selected": sel.get("selected"),
                "via": "service",
            }
        )

        # 4 correct
        updated = ctx.service.update_memory(
            mem_id,
            MemoryUpdateIn(content="连续性：A喜欢茶（纠正）", expected_version=got["version"]),
        )
        steps.append({"step": "correct", "ok": True, "version": updated.version, "via": "service"})

        # 5 source change + derived invalidate
        drv = DerivationStore(ctx.db)
        reg = drv.register(
            artifact_kind="continuity_summary",
            dependencies=[{"id": "src-cont", "revision": "1", "hash": "h1"}],
            processor_version="v1",
            bank_id="personal",
        )
        stale = drv.mark_stale_for_source("src-cont", new_revision="2", bank_id="personal")
        steps.append(
            {
                "step": "source_change_invalidate",
                "ok": bool(reg) and bool(stale.get("stale_ids")),
                "stale_ids": stale.get("stale_ids"),
                "via": "service",
            }
        )

        # 6 next answer via REST
        rest = http.post(
            "/api/v1/cognition/answers/select",
            headers={"Authorization": "Bearer continuity-token"},
            json={
                "bank_id": "personal",
                "target": {"subject_id": "A", "predicate": "喜欢"},
                "mode": "current",
                "at": "2026-09-01T00:00:00Z",
            },
        )
        rest_body = rest.json() if rest.status_code < 500 else {}
        steps.append(
            {
                "step": "next_answer_rest",
                "ok": rest.status_code == 200 and rest_body.get("status") == "chosen",
                "status_code": rest.status_code,
                "claim_id": (rest_body.get("selected") or [{}])[0].get("claim_id"),
                "via": "rest",
            }
        )

        # 7 SDK
        via_sdk = client.select_answer(
            {
                "bank_id": "personal",
                "target": {"subject_id": "A", "predicate": "喜欢"},
                "mode": "current",
                "at": "2026-09-01T00:00:00Z",
            }
        )
        steps.append(
            {
                "step": "next_answer_sdk",
                "ok": via_sdk.get("status") == "chosen",
                "claim_id": (via_sdk.get("selected") or [{}])[0].get("claim_id"),
                "via": "sdk",
            }
        )

        # 8 thin-host adapter
        via_host = adapter.select_answer(
            {
                "bank_id": "personal",
                "target": {"subject_id": "A", "predicate": "喜欢"},
                "mode": "current",
                "at": "2026-09-01T00:00:00Z",
            }
        )
        steps.append(
            {
                "step": "next_answer_host",
                "ok": via_host.get("availability") == "available"
                and via_host.get("status") == "chosen",
                "claim_id": (via_host.get("selected") or [{}])[0].get("claim_id"),
                "via": "thin_host_adapter",
            }
        )

        # 9 fake executor (protocol only)
        fake = FakeExecutor(ExecutionReceiptStore(ctx.db))
        preview = fake.preview(
            actor="continuity",
            bank_id="personal",
            capability="demo",
            payload={"config": {"ok": True}},
        )
        steps.append(
            {
                "step": "fake_executor",
                "ok": preview.get("ok") is True and preview.get("real_platform_ops") is False,
                "real_platform_ops": False,
                "via": "fake_executor",
            }
        )

        ok = all(s.get("ok") for s in steps)
        return {
            "ok": ok,
            "data_dir": str(resolved),
            "production_path_used": False,
            "channels": ["service", "rest", "sdk", "thin_host_adapter"],
            "steps": steps,
            "user_effect": "not_measured",
        }
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            ctx.db.close()
        except Exception:
            pass


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemosyne-continuity-") as scratch:
        data_dir = Path(scratch) / "bank"
        report = run_continuity(data_dir)
        out = ROOT / "reports" / "memory_continuity.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "path": str(out)}, ensure_ascii=False))
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
