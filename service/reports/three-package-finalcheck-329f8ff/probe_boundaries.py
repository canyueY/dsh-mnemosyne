import json, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'src'))
from fastapi.testclient import TestClient
from cognitive_memory.bootstrap import build_context
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.domain.models import MemoryProposeIn
out=[]
with tempfile.TemporaryDirectory(prefix='bounds-',dir=HERE,ignore_cleanup_errors=True) as temp:
    ctx=build_context(data_dir=temp,token='isolated-bounds')
    try:
        store=ClaimStore(ctx.db)
        client=TestClient(ctx.app)
        admin={'Authorization':'Bearer isolated-bounds'}
        claim=store.propose_from_text('D喜欢咖啡')
        ev=EvidenceObjectStore(ctx.db).create(source_type='doc',source_ref='review-only-counter-source',content_for_fingerprint='D does not like coffee',role_hint='counter')['evidence']
        attached=store.attach_evidence(claim['claim_id'],ev['id'],role='counter')
        result=client.post('/api/v1/cognition/answers/select',headers=admin,json={'target':{'subject_id':'D','predicate':'喜欢','arguments':['咖啡']}}).json()
        out.append(dict(case='counter_only_source',attached=attached,links=store.list_evidence(claim['claim_id']),result=result))
        missing=store.propose_from_text('E喜欢咖啡')
        ev2=EvidenceObjectStore(ctx.db).create(source_type='unknown',source_ref='',content_for_fingerprint='unknown origin')['evidence']
        store.attach_evidence(missing['claim_id'],ev2['id'],role='support')
        result2=client.post('/api/v1/cognition/answers/select',headers=admin,json={'target':{'subject_id':'E','predicate':'喜欢','arguments':['咖啡']}}).json()
        out.append(dict(case='unknown_provenance_object',evidence=ev2,result=result2))
    finally:
        ctx.db.close()
(HERE/'boundary-python.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps([dict(case=x['case'],status=x['result']['status'],checks_complete=x['result']['checks_complete'],counterevidence_refs=x['result']['counterevidence_refs']) for x in out],ensure_ascii=False,indent=2))
