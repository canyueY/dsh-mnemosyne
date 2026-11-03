"""Real SQLite/API/MCP regression for scoped recall and expiry starvation."""
import tempfile
import unittest

from fastapi.testclient import TestClient
from cognitive_memory.bootstrap import build_context
from cognitive_memory.domain.models import MemoryProposeIn, SearchQuery
from cognitive_memory.mcp.server import _dispatch


class ScopedBankSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="test-scoped-search")
        self.svc = self.ctx.service

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, content="coffee", **kw):
        return self.svc.propose_memory(MemoryProposeIn(content=content, **kw))

    def test_scopes_before_limit_in_all_recall_branches(self):
        target = self.add(metadata={"user_id": "alice", "agent_id": "a", "run_id": "r"})
        for i in range(5):
            self.add(metadata={"user_id": "bob", "agent_id": "a", "run_id": "r"})
        self.add(metadata={"user_id": "alice", "agent_id": "other", "run_id": "r"})
        self.add(metadata={"user_id": "alice", "agent_id": "a", "run_id": "other"})
        self.add(metadata={"user_id": "alice", "agent_id": "a", "run_id": "r"}, bank_id="shared")
        for query in ("", "coffee", "offe"):
            rows = self.svc.search(SearchQuery(query=query, user_id="alice", agent_id="a", run_id="r", limit=1))
            self.assertEqual([m.id for m in rows], [target.id])
        self.assertEqual(self.svc.search(SearchQuery(query="coffee", user_id="missing")), [])

    def test_expired_records_cannot_starve_live_result(self):
        live = self.add()
        for i in range(4):
            self.add(expires_at="2000-01-01T00:00:00+08:00")
        for query in ("", "coffee", "offe"):
            self.assertEqual([m.id for m in self.svc.search(SearchQuery(query=query, limit=1))], [live.id])
        self.assertEqual(len(self.svc.search(SearchQuery(query="coffee", mode="historical"))), 5)

    def test_scope_is_not_sql_or_fts_syntax(self):
        self.add()
        self.assertEqual(self.svc.search(SearchQuery(query="coffee", user_id="' OR 1=1 --")), [])

    def test_bm25_prefers_focused_match_over_newer_long_record(self):
        focused = self.add("coffee")
        self.add("coffee " + "unrelated material " * 100)
        self.assertEqual(self.svc.search(SearchQuery(query="coffee", limit=1))[0].id, focused.id)

    def test_bad_expiry_fails_closed_but_history_is_preserved(self):
        record = self.add(expires_at="invalid-timestamp")
        self.assertEqual(self.svc.search(SearchQuery(query="coffee")), [])
        self.assertEqual(self.svc.search(SearchQuery(query="coffee", mode="historical"))[0].id, record.id)

    def test_api_and_mcp_preserve_scopes(self):
        target = self.add(metadata={"user_id": "alice"})
        self.add(metadata={"user_id": "bob"})
        with TestClient(self.ctx.app) as client:
            response = client.post("/api/v1/memories/search", json={"query": "coffee", "user_id": "alice"}, headers={"Authorization": "Bearer test-scoped-search"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(target.id, response.text)
            self.assertNotIn('"user_id":"bob"', response.text)
        out = _dispatch(self.svc, "memory_search", {"query": "coffee", "user_id": "alice"})
        self.assertEqual([m["id"] for m in out["items"]], [target.id])
