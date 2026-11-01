# -*- coding: utf-8 -*-
"""E01 AC01–AC04：SourceRef / Coverage / Bank / 工作区信任契约。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition.consolidate import consolidate_observation
from cognitive_memory.domain.models import ObservationIn, SourceKind
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.evidence.source_contract import (
    coverage_adequacy,
    normalize_coverage,
)
from cognitive_memory.governance.policy import PolicyDeniedError
from cognitive_memory.governance.workspace import grant_workspace_read
from cognitive_memory.identity import issue_grant, register_agent
from cognitive_memory.ingestion import ingest_observation
from cognitive_memory.storage.db import Database


class SourceCoverageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e01-token")
        self.http = TestClient(self.ctx.app)
        self.h = {"Authorization": "Bearer e01-token"}
        self.estore = EvidenceObjectStore(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac01(self) -> None:
        """同源同修订经两个入口导入 → 独立支持数不增加。"""
        common = dict(
            root_source_id="book:demo:root",
            revision="rev-1",
            producer="dsh",
            content_for_fingerprint="same-body",
            apply_source_contract=True,
            coverage={
                "available_modalities": ["text"],
                "completeness": "partial",
            },
        )
        # 入口 A：evidences API 形态（object store）
        a = self.estore.create(
            source_type="import",
            source_ref="entry:a:locator",
            bank_id="personal",
            **common,
        )
        # 入口 B：另一 locator / source_ref，同 root+revision+hash
        b = self.estore.create(
            source_type="observation",
            source_ref="entry:b:locator",
            bank_id="personal",
            **common,
        )
        self.assertTrue(a["ok"])
        self.assertTrue(b["ok"])
        self.assertTrue(b.get("deduped"), msg=b)
        self.assertEqual(a["evidence"]["id"], b["evidence"]["id"])
        # 即便强制插入不同 fingerprint 的同根同修订，独立支持计数仍为 1
        c = self.estore.create(
            source_type="import",
            source_ref="entry:c:other-content",
            bank_id="personal",
            root_source_id="book:demo:root",
            revision="rev-1",
            producer="dsh",
            content_for_fingerprint="different-body-same-root-rev",
            apply_source_contract=True,
        )
        # content_hash 不同 → 可落第二条，但独立支持按 root@rev 计为 1 组内副本
        ids = [a["evidence"]["id"]]
        if not c.get("deduped"):
            ids.append(c["evidence"]["id"])
        counted = self.estore.independent_support_count(ids)
        self.assertEqual(counted["independent_count"], 1, msg=counted)

    def test_ac02(self) -> None:
        """仅字幕覆盖 → 查询视觉结论不得声称看过画面。"""
        cov = normalize_coverage(
            {
                "available_modalities": ["subtitle", "text"],
                "missing_ranges": [
                    {"modality": "frame", "note": "no video frames"},
                    {"modality": "audio", "note": "no soundtrack"},
                ],
                "completeness": "partial",
            }
        )
        self.assertIn("subtitle", cov.available_modalities)
        verdict = coverage_adequacy(cov, query_kind="visual")
        self.assertFalse(verdict["sufficient"])
        self.assertTrue(verdict["text_coverage_only"])
        self.assertFalse(verdict["claims_visual_seen"])
        self.assertIn("coverage_insufficient", verdict["reason_codes"])

        # 经真实 evidence 元数据路径
        created = self.estore.create(
            source_type="video_observation",
            source_ref="obs_sub_only",
            producer="dsh",
            root_source_id="video:1",
            revision="1",
            content_for_fingerprint="subtitle line",
            apply_source_contract=True,
            coverage=cov.model_dump(),
        )
        meta = created["evidence"]["metadata"]
        v2 = coverage_adequacy(meta.get("coverage"), required_modalities=["visual"])
        self.assertFalse(v2["claims_visual_seen"])
        self.assertEqual(meta["coverage"]["completeness"], "partial")

    def test_ac03(self) -> None:
        """principal 仅 project-a → 写/读 project-b 被拒且无内容泄漏。"""
        register_agent(self.ctx.db, agent_id="agent:e01-a")
        g = issue_grant(
            self.ctx.db,
            "agent:e01-a",
            permissions=["memory.write", "memory.read"],
            banks=["project-a"],
        )
        headers = {
            "X-Agent-Id": "agent:e01-a",
            "X-Agent-Token": g["agent_token"],
        }
        # 写入越权 bank
        denied_write = self.http.post(
            "/api/v1/memories",
            headers=headers,
            json={
                "type": "semantic",
                "content": "secret-b",
                "status": "candidate",
                "bank_id": "project-b",
            },
        )
        self.assertIn(denied_write.status_code, (403, 404), denied_write.text)
        body = denied_write.json()
        leaked = str(body)
        self.assertNotIn("secret-b", leaked)

        # 合法写入 project-a
        ok = self.http.post(
            "/api/v1/memories",
            headers=headers,
            json={
                "type": "semantic",
                "content": "visible-a",
                "status": "candidate",
                "bank_id": "project-a",
            },
        )
        self.assertEqual(ok.status_code, 200, ok.text)

        # 搜索越权 bank
        denied_read = self.http.post(
            "/api/v1/memories/search",
            headers=headers,
            json={"query": "secret", "banks": ["project-b"]},
        )
        self.assertEqual(denied_read.status_code, 403, denied_read.text)
        self.assertEqual(denied_read.json()["detail"]["code"], "bank_denied")

    def test_ac04(self) -> None:
        """未登记工作区：采集/注入/巩固均拒绝；授权后仍不自动 verified。"""
        wid = "ws-untrusted-e01"
        meta = {"workspace_id": wid, "revision": "1"}

        with self.assertRaises(PolicyDeniedError) as cap_exc:
            self.ctx.service.capture_observation(
                ObservationIn(
                    source_kind=SourceKind.USER_STATEMENT,
                    content="should not capture",
                    producer="dsh",
                    metadata=dict(meta),
                )
            )
        self.assertEqual(cap_exc.exception.code, "workspace_read_denied")

        with self.assertRaises(PolicyDeniedError) as ing_exc:
            ingest_observation(
                self.ctx.service,
                ObservationIn(
                    source_kind=SourceKind.DIALOG,
                    content="should not ingest",
                    producer="dsh",
                    metadata=dict(meta),
                ),
            )
        self.assertEqual(ing_exc.exception.code, "workspace_read_denied")

        # 巩固：直接落一条 observation 行（绕过采集）后仍应拒绝
        db: Database = self.ctx.db
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO observations(
                  id, source_kind, content, occurred_at, created_at, producer,
                  correlation_id, data_ref, metadata_json
                ) VALUES (?, 'dialog', 'seed', '2026-11-01T00:00:00Z',
                          '2026-11-01T00:00:00Z', 'dsh', NULL, NULL, ?)
                """,
                ("obs_ws_seed", '{"workspace_id":"%s"}' % wid),
            )
        with self.assertRaises(PolicyDeniedError) as cons_exc:
            consolidate_observation(self.ctx.service, "obs_ws_seed")
        self.assertEqual(cons_exc.exception.code, "workspace_read_denied")

        # 授权读取后可采集；写入成功 ≠ verified
        grant_workspace_read(self.ctx.db, wid)
        obs = self.ctx.service.capture_observation(
            ObservationIn(
                source_kind=SourceKind.USER_STATEMENT,
                content="now allowed",
                producer="dsh",
                metadata={
                    "workspace_id": wid,
                    "revision": "1",
                    "coverage": {
                        "available_modalities": ["text"],
                        "completeness": "partial",
                    },
                },
            )
        )
        self.assertTrue(obs.id)
        self.assertEqual(obs.metadata.get("workspace_id"), wid)

        http_obs = self.http.post(
            "/api/v1/observations",
            headers=self.h,
            json={
                "source_kind": "dialog",
                "content": "api allowed",
                "producer": "dsh",
                "metadata": {"workspace_id": wid},
            },
        )
        self.assertEqual(http_obs.status_code, 200, http_obs.text)
        payload = http_obs.json()
        self.assertFalse(payload.get("auto_verified"))
        self.assertNotIn("VERIFIED", payload.get("write_stages") or [])

        # 巩固后记忆仍为 candidate / 非 verified claim
        mid = consolidate_observation(self.ctx.service, obs.id)
        self.assertIsNotNone(mid)
        mem = self.ctx.service.get_memory(mid)
        status = mem["status"] if isinstance(mem, dict) else mem.status.value
        self.assertEqual(status, "candidate")


if __name__ == "__main__":
    unittest.main()
