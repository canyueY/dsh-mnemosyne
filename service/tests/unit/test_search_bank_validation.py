import tempfile
import unittest

from fastapi.testclient import TestClient
from cognitive_memory.bootstrap import build_context
from cognitive_memory.identity import issue_grant, register_agent
from cognitive_memory.memory.banks import resolve_search_banks, UnknownMemoryBankError


class SearchBankValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.ctx = build_context(data_dir=self.tmp.name, token="fixture-node-token")
        self.http = TestClient(self.ctx.app)
        self.admin = {"Authorization": "Bearer fixture-node-token"}
        register_agent(self.ctx.db, agent_id="agent:fixture")
        grant = issue_grant(self.ctx.db, "agent:fixture", permissions=["memory.read"], banks=["personal"])
        self.agent_token = grant["agent_token"]
        self.agent_headers = {"X-Agent-Id": "agent:fixture", "X-Agent-Token": self.agent_token}

    def tearDown(self):
        self.http.close()
        self.ctx.db.close()
        self.tmp.cleanup()

    def test_bank_resolver_defaults_and_explicit_invalid(self):
        self.assertEqual(resolve_search_banks(None), ["personal"])
        self.assertEqual(resolve_search_banks([]), ["personal"])
        self.assertEqual(resolve_search_banks([" SHARED ", "shared"]), ["shared"])
        for value in ([""], ["missing"], ["public", "missing"]):
            with self.subTest(value=value), self.assertRaises(UnknownMemoryBankError):
                resolve_search_banks(value)

    def test_native_and_compat_node_auth_default_and_invalid(self):
        for url, field in (("/api/v1/memories/search", "banks"), ("/api/v1/compat/mem0/search", "bank_id")):
            self.assertEqual(self.http.post(url, headers=self.admin, json={"query": "fixture"}).status_code, 200)
            invalid = ["missing"] if field == "banks" else "missing"
            result = self.http.post(url, headers=self.admin, json={"query": "fixture", field: invalid})
            self.assertEqual(result.status_code, 400, result.text)
            self.assertEqual(result.json()["code"], "unknown_memory_bank")
            blank = [""] if field == "banks" else ""
            result = self.http.post(url, headers=self.admin, json={"query": "fixture", field: blank})
            self.assertEqual(result.status_code, 400, result.text)

    def test_agent_authorized_and_denied_remain_enforced(self):
        url = "/api/v1/memories/search"
        self.assertEqual(self.http.post(url, headers=self.agent_headers, json={"query": "fixture", "banks": ["personal"]}).status_code, 200)
        self.assertEqual(self.http.post(url, headers=self.agent_headers, json={"query": "fixture", "banks": ["shared"]}).status_code, 403)
        self.assertEqual(self.http.post(url, headers=self.agent_headers, json={"query": "fixture", "banks": ["missing"]}).status_code, 403)
        compat = "/api/v1/compat/mem0/search"
        self.assertEqual(self.http.post(compat, headers=self.agent_headers, json={"query": "fixture"}).status_code, 200)
        self.assertIn(self.http.post(compat, headers=self.agent_headers, json={"query": "fixture", "bank_id": "shared"}).status_code, (403, 404))
        act = "/api/v1/agents/act/search"
        body = {"agent_id": "agent:fixture", "agent_token": self.agent_token, "query": "fixture"}
        self.assertEqual(self.http.post(act, json=body).status_code, 200)
        self.assertEqual(self.http.post(act, json={**body, "bank": "shared"}).status_code, 403)
        self.assertEqual(self.http.post(act, json={**body, "bank": "missing"}).status_code, 403)
        self.assertEqual(self.http.post(url, json={"query": "fixture"}).status_code, 401)

if __name__ == "__main__":
    unittest.main()