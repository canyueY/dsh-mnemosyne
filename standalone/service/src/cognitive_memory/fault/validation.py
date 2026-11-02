"""Execute reproduction, repair replay and regression against isolated databases."""
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from cognitive_memory.storage.db import Database
from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.fault.invariants import check_claim_invariants
from cognitive_memory.fault.repair import repair_claim_invariant


def snapshot_database(db):
    snapshot=sqlite3.connect(":memory:")
    with db.exclusive() as conn:
        conn.backup(snapshot)
    return snapshot


@contextmanager
def clone_snapshot(snapshot):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        db=Database(Path(root)/"replay.db")
        try:
            snapshot.backup(db.connect())
            db.ensure_schema()
            yield db
        finally:
            db.close()


def reproduce(snapshot, claim_id, code):
    with clone_snapshot(snapshot) as db:
        store=ClaimStore(db)
        result=check_claim_invariants(store.get(claim_id,follow_redirect=False),
                                      evidence_n=len(store.list_evidence(claim_id)))
        return {"executed":True,"ok":code in {v["code"] for v in result["violations"]},
                "observed":result,"isolation":"scratch_sqlite_snapshot"}


def replay_repair(snapshot,claim_id,code,expected):
    with clone_snapshot(snapshot) as db:
        result=repair_claim_invariant(db,claim_id,code=code)
        actual=ClaimStore(db).get(claim_id,follow_redirect=False)
        fields=("lifecycle","verification_status","redirects_to")
        differences=[k for k in fields if (actual or {}).get(k)!=expected.get(k)]
        return {"executed":True,"ok":bool(result.get("ok")) and not differences,
                "differences":differences,"repair":result,"algorithm":"known_invariant_repair.v1",
                "scope":"snapshot repair replay; not arbitrary Observation/LLM replay"}


def regression_suite():
    from cognitive_memory.fault.inject import INJECTORS
    results=[]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        db=Database(Path(root)/"regression.db")
        try:
            store=ClaimStore(db)
            for index,(code,inject) in enumerate(INJECTORS.items()):
                cid=store.propose_from_text(f"我喜欢回归测试{index}")["claim_id"]
                kwargs={"claim_id":cid}
                if code=="redirect_without_tombstone":
                    kwargs["redirects_to"]=store.propose_from_text("我喜欢回归目标")["claim_id"]
                inject(db,**kwargs)
                initial=check_claim_invariants(store.get(cid,follow_redirect=False),
                                               evidence_n=len(store.list_evidence(cid)))
                result=repair_claim_invariant(db,cid,code=code)
                final=check_claim_invariants(store.get(cid,follow_redirect=False),
                                             evidence_n=len(store.list_evidence(cid)))
                results.append({"case":code,"passed":not initial["ok"] and bool(result.get("ok")) and final["ok"]})
        finally:
            db.close()
    return {"executed":True,"passed":all(r["passed"] for r in results),"cases":results}
