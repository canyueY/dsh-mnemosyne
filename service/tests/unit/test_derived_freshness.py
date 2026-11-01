# -*- coding: utf-8 -*-
"""E02 AC05–AC09：派生新鲜度、撤回、tombstone、环路、版本竞态。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryType
from cognitive_memory.evidence.derivation import DerivationStore
from cognitive_memory.evidence.object_store import EvidenceObjectStore
class DerivedFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="e02-token")
        self.drv = DerivationStore(self.ctx.db)
        self.estore = EvidenceObjectStore(self.ctx.db)
        self.claims = ClaimStore(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_ac05(self) -> None:
        """源修订至 rev2 且重建失败 → 旧派生物不可作当前证据。"""
        src = "src:book:chapter1"
        man = self.drv.register(
            artifact_kind="summary",
            dependencies=[{"id": src, "revision": "rev1", "hash": "h1"}],
            processor_version="summary.v1",
            config={"max_chars": 200},
            payload={"text": "rev1 summary"},
        )
        self.assertEqual(man["freshness"], "current")
        usable = self.drv.is_usable_current_evidence(man["derived_id"])
        self.assertTrue(usable["usable"])

        revised = self.drv.revise_source(
            src,
            new_revision="rev2",
            content_hash="h2",
            rebuild=True,
            rebuild_succeeds=False,
        )
        self.assertTrue(revised["ok"])
        after = self.drv.get(man["derived_id"])
        self.assertEqual(after["freshness"], "failed")
        self.assertIn("rebuild_failed", after["reason_codes"])
        self.assertIn("source_stale", after["reason_codes"])
        check = self.drv.is_usable_current_evidence(man["derived_id"])
        self.assertFalse(check["usable"])
        self.assertFalse(check["usable"])

    def test_ac06(self) -> None:
        """撤回一条证据后重审；其他独立支持保留。"""
        prop = self.claims.propose_from_text("用户喜欢夜航星")
        cid = prop["claim_id"]
        a = self.estore.create(
            source_type="test_fixture",
            source_ref="ev_a",
            validation_state="valid",
            provenance_status="complete",
            chain=[{"step": "fixture", "ref": "ev_a"}],
            producer="dsh",
            root_source_id="root_a",
            content_for_fingerprint="a",
            apply_source_contract=True,
            revision="1",
        )
        b = self.estore.create(
            source_type="test_fixture",
            source_ref="ev_b",
            validation_state="valid",
            provenance_status="complete",
            chain=[{"step": "fixture", "ref": "ev_b"}],
            producer="dsh",
            root_source_id="root_b",
            content_for_fingerprint="b",
            apply_source_contract=True,
            revision="1",
        )
        eid_a = a["evidence"]["id"]
        eid_b = b["evidence"]["id"]
        self.claims.attach_evidence(cid, eid_a, role="support")
        self.claims.attach_evidence(cid, eid_b, role="support")
        verified = self.claims.submit_verified(
            cid, evidence_refs=[eid_a, eid_b], actor="tester"
        )
        self.assertTrue(verified.get("ok"), msg=verified)

        revoked = self.estore.assess(eid_a, state="revoked", actor="tester")
        self.assertTrue(revoked.get("ok"), msg=revoked)
        still_b = self.estore.get(eid_b)
        self.assertIsNotNone(still_b)
        self.assertNotEqual(still_b["validation_state"], "revoked")

        claim = self.claims.get(cid, follow_redirect=False)
        self.assertIsNotNone(claim)
        # 受影响核验进入重审或仍由独立支持维持；不得删除 b
        self.assertTrue(self.estore.get(eid_b) is not None)
        links = self.claims.list_evidence(cid)
        refs = {r["evidence_ref"] for r in links}
        self.assertIn(eid_b, refs)

    def test_ac07(self) -> None:
        """授权删除留 tombstone → 重复导入被拒，来源不可核验。"""
        mem = self.ctx.service.propose_memory(
            MemoryProposeIn(
                type=MemoryType.SEMANTIC,
                status=MemoryStatus.ACTIVE,
                content="private note to forget",
                metadata={"source_ref": {"root_source_id": "import:notes:1", "source_id": "n1"}},
            )
        )
        forgotten = self.ctx.service.forget_memory(mem.id, hard=False)
        self.assertIn("source_tombstone", forgotten)
        # 当前检索不应再把 invalidated 当 ACTIVE 命中（默认状态过滤）
        hits = self.ctx.service.search(
            __import__("cognitive_memory.domain.models", fromlist=["SearchQuery"]).SearchQuery(
                query="private note to forget",
                status=[MemoryStatus.ACTIVE],
            )
        )
        self.assertEqual([h.id for h in hits], [])

        blocked = self.estore.create(
            source_type="import",
            source_ref="reimport-attempt",
            root_source_id="import:notes:1",
            producer="dsh",
            content_for_fingerprint="private note to forget",
            apply_source_contract=True,
            revision="1",
        )
        self.assertFalse(blocked.get("ok"))
        self.assertEqual(blocked.get("detail"), "tombstone_blocks_reimport")
        self.assertTrue(blocked.get("tombstone", {}).get("unverifiable"))

    def test_ac08(self) -> None:
        """A↔B 依赖环 → dependency_cycle，不可 verified。"""
        a = self.estore.create(
            source_type="test_fixture",
            source_ref="cycle_a",
            producer="dsh",
            root_source_id="cycle_root_a",
            content_for_fingerprint="a",
            provenance_status="partial",
            metadata={"parent_evidence_ids": []},
        )
        b = self.estore.create(
            source_type="test_fixture",
            source_ref="cycle_b",
            producer="dsh",
            root_source_id="cycle_root_b",
            content_for_fingerprint="b",
            provenance_status="partial",
            metadata={"parent_evidence_ids": []},
        )
        eid_a = a["evidence"]["id"]
        eid_b = b["evidence"]["id"]
        # 写入环：A→B→A
        with self.ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE evidences SET metadata_json=? WHERE id=?",
                (
                    __import__("json").dumps(
                        {"parent_evidence_ids": [eid_b]}, ensure_ascii=False
                    ),
                    eid_a,
                ),
            )
            conn.execute(
                "UPDATE evidences SET metadata_json=? WHERE id=?",
                (
                    __import__("json").dumps(
                        {"parent_evidence_ids": [eid_a]}, ensure_ascii=False
                    ),
                    eid_b,
                ),
            )
        lin = self.estore.lineage(eid_a)
        self.assertFalse(lin["ok"])
        self.assertIn("dependency_cycle", lin["reason_codes"])
        self.assertFalse(lin["verified_allowed"])

    def test_ac09(self) -> None:
        """检查后源变更 → 提交时重查，结果不能 chosen。"""
        src = "src:race"
        man = self.drv.register(
            artifact_kind="cache",
            dependencies=[{"id": src, "revision": "r1", "hash": "h1"}],
            processor_version="cache.v1",
        )
        expected = [{"id": src, "revision": "r1"}, {"derived_id": man["derived_id"], "id": man["derived_id"], "revision": "r1"}]
        # 模拟检查时仍为 r1
        ok_check = self.drv.recheck_revisions(expected, current_revisions={src: "r1"})
        self.assertTrue(ok_check["chosen_allowed"])

        # 处理中源变更 + 派生过期
        self.drv.revise_source(src, new_revision="r2", rebuild_succeeds=False)
        race = self.drv.recheck_revisions(
            expected,
            current_revisions={src: "r2"},
        )
        self.assertFalse(race["chosen_allowed"])
        self.assertEqual(race["status"], "unknown")
        self.assertTrue(
            "source_revised_during_check" in race["reason_codes"]
            or "source_stale" in race["reason_codes"]
        )


if __name__ == "__main__":
    unittest.main()
