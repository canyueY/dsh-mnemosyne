import json, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'src'))
from fastapi.testclient import TestClient
from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.domain.models import MemoryProposeIn, ObservationIn, SourceKind
from cognitive_memory.retrieval.pipeline import run_retrieve
from cognitive_memory.jobs.dispatcher import BackgroundRuntime

results = []
with tempfile.TemporaryDirectory(prefix='remaining-', dir=HERE, ignore_cleanup_errors=True) as directory:
    ctx = build_context(data_dir=directory, token='isolated-recheck')
    client = TestClient(ctx.app)
    auth = {'Authorization': 'Bearer isolated-recheck'}
    store = ClaimStore(ctx.db)
    try:
        for envelope in [{'accurate_tokenizer': True}, {'accurate_tokenizer': True, 'measurement': 'measured'}]:
            response = client.post('/api/v1/contexts/build', headers=auth, json={'query':'probe', 'token_budget':256, 'envelope':envelope})
            body = response.json()
            results.append(dict(case='envelope_tokenizer_bypass', http=response.status_code, request_envelope=envelope, measurement=body.get('measurement'), hard_limit_verified=body.get('hard_limit_verified')))
        claim = store.propose_from_text('C喜欢咖啡')
        packed = store.get(claim['claim_id'])
        answer = client.post('/api/v1/cognition/answers/select', headers=auth, json={'target':{'subject_id':'C','predicate':'喜欢','arguments':['咖啡']}}).json()
        results.append(dict(case='unverified_sourceless_claim', stored={k:packed.get(k) for k in ['lifecycle','verification_status','source_memory_id']}, result=answer))
        safe = ctx.service.propose_memory(MemoryProposeIn(content='mixedreview active safe record'))
        expired = ctx.service.propose_memory(MemoryProposeIn(content='mixedreview expired unsafe record', metadata={'validity_to':'2000-01-01T00:00:00Z'}))
        retrieval = run_retrieve(ctx.service, query='mixedreview', force=True, semantic_assist=False)
        results.append(dict(case='mixed_filtered_context', safe_id=safe.id, expired_id=expired.id, item_ids=[x['id'] for x in retrieval.get('items',[])], fragment_ids=[x['id'] for x in retrieval.get('fragments',[])], brief=retrieval.get('brief')))
        # Inject takeover immediately before the real consolidation calls the real
        # MemoryService.propose_memory. No fake consolidation or synthetic SQL write.
        observation = ctx.service.capture_observation(ObservationIn(content='actual stale worker observation', source_kind=SourceKind.SYSTEM, producer='review-only'))
        original = ctx.service.propose_memory
        runtime = BackgroundRuntime(ctx.db, ctx.bus, ctx.service)
        lease_change = []
        written = []
        def interleaved_propose(body, **kwargs):
            lease_change.append(runtime.leases.acquire(worker_id='replacement-worker', resource='consolidation'))
            memory = original(body, **kwargs)
            written.append(memory.id)
            return memory
        ctx.service.propose_memory = interleaved_propose
        try:
            runtime.run_tasks_once()
            with ctx.db.exclusive() as conn:
                tasks = [dict(row) for row in conn.execute('SELECT id, task_type, status, last_error FROM tasks').fetchall()]
            results.append(dict(case='stale_worker_real_write', observation_id=observation.id, replacement_acquired=bool(lease_change), written_memories=[ctx.service.get_memory(mid) for mid in written], tasks=tasks))
        finally:
            ctx.service.propose_memory = original
    finally:
        ctx.db.close()
(HERE / 'remaining-python.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps(results, ensure_ascii=False, indent=2))
