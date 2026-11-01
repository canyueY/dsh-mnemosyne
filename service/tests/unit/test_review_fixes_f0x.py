# -*- coding: utf-8 -*-
"""Review-fixes F01–F03 / F08–F09 / F11 counterexample regressions."""

from __future__ import annotations

import json
import tempfile
import unittest

from fastapi.testclient import TestClient

from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.cognition.session_checkpoint import SessionLedger
from cognitive_memory.domain.models import MemoryProposeIn
from cognitive_memory.jobs.dispatcher import BackgroundRuntime
from cognitive_memory.retrieval.pipeline import run_retrieve
from cognitive_memory.strategy.fencing import FencingError, WorkerLeaseManager


class ReviewFixesPythonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="review-fix-token")
        self.client = TestClient(self.ctx.app)
        self.admin = {"Authorization": "Bearer review-fix-token"}
        self.claims = ClaimStore(self.ctx.db)
        self.ledger = SessionLedger(self.ctx.db)

    def tearDown(self) -> None:
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_fa03_target_arguments_must_match(self) -> None:
        self.claims.propose_from_text("A喜欢咖啡")
        response = self.client.post(
            "/api/v1/cognition/answers/select",
            headers=self.admin,
            json={"target": {"subject_id": "A", "predicate": "喜欢", "arguments": ["茶"]}},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body.get("status"), "unknown")
        self.assertEqual(body.get("selected") or [], [])

    def test_fa05_rejected_not_chosen(self) -> None:
        claim = self.claims.propose_from_text("B喜欢咖啡")
        self.assertTrue(self.claims.transition(claim["claim_id"], "rejected", reason="x").get("ok"))
        body = self.client.post(
            "/api/v1/cognition/answers/select",
            headers=self.admin,
            json={"target": {"subject_id": "B", "predicate": "喜欢"}},
        ).json()
        self.assertEqual(body.get("status"), "unknown")
        self.assertEqual(body.get("selected") or [], [])

    def test_fa02_cross_bank_checkpoint_denied(self) -> None:
        cid = "review-shared-checkpoint-id"
        first = self.client.post(
            "/api/v1/cognition/sessions/checkpoints",
            headers=self.admin,
            json={
                "checkpoint_id": cid,
                "session_id": "project-session",
                "bank_id": "projects",
                "goal": "original-project-goal",
            },
        )
        self.assertEqual(first.status_code, 200)
        reg = self.client.post(
            "/api/v1/agents/register",
            headers=self.admin,
            json={"agent_id": "agent:review-limited", "display_name": "synthetic"},
        )
        self.assertEqual(reg.status_code, 200)
        grant = self.client.post(
            "/api/v1/agents/agent:review-limited/grants",
            headers=self.admin,
            json={"permissions": ["memory.read", "memory.write"], "banks": ["personal"]},
        )
        self.assertEqual(grant.status_code, 200)
        limited = {
            "X-Agent-Id": "agent:review-limited",
            "X-Agent-Token": grant.json()["agent_token"],
        }
        overwrite = self.client.post(
            "/api/v1/cognition/sessions/checkpoints",
            headers=limited,
            json={
                "checkpoint_id": cid,
                "session_id": "personal-session",
                "bank_id": "personal",
                "goal": "overwritten-by-personal-only-agent",
            },
        )
        self.assertEqual(overwrite.status_code, 403)
        after = self.client.get(
            f"/api/v1/cognition/sessions/checkpoints/{cid}", headers=self.admin
        ).json()
        self.assertEqual(after.get("bank_id"), "projects")
        self.assertEqual(after.get("manifest", {}).get("goal"), "original-project-goal")

    def test_fa16_client_tokenizer_flag_ignored(self) -> None:
        budget = self.client.post(
            "/api/v1/contexts/build",
            headers=self.admin,
            json={"query": "review", "token_budget": 256, "has_accurate_tokenizer": True},
        ).json()
        self.assertEqual(budget.get("measurement"), "estimated")
        self.assertFalse(budget.get("hard_limit_verified"))

    def test_c01_sourceless_claim_not_chosen_but_sourced_claim_is(self) -> None:
        sourceless = self.claims.propose_from_text("C喜欢咖啡")
        body = self.client.post(
            "/api/v1/cognition/answers/select",
            headers=self.admin,
            json={"target": {"subject_id": "C", "predicate": "喜欢"}},
        ).json()
        self.assertNotEqual(body.get("status"), "chosen", body)
        self.assertEqual(body.get("selected") or [], [])
        self.assertIn("source_missing", body.get("reason_codes") or [])

        source = self.ctx.service.propose_memory(
            MemoryProposeIn(content="C01 fixture source")
        )
        self.claims.propose_from_text(
            "D喜欢咖啡", source_memory_id=source.id
        )
        sourced = self.client.post(
            "/api/v1/cognition/answers/select",
            headers=self.admin,
            json={"target": {"subject_id": "D", "predicate": "喜欢"}},
        ).json()
        self.assertEqual(sourced.get("status"), "chosen", sourced)
        self.assertEqual(len(sourced.get("selected") or []), 1)
        self.assertNotEqual(sourced["selected"][0]["claim_id"], sourceless["claim_id"])

    def test_c03_nested_envelope_tokenizer_flags_ignored(self) -> None:
        for envelope in (
            {"accurate_tokenizer": True},
            {"measurement": "measured"},
        ):
            body = self.client.post(
                "/api/v1/contexts/build",
                headers=self.admin,
                json={"query": "probe", "token_budget": 256, "envelope": envelope},
            ).json()
            self.assertEqual(body.get("measurement"), "estimated", body)
            self.assertFalse(body.get("hard_limit_verified"), body)

    def test_c04_mixed_filtered_context_does_not_leak_expired_record(self) -> None:
        safe = self.ctx.service.propose_memory(
            MemoryProposeIn(content="mixedreview active safe record")
        )
        expired = self.ctx.service.propose_memory(
            MemoryProposeIn(
                content="mixedreview expired unsafe record",
                metadata={"validity_to": "2000-01-01T00:00:00Z"},
            )
        )
        retrieval = run_retrieve(
            self.ctx.service, query="mixedreview", force=True, semantic_assist=False
        )
        ids = [x["id"] for x in retrieval.get("items") or []]
        self.assertIn(safe.id, ids)
        self.assertNotIn(expired.id, ids)
        fragment_ids = [f.get("id") for f in retrieval.get("fragments") or []]
        self.assertNotIn(expired.id, fragment_ids)
        self.assertNotIn("expired unsafe record", retrieval.get("brief") or "")

    def test_fa14_expired_not_restored_by_fusion_fallback(self) -> None:
        memory = self.ctx.service.propose_memory(
            MemoryProposeIn(
                content="reviewkeyword expired record",
                metadata={"validity_to": "2000-01-01T00:00:00Z"},
            )
        )
        retrieval = run_retrieve(
            self.ctx.service, query="reviewkeyword", force=True, semantic_assist=False
        )
        ids = [x["id"] for x in retrieval.get("items") or []]
        self.assertNotIn(memory.id, ids)
        fusion = retrieval.get("fusion") or {}
        self.assertIn("no_safe_items", fusion.get("reason_codes") or [])

    def test_fa21_checkpoint_resume_rejects_source_revision_drift(self) -> None:
        from cognitive_memory.evidence.object_store import EvidenceObjectStore

        store = EvidenceObjectStore(self.ctx.db)
        created = store.create(
            source_type="doc",
            source_ref="src-drift",
            bank_id="personal",
            root_source_id="src-drift",
            producer="test",
            revision="r1",
            content_for_fingerprint="drift-body",
        )
        self.assertTrue(created.get("ok"), created)
        eid = created["evidence"]["id"]
        saved = self.ledger.save_checkpoint(
            session_id="sess-drift",
            bank_id="personal",
            goal="g",
            evidence_revisions=[{"source_id": "src-drift", "revision": "r1"}],
        )
        cid = saved["checkpoint_id"]
        # authority advances to r2
        with self.ctx.db.transaction() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM evidences WHERE id=?", (eid,)
            ).fetchone()
            meta = json.loads(row["metadata_json"] or "{}")
            meta["revision"] = "r2"
            for key in ("source", "source_ref"):
                block = meta.get(key) if isinstance(meta.get(key), dict) else {}
                block["revision"] = "r2"
                meta[key] = block
            conn.execute(
                "UPDATE evidences SET metadata_json=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), eid),
            )
        resumed = self.ledger.resume_checkpoint(cid, principal_banks=["personal"])
        self.assertTrue(resumed.get("ok"))
        self.assertFalse(resumed.get("resumed"))
        self.assertEqual(resumed.get("status"), "source_stale")
        self.assertIn("source_revised_during_check", resumed.get("reason_codes") or [])

    def test_fa20_dispatcher_lease_rejects_stale_worker(self) -> None:
        leases = WorkerLeaseManager(self.ctx.db)
        old = leases.acquire(worker_id="old", resource="consolidation")
        runtime = BackgroundRuntime(self.ctx.db, self.ctx.bus, self.ctx.service)
        # new dispatcher lease bumps generation
        runtime.ensure_lease(resource="consolidation")
        with self.assertRaises(FencingError):
            leases.commit_artifact(
                fencing_token=str(old["fencing_token"]),
                expected_generation=int(old["generation"]),
                artifact={"stale": True},
                resource="consolidation",
            )


if __name__ == "__main__":
    unittest.main()
