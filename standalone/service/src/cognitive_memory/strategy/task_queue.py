"""Durable MetaController scheduling on the existing task foundation."""
import hashlib
import json
import time
from datetime import datetime,timezone
from cognitive_memory.tracing.service import JudgmentTraceService


def stamp(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec="microseconds")


class MetaTaskQueue:
    def __init__(self,db):
        self.db=db
        db.ensure_schema()

    def submit(self,*,trigger,target_refs=None,action="reflect",priority=0,
               deduplication_key=None,budget_ms=500,cooldown_seconds=60,max_retries=3,now=None):
        if action not in {"reflect","rank_candidates","reevaluate"}:
            raise ValueError("unsupported_meta_action")
        refs=list(dict.fromkeys(target_refs or []))
        if len(refs)>100 or not 1<=budget_ms<=5000 or not 0<=priority<=100 or not 1<=max_retries<=5 or cooldown_seconds<0:
            raise ValueError("invalid_meta_budget")
        now=time.time() if now is None else now
        key=deduplication_key or json.dumps([trigger,refs,action],sort_keys=True)
        tid="meta_"+hashlib.sha256(key.encode()).hexdigest()
        with self.db.transaction() as conn:
            row=conn.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
            if row:
                previous=json.loads(row["payload_json"])
                if row["status"] in {"pending","running"} or previous.get("cooldown_until",0)>now:
                    return {"ok":True,"task_id":tid,"deduplicated":True,"status":row["status"]}
            payload={"trigger":trigger,"target_refs":refs,"action":action,"priority":priority,
                     "budget_ms":budget_ms,"max_retries":max_retries,"cooldown_seconds":cooldown_seconds,
                     "cooldown_until":0,"submitted_at":now,"result":None}
            conn.execute(
                """INSERT INTO tasks(id,task_type,status,payload_json,attempts,last_error,created_at,updated_at)
                VALUES (?,'cognitive.meta','pending',?,0,'',?,?)
                ON CONFLICT(id) DO UPDATE SET status='pending',payload_json=excluded.payload_json,
                  attempts=0,last_error='',created_at=excluded.created_at,updated_at=excluded.updated_at,next_attempt_at=NULL""",
                (tid,json.dumps(payload),stamp(now),stamp(now)),
            )
        return {"ok":True,"task_id":tid,"deduplicated":False,"status":"pending"}

    def get(self,tid):
        with self.db.exclusive() as conn:
            row=conn.execute("SELECT * FROM tasks WHERE id=? AND task_type='cognitive.meta'",(tid,)).fetchone()
        return {**dict(row),"payload":json.loads(row["payload_json"])} if row else None

    def run_once(self,*,now=None,handler=None):
        from cognitive_memory.strategy.metacontroller import schedule
        now=time.time() if now is None else now
        with self.db.transaction() as conn:
            stale=conn.execute("SELECT * FROM tasks WHERE task_type='cognitive.meta' AND status='running' AND next_attempt_at<=?",(stamp(now),)).fetchall()
            for row in stale:
                p=json.loads(row["payload_json"])
                state="dead" if row["attempts"]>=p["max_retries"] else "pending"
                conn.execute("UPDATE tasks SET status=?,last_error='lease_expired' WHERE id=?",(state,row["id"]))
            rows=conn.execute("SELECT * FROM tasks WHERE task_type='cognitive.meta' AND status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)",(stamp(now),)).fetchall()
            if not rows:
                return {"ok":True,"executed":False}
            # Aging means even a low-priority task eventually outranks new arrivals.
            chosen=max(rows,key=lambda r:json.loads(r["payload_json"])["priority"]+
                       (now-json.loads(r["payload_json"])["submitted_at"])/30)
            p=json.loads(chosen["payload_json"]);tid=chosen["id"]
            conn.execute("UPDATE tasks SET status='running',attempts=attempts+1,updated_at=?,next_attempt_at=? WHERE id=?",
                         (stamp(now),stamp(now+max(1,p["budget_ms"]/1000)+1),tid))
        start=time.perf_counter();results=[];status="done";error=""
        try:
            for ref in p["target_refs"] or [None]:
                if (time.perf_counter()-start)*1000>=p["budget_ms"]:
                    status="budget_exceeded";break
                result=handler(ref,p) if handler else schedule(self.db,action=p["action"],strategy_id=ref)
                if not result.get("ok"):
                    raise RuntimeError(result.get("detail","meta_task_failed"))
                results.append(result)
            if (time.perf_counter()-start)*1000>p["budget_ms"]:
                status="budget_exceeded"
        except Exception as exc:
            error=str(exc)[:500]
            status="dead" if chosen["attempts"]+1>=p["max_retries"] else "pending"
        p["result"]=results;p["cooldown_until"]=now+p["cooldown_seconds"]
        with self.db.transaction() as conn:
            updated=conn.execute("UPDATE tasks SET status=?,payload_json=?,last_error=?,updated_at=?,next_attempt_at=? WHERE id=? AND status='running' AND attempts=?",
                         (status,json.dumps(p),error,stamp(now),
                          stamp(now+2**(chosen["attempts"]+1)) if status=="pending" else None,tid,chosen["attempts"]+1))
            if updated.rowcount != 1:
                return {"ok":False,"executed":True,"task_id":tid,"status":"lease_lost"}
            JudgmentTraceService(self.db).record("meta.task",input_refs=[tid],
                                                 reason_codes=[status],governance_critical=True)
        return {"ok":status=="done","executed":True,"task_id":tid,"status":status,"results":results}
