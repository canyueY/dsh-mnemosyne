# -*- coding: utf-8 -*-
"""mnemosyne-retrieval-quality MA02–MA18（复用既有模块）。"""

from __future__ import annotations

import tempfile
import unittest

from cognitive_memory.bootstrap import build_context
from cognitive_memory.cognition.answer_selection import AnswerSelectionService
from cognitive_memory.cognition.consolidate import consolidate_session
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.domain.models import MemoryProposeIn
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.retrieval.context_budget import build_context_envelope
from cognitive_memory.retrieval.fusion import fuse_channels
from cognitive_memory.strategy.fencing import FencingError, WorkerLeaseManager
from cognitive_memory.strategy.watermarks import ConsumerWatermarkStore
from cognitive_memory.tracing.cost_ledger import UsageLedger


class RetrievalQualityMATests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="ma-token")
        self.sel = AnswerSelectionService(self.ctx.service)
        self.claims = ClaimStore(self.ctx.db)
        self.estore = EvidenceObjectStore(self.ctx.db)
        self.source = self.ctx.service.propose_memory(
            MemoryProposeIn(content="retrieval-quality fixture source")
        )

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def _set_validity(self, claim_id: str, *, vf=None, vt=None) -> None:
        with self.ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE claims SET validity_from=?, validity_to=? WHERE id=?",
                (vf, vt, claim_id),
            )

    def _propose(self, text: str) -> dict:
        # Selection requires traceable provenance; these fixtures model a source.
        return self.claims.propose_from_text(
            text, source_memory_id=self.source.id
        )

    def test_ma02(self) -> None:
        c = self._propose("A喜欢咖啡")
        out = self.sel.select_answer(
            target={"subject_id": "A", "predicate": "喜欢"}, mode="current"
        )
        self.assertEqual(out["status"], "chosen", out)
        self.assertFalse(out.get("wrote_verified"))
        pack = self.claims.get(c["claim_id"], follow_redirect=False)
        self.assertNotEqual(pack.get("lifecycle"), "verified")

    def test_ma03(self) -> None:
        out = self.sel.select_answer(
            target={"subject_id": "Z_missing", "predicate": "喜欢"}, mode="current"
        )
        self.assertEqual(out["status"], "unknown", out)
        self.assertTrue(
            {"source_missing", "coverage_insufficient"} & set(out["reason_codes"])
        )

    def test_ma04(self) -> None:
        self._propose("A喜欢咖啡")
        for i in range(6):
            self._propose(f"A不喜欢咖啡变体{i}")
        out = self.sel.select_answer(
            target={"subject_id": "A", "predicate": "喜欢", "negated": False},
            budget={"max_candidates": 1, "max_steps": 3},
            mode="current",
        )
        self.assertIn(out["status"], {"partial", "unknown"}, out)
        self.assertTrue(
            out.get("counterevidence_refs") is not None
            or {"counterevidence_pending", "budget_exhausted"} & set(out["reason_codes"])
        )

    def test_ma05(self) -> None:
        common = dict(
            root_source_id="book:ma05",
            revision="rev-1",
            producer="dsh",
            content_for_fingerprint="same-body",
            apply_source_contract=True,
            coverage={"available_modalities": ["text"], "completeness": "partial"},
        )
        a = self.estore.create(source_type="import", source_ref="a", bank_id="personal", **common)
        b = self.estore.create(source_type="observation", source_ref="b", bank_id="personal", **common)
        self.assertTrue(a["ok"] and b["ok"])
        self.assertTrue(b.get("deduped"))
        self.assertEqual(a["evidence"]["id"], b["evidence"]["id"])

    def test_ma06(self) -> None:
        c = self._propose("A喜欢咖啡")
        self._set_validity(c["claim_id"], vf="2020-01-01T00:00:00Z", vt="2021-01-01T00:00:00Z")
        out = self.sel.select_answer(
            target={"subject_id": "A", "predicate": "喜欢"},
            at="2026-01-01T00:00:00Z",
            mode="current",
        )
        self.assertEqual(out["status"], "unknown", out)

    def test_ma07(self) -> None:
        a = self._propose("A喜欢咖啡")
        b = self._propose("A喜欢茶")
        self.claims.transition(a["claim_id"], "retracted", reason="withdraw")
        pack_b = self.claims.get(b["claim_id"], follow_redirect=False)
        self.assertIsNotNone(pack_b)
        self.assertNotEqual(pack_b.get("lifecycle"), "retracted")

    def test_ma08(self) -> None:
        out = fuse_channels(
            query="喜欢咖啡",
            candidates=[
                {"id": "1", "content": "A喜欢咖啡", "bank_id": "personal"},
                {"id": "2", "content": "B喜欢茶", "bank_id": "personal"},
            ],
            allowed_banks={"personal"},
            limit=5,
        )
        self.assertEqual(out["fusion_method"], "rrf")
        self.assertTrue(out["items"][0].get("sources"))

    def test_ma09(self) -> None:
        out = fuse_channels(
            query="喜欢咖啡",
            candidates=[{"id": "s1", "content": "A喜欢咖啡", "bank_id": "personal"}],
            counterevidence=[
                {"id": "c1", "content": "A不喜欢咖啡", "bank_id": "personal"},
                {"id": "c2", "content": "A讨厌咖啡", "bank_id": "personal"},
            ],
            counterevidence_budget=1,
            allowed_banks={"personal"},
        )
        self.assertTrue(out["counterevidence"])
        self.assertTrue(out["notes"]["counterevidence_independent"])

    def test_ma10(self) -> None:
        out = fuse_channels(
            query="语义",
            candidates=[{"id": "1", "content": "哈希不是语义", "bank_id": "personal"}],
            allowed_banks={"personal"},
        )
        self.assertEqual(out["semantic_model"], "unavailable")
        self.assertIn("candidate_similarity", out["items"][0])

    def test_ma11(self) -> None:
        out = consolidate_session(
            session_id="ma11",
            atoms=[
                {"id": "a1", "result": "passed", "content": "观察1", "source_ids": ["s1"]},
                {"id": "a2", "result": "passed", "content": "观察2", "source_ids": ["s2"]},
            ],
            topic="巩固",
        )
        derived = out["derived"]
        self.assertEqual(derived["consolidation_level"], "derived_summary")
        self.assertIn("importance_score", derived)
        self.assertTrue(derived.get("source_refs") is not None)

    def test_ma12(self) -> None:
        atoms = [
            {
                "id": f"a{i}",
                "result": "passed",
                "content": "x" * 200,
                "source_ids": [f"s{i}"],
                "lossy": True,
            }
            for i in range(3)
        ]
        out = consolidate_session(session_id="ma12", atoms=atoms, topic="压缩")
        self.assertTrue(out["derived"]["lossy"])
        self.assertTrue(out["originals_unchanged"])
        self.assertTrue(out["derived"]["source_refs"] or out["derived"]["utility_view"]["source_anchors"])

    def test_ma13(self) -> None:
        from cognitive_memory.governance.capability_gate import (
            check_capability,
            set_capability_level,
        )

        set_capability_level(self.ctx.db, "SCHEMA_MUTATION", level="LOCKED", enabled=False)
        mut = check_capability(self.ctx.db, "SCHEMA_MUTATION")
        self.assertFalse(mut["allowed"])

    def test_ma14(self) -> None:
        env = build_context_envelope(
            token_budget=300,
            sections={
                "stable_prefix": "P",
                "tools": "T",
                "history": "H",
                "header_tags": "tags",
                "output_reserve": "O",
            },
            memory_candidates=[{"id": "m1", "content": "mem", "tags": ["t"]}],
            has_accurate_tokenizer=False,
        )
        b = env["breakdown"]
        self.assertIn("tools_descriptions", b)
        self.assertIn("memory_tags", b)
        self.assertIn(env["measurement"], {"estimated", "unknown", "measured"})
        self.assertFalse(env["hard_limit_verified"])

    def test_ma15(self) -> None:
        env = build_context_envelope(
            token_budget=30,
            sections={
                "stable_prefix": "X" * 40,
                "tools": "Y" * 40,
                "history": "Z" * 40,
                "header_tags": "t",
                "output_reserve": "OUT" * 5,
            },
            memory_candidates=[{"id": "m1", "content": "should not", "tags": []}],
            config={"conservative_reserve": 50},
        )
        self.assertIs(env["fits"], False)
        self.assertEqual(env["memory_fragments"], [])
        self.assertIn("output_reserve", env["breakdown"])

    def test_ma16(self) -> None:
        led = UsageLedger(self.ctx.db)
        r1 = led.reserve(
            correlation_id="c1", attempt=1, provider="host", meter_scope="host", estimated_cost=1.0
        )
        r2 = led.reserve(
            correlation_id="c1", attempt=1, provider="node", meter_scope="node", estimated_cost=2.0
        )
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertNotEqual(r1["entry_id"], r2["entry_id"])

    def test_ma17(self) -> None:
        wm = ConsumerWatermarkStore(self.ctx.db)
        first = wm.apply_effect_with_watermark(
            consumer="utility",
            event_id="e1",
            effect_payload={"n": 1},
            next_position=1,
        )
        replay = wm.apply_effect_with_watermark(
            consumer="utility",
            event_id="e1",
            effect_payload={"n": 1},
            next_position=1,
        )
        self.assertTrue(first["ok"])
        self.assertTrue(replay["ok"])
        self.assertTrue(replay.get("deduped"))

    def test_ma18(self) -> None:
        leases = WorkerLeaseManager(self.ctx.db)
        live = leases.acquire(worker_id="w1", resource="consolidation")
        stale = leases.commit_artifact(
            fencing_token="lease_deadbeef",
            expected_generation=0,
            artifact={"x": 1},
            resource="consolidation",
        ) if False else None
        with self.assertRaises(FencingError):
            leases.commit_artifact(
                fencing_token="lease_deadbeef",
                expected_generation=0,
                artifact={"x": 1},
                resource="consolidation",
            )
        ok = leases.commit_artifact(
            fencing_token=live["fencing_token"],
            expected_generation=live["generation"],
            artifact={"x": 1},
            resource="consolidation",
        )
        self.assertTrue(ok.get("ok"))


if __name__ == "__main__":
    unittest.main()
