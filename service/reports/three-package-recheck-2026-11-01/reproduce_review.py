"""Isolated review counterexamples. Does not modify application source or user Banks."""
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastapi.testclient import TestClient
from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.domain.models import MemoryProposeIn
from cognitive_memory.retrieval.pipeline import run_retrieve


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="review-only-", dir=HERE, ignore_cleanup_errors=True) as directory:
        assert Path(directory).resolve().is_relative_to(HERE)
        ctx = build_context(data_dir=directory, token="synthetic-review-only")
        try:
            store = ClaimStore(ctx.db)
            client = TestClient(ctx.app)
            admin = {"Authorization": "Bearer synthetic-review-only"}
            store.propose_from_text("A喜欢咖啡")
            response = client.post("/api/v1/cognition/answers/select", headers=admin,
                                   json={"target": {"subject_id": "A", "predicate": "喜欢", "arguments": ["茶"]}})
            chosen = response.json()
            results.append({"case": "target_arguments_ignored", "http": response.status_code,
                            "requested_arguments": ["茶"], "status": chosen.get("status"),
                            "selected": [{k: x.get(k) for k in ("surface", "arguments", "lifecycle")} for x in chosen.get("selected", [])]})

            claim = store.propose_from_text("B喜欢咖啡")
            transition = store.transition(claim["claim_id"], "rejected", reason="review-rejected")
            assert transition.get("ok"), transition
            chosen = client.post("/api/v1/cognition/answers/select", headers=admin,
                                 json={"target": {"subject_id": "B", "predicate": "喜欢"}}).json()
            results.append({"case": "rejected_claim_chosen", "status": chosen.get("status"),
                            "selected": [{k: x.get(k) for k in ("surface", "lifecycle", "epistemic_tag")} for x in chosen.get("selected", [])]})

            cid = "review-shared-checkpoint-id"
            first = client.post("/api/v1/cognition/sessions/checkpoints", headers=admin,
                                json={"checkpoint_id": cid, "session_id": "project-session", "bank_id": "projects", "goal": "original-project-goal"})
            assert first.status_code == 200, first.text
            register = client.post("/api/v1/agents/register", headers=admin,
                                   json={"agent_id": "agent:review-limited", "display_name": "synthetic review"})
            assert register.status_code == 200, register.text
            grant = client.post("/api/v1/agents/agent:review-limited/grants", headers=admin,
                                json={"permissions": ["memory.read", "memory.write"], "banks": ["personal"]})
            assert grant.status_code == 200, grant.text
            limited = {"X-Agent-Id": "agent:review-limited", "X-Agent-Token": grant.json()["agent_token"]}
            overwrite = client.post("/api/v1/cognition/sessions/checkpoints", headers=limited,
                                    json={"checkpoint_id": cid, "session_id": "personal-session", "bank_id": "personal", "goal": "overwritten-by-personal-only-agent"})
            after = client.get("/api/v1/cognition/sessions/checkpoints/" + cid, headers=admin).json()
            results.append({"case": "cross_bank_checkpoint_overwrite", "agent_granted_banks": ["personal"],
                            "overwrite_http": overwrite.status_code, "stored_row_bank": after.get("bank_id"),
                            "stored_goal": after.get("manifest", {}).get("goal"),
                            "manifest_bank": after.get("manifest", {}).get("bank_id")})

            budget = client.post("/api/v1/contexts/build", headers=admin,
                                 json={"query": "review", "token_budget": 256, "has_accurate_tokenizer": True}).json()
            results.append({"case": "client_flag_makes_heuristic_measured", "measurement": budget.get("measurement"),
                            "hard_limit_verified": budget.get("hard_limit_verified")})

            memory = ctx.service.propose_memory(MemoryProposeIn(content="reviewkeyword expired record",
                                                metadata={"validity_to": "2000-01-01T00:00:00Z"}))
            retrieval = run_retrieve(ctx.service, query="reviewkeyword", force=True, semantic_assist=False)
            results.append({"case": "expired_fusion_result_restored", "status": retrieval.get("status"),
                            "expired_id": memory.id, "returned_ids": [x["id"] for x in retrieval.get("items", [])],
                            "fusion": retrieval.get("fusion") or retrieval.get("trace", {}).get("fusion")})
        finally:
            ctx.db.close()
    (HERE / "counterexamples-python.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
