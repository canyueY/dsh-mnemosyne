import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from cognitive_memory.bootstrap import build_context
from cognitive_memory.domain.models import ObservationIn, SourceKind, SearchQuery, MemoryProposeIn
from cognitive_memory.core.service import ConflictError
from cognitive_memory.memory.shallow import stage_at, maintain, select, policy
from cognitive_memory.cognition.consolidate import consolidate_observation
from cognitive_memory.cognition.promote import maybe_promote


class ShallowMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="shallow-test")
        self.s = self.ctx.service

    def tearDown(self):
        self.ctx.db.close(); self.tmp.cleanup()

    def capture(self, days=0, key=None):
        created = (datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        with patch("cognitive_memory.core.service._now", return_value=created):
            obs = self.s.capture_observation(ObservationIn(source_kind=SourceKind.VIDEO_OBSERVATION,
                content="我不喜欢辣椒。视频谈到咖啡和音乐。", idempotency_key=key,
                metadata={"user_id":"alice", "bank_id":"personal"}))
        return obs, next(m for m in self.s.search(SearchQuery(mode="historical")) if obs.id in m.source_ids)

    def test_exact_stage_boundaries(self):
        start = datetime(2026,1,1,tzinfo=timezone.utc)
        for days, stage in [(0,"temporary"),(79.99,"temporary"),(80,"prompt"),(119.99,"prompt"),(120,"impression"),(200,"impression"),(200.01,"archived")]:
            self.assertEqual(stage_at(start,start+timedelta(days=days)),stage)

    def test_capture_immediately_creates_provisional_and_replay_is_idempotent(self):
        obs, mem = self.capture(key="one")
        self.s.capture_observation(ObservationIn(source_kind=SourceKind.VIDEO_OBSERVATION,content="retry",idempotency_key="one"))
        self.assertEqual(consolidate_observation(self.s,obs.id),mem.id)
        state=mem.metadata["shallow_memory"]
        self.assertEqual(state["long_term_class"],"provisional_deletable")
        self.assertFalse(state["formal_long_term"])
        self.assertIn("不喜欢",state["initial_cognition"]["text"])
        self.assertEqual(self.ctx.db.connect().execute("SELECT count(*) FROM memories").fetchone()[0],1)
        self.assertFalse(maybe_promote(self.s,mem.id))
        with self.assertRaises(ConflictError):
            self.s.propose_memory(MemoryProposeIn(content="bypass", source_ids=[obs.id]))

    def test_periodic_extraction_and_archive_preserve_original(self):
        obs, mem=self.capture()
        start=datetime.fromisoformat(mem.created_at)
        self.assertEqual(maintain(self.s,now=start+timedelta(hours=2))["updated"],0)
        for days,stage in [(1,"temporary"),(80,"prompt"),(120,"impression"),(201,"archived")]:
            maintain(self.s,now=start+timedelta(days=days))
            data=self.s.get_memory(mem.id)
            self.assertEqual(data["metadata"]["shallow_memory"]["stage"],stage)
        self.assertEqual(data["status"],"superseded")
        self.assertEqual(self.ctx.db.connect().execute("SELECT content FROM observations WHERE id=?",(obs.id,)).fetchone()[0],obs.content)
        self.assertEqual(self.s.search(SearchQuery()),[])

    def test_selection_age_and_existing_verification_gate(self):
        _,young=self.capture()
        with self.assertRaises(ValueError):select(self.s,young.id,reason="review",actor="test")
        _,old=self.capture(days=90)
        maintain(self.s)
        self.assertFalse(maybe_promote(self.s,old.id))
        candidate=select(self.s,old.id,reason="selected after review",actor="test")
        self.assertEqual(candidate["status"],"candidate")
        self.assertEqual(candidate["metadata"]["selected_from_shallow"],old.id)
        self.assertFalse(maybe_promote(self.s,candidate["id"]))

    def test_policy_api_and_selection_confirmation(self):
        _,mem=self.capture()
        with TestClient(self.ctx.app) as client:
            headers={"Authorization":"Bearer shallow-test"}
            self.assertEqual(client.get("/api/v1/shallow/policy",headers=headers).json(),policy())
            self.assertEqual(client.post(f"/api/v1/shallow/{mem.id}/select",json={"reason":"x"},headers=headers).status_code,400)
            self.assertEqual(client.get("/api/v1/shallow/policy").status_code,401)

    def test_verified_evidence_can_form_formal_long_term_and_clear_ttl(self):
        from cognitive_memory.claims.store import ClaimStore
        from cognitive_memory.evidence.object_store import EvidenceObjectStore
        _, mem = self.capture(days=90)
        claims=ClaimStore(self.ctx.db)
        claim=claims.propose_from_text("Alice喜欢咖啡",source_memory_id=mem.id)
        evidence=EvidenceObjectStore(self.ctx.db).create(source_type="doc",source_ref="fixture:independent",
            producer="test",content_for_fingerprint="Alice confirms coffee preference",
            validation_state="valid",provenance_status="complete",chain=[{"source":"fixture:independent"}])["evidence"]["id"]
        claims.attach_evidence(claim["claim_id"],evidence,role="support")
        # Seed verified lifecycle; promotion must still run real assess_evidence.
        with self.ctx.db.transaction() as conn:
            conn.execute("UPDATE claims SET lifecycle='verified',verification_status='verified' WHERE id=?",(claim["claim_id"],))
        self.assertTrue(maybe_promote(self.s,mem.id))
        result=self.s.get_memory(mem.id)
        self.assertTrue(result["metadata"]["shallow_memory"]["formal_long_term"])
        self.assertEqual(result["status"],"active")
        self.assertIsNone(result["expires_at"])
