"""Task and learning loops. No network/model calls and no arbitrary code execution.

Executable schemas use explicit JSON equality conditions. Evaluation re-runs the
same decision engine on preregistered cases, never accepts a caller's pass flag.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import uuid

from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.cognition.loop_models import (
    CognitiveTask, ExecutableRule, LearningProposal, ProfileHypothesis, TaskFeedback, ValidationSuite,
)
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryType
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.verification.assessment import assess_evidence


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def _matches(conditions, context):
    return all(k in context and _json(context[k]) == _json(v) for k, v in conditions.items())


def _current(start, end):
    now = datetime.now(timezone.utc)
    try:
        return (not start or datetime.fromisoformat(start.replace("Z", "+00:00")) <= now) and (
            not end or now < datetime.fromisoformat(end.replace("Z", "+00:00")))
    except (ValueError, TypeError, AttributeError):
        return False


class CognitiveLoops:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.db.ensure_schema()
        self.claims = ClaimStore(self.db)
        self.evidence = EvidenceObjectStore(self.db)

    def _snapshot(self, task, overlay=None):
        """Complete dependency snapshot, including missing refs and counterevidence."""
        schemas, claim_ids, schema_windows = {}, set(), {}
        for action in task.actions:
            claim_ids.update(action.claim_ids)
            if action.schema_id:
                schema = (overlay or {}).get(action.schema_id) or self.service.get_cognitive_schema(action.schema_id)
                schemas[action.schema_id] = schema
                try:
                    rule = ExecutableRule.model_validate((schema or {}).get("executable_rule"))
                except ValueError:
                    rule = None
                if rule:
                    schema_windows[action.schema_id] = _current(rule.valid_from, rule.valid_until)
                    claim_ids.update(rule.required_claim_ids)
                    claim_ids.update(rule.counter_claim_ids)
        claims, evidences = {}, {}
        for cid in sorted(claim_ids):
            claim = self.claims.get(cid, follow_redirect=False)
            links = self.claims.list_evidence(cid)
            claims[cid] = {"claim": claim, "links": links,
                           "time_valid": bool(claim and _current(claim.get("validity_from"), claim.get("validity_to")))}
            refs = {x["evidence_ref"] for x in links}
            with self.db.exclusive() as conn:
                for row in conn.execute("SELECT evidence_refs_json FROM verification_records WHERE claim_id=?", (cid,)):
                    refs.update(json.loads(row[0]))
            for ref in refs:
                lineage = self.evidence.lineage(ref)
                for eid in set(lineage["evidence_ids"]) | {ref}:
                    ev = self.evidence.get(eid)
                    if ev:
                        ev = {**ev, "time_valid": _current(None, ev["metadata"].get("valid_until"))}
                    evidences[eid] = ev
        return {"schemas": schemas, "schema_windows": schema_windows, "claims": claims, "evidences": evidences}

    def _claim_ready(self, cid, task, snapshot):
        item = snapshot["claims"].get(cid) or {}
        claim = item.get("claim")
        if not claim or claim["bank_id"] != task.bank_id:
            return False, "claim_unavailable"
        if claim["lifecycle"] != "verified" or claim["verification_status"] != "verified":
            return False, "claim_not_verified"
        if not _current(claim.get("validity_from"), claim.get("validity_to")):
            return False, "claim_not_current"
        scope = {k: v for k, v in (claim.get("scope") or {}).items()
                 if k not in {"modality", "epistemic", "validity"}}
        if not _matches(scope, task.context):
            return False, "claim_context_mismatch"
        # Use the reviewed support set, not every unreviewed attached candidate.
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT evidence_refs_json FROM verification_records WHERE claim_id=? ORDER BY rowid DESC LIMIT 1", (cid,)).fetchone()
        refs = json.loads(row[0]) if row else []
        result = assess_evidence(self.db, claim, refs)
        return result["ok"], result.get("detail", "supported")

    def _decide(self, task, snapshot):
        steps, candidates, checks = 0, [], []
        for action in task.actions:
            if action.id not in task.allowed_actions:
                checks.append({"action": action.id, "reason": "action_not_allowed"})
                continue
            if not _matches(action.when, task.context):
                checks.append({"action": action.id, "reason": "context_mismatch"})
                continue
            refs = list(action.claim_ids)
            if action.schema_id:
                schema = snapshot["schemas"].get(action.schema_id)
                if not schema or schema.get("state") != "ACTIVE" or schema.get("bank_id", "personal") != task.bank_id:
                    checks.append({"action": action.id, "reason": "schema_not_active"})
                    continue
                try:
                    rule = ExecutableRule.model_validate(schema.get("executable_rule"))
                except ValueError:
                    checks.append({"action": action.id, "reason": "schema_not_executable"})
                    continue
                if not _current(rule.valid_from, rule.valid_until) or rule.action_id != action.id or not _matches(rule.when, task.context) or (
                    rule.invalid_when and _matches(rule.invalid_when, task.context)):
                    checks.append({"action": action.id, "reason": "schema_not_applicable"})
                    continue
                # Counterclaims are checked before allowing the shortcut/schema.
                counter_block = False
                for cid in rule.counter_claim_ids:
                    steps += 1
                    if steps > task.max_steps:
                        return self._unknown("budget_exhausted", task.max_steps, checks)
                    counter = (snapshot["claims"].get(cid) or {}).get("claim") or {}
                    if counter.get("lifecycle") in {"retracted", "expired", "rejected"}:
                        continue
                    ready, reason = self._claim_ready(cid, task, snapshot)
                    if ready or reason not in {"claim_not_current", "claim_context_mismatch"}:
                        counter_block = True  # unresolved counter is not refuted
                if counter_block:
                    checks.append({"action": action.id, "reason": "counterclaim_requires_review"})
                    continue
                refs += rule.required_claim_ids
            ready = True
            for cid in dict.fromkeys(refs):
                steps += 1
                if steps > task.max_steps:
                    return self._unknown("budget_exhausted", task.max_steps, checks)
                ok, reason = self._claim_ready(cid, task, snapshot)
                checks.append({"action": action.id, "claim_id": cid, "reason": reason})
                ready = ready and ok
            if ready:
                candidates.append(action)
        if not candidates:
            return self._unknown("insufficient_evidence_or_context", steps, checks)
        chosen = sorted(candidates, key=lambda a: (-a.priority, a.id))[0]
        return {"status": "selected", "selected_action": chosen.id, "steps": steps,
                "checks": checks, "task_success": None, "acceptance": task.acceptance,
                "score_kind": "task_priority_not_probability"}

    @staticmethod
    def _unknown(reason, steps, checks):
        return {"status": "unknown", "selected_action": None, "reason": reason,
                "steps": steps, "checks": checks, "task_success": None}

    def run(self, task: CognitiveTask):
        with self.db.transaction() as conn:
            snapshot = self._snapshot(task)
            key = _digest({"engine": "cognitive.loops.v1", "task": task.model_dump(), "dependencies": snapshot})
            # Snapshot includes time validity; no stale hit after expiry/start.
            row = conn.execute("SELECT result_json FROM cognitive_result_cache WHERE cache_key=?", (key,)).fetchone() if task.use_cache else None
            cached = json.loads(row[0]) if row else None
            hit = cached is not None
            fresh = cached if hit else self._decide(task, snapshot)
            result = {**fresh, "cache_hit": hit, "dependency_digest": _digest(snapshot)}
            if task.use_cache and not hit:
                conn.execute("INSERT OR REPLACE INTO cognitive_result_cache VALUES (?,?,?)", (key, _json(fresh), _now()))
                conn.execute("DELETE FROM cognitive_result_cache WHERE cache_key NOT IN (SELECT cache_key FROM cognitive_result_cache ORDER BY created_at DESC LIMIT 256)")
            rid = _id("ctr")
            conn.execute("INSERT INTO cognitive_task_runs VALUES (?,?,?,?,?)", (rid, task.bank_id, _json(task.model_dump()), _json(result), _now()))
        return {"ok": True, "run_id": rid, **result}

    def register_suite(self, suite: ValidationSuite):
        with self.db.transaction() as conn:
            payload = suite.model_dump()
            payload["dependencies"] = {case.id: self._snapshot(case.task) for case in suite.cases}
            sid = _id("cvs")
            digest = _digest(payload)
            conn.execute("INSERT INTO cognitive_validation_suites VALUES (?,?,?,?,?)", (sid, suite.bank_id, digest, _json(payload), _now()))
        return {"ok": True, "suite_id": sid, "digest": digest}

    def record_result(self, run_id: str, feedback: TaskFeedback):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT bank_id FROM cognitive_task_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return {"ok": False, "detail": "run_unavailable"}
            for ref in feedback.evidence_ids:
                ev = self.evidence.get(ref)
                if not ev or ev["bank_id"] != row["bank_id"] or not self.evidence.lineage(ref)["ok"]:
                    return {"ok": False, "detail": "feedback_evidence_unavailable"}
            payload = {**feedback.model_dump(), "attribution": "unresolved", "fact_confidence_changed": False}
            fid = _id("ctf")
            conn.execute("INSERT INTO cognitive_task_feedback VALUES (?,?,?,?)", (fid, run_id, _json(payload), _now()))
        return {"ok": True, "feedback_id": fid, **payload}

    def propose(self, proposal: LearningProposal):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM cognitive_validation_suites WHERE id=?", (proposal.suite_id,)).fetchone()
            schema = self.service.get_cognitive_schema(proposal.schema_id)
            if not row or not schema or schema.get("bank_id", "personal") != row["bank_id"]:
                return {"ok": False, "detail": "target_or_suite_unavailable"}
            suite = json.loads(row["payload_json"])
            if _digest(suite) != row["digest"]:
                return {"ok": False, "detail": "suite_integrity_mismatch"}
            if set(proposal.training_case_ids) & {c["id"] for c in suite["cases"]}:
                return {"ok": False, "detail": "training_validation_overlap"}
            if not any(a.get("schema_id") == proposal.schema_id for c in suite["cases"] for a in c["task"]["actions"]):
                return {"ok": False, "detail": "suite_does_not_exercise_target"}
            for cid in proposal.rule.required_claim_ids + proposal.rule.counter_claim_ids:
                claim = self.claims.get(cid, follow_redirect=False)
                if not claim or claim["bank_id"] != row["bank_id"]:
                    return {"ok": False, "detail": "cross_bank_or_missing_claim"}
            validation_roots = set()
            for dep in suite["dependencies"].values():
                validation_roots.update(ev["root_source_id"] or ev["source_ref"] for ev in dep["evidences"].values() if ev)
            for ref in proposal.training_evidence_ids:
                ev = self.evidence.get(ref)
                lineage = self.evidence.lineage(ref)
                if not ev or ev["bank_id"] != row["bank_id"] or not lineage["ok"]:
                    return {"ok": False, "detail": "training_lineage_unavailable"}
                if validation_roots & set(lineage["roots"]):
                    return {"ok": False, "detail": "training_validation_source_overlap"}
            uid = _id("clu")
            payload = {"proposal": proposal.model_dump(), "before": schema,
                       "suite_digest": row["digest"], "report": None}
            proposed = {**schema, "state": "ACTIVE", "executable_rule": proposal.rule.model_dump()}
            payload["candidate_dependencies"] = {c["id"]: self._snapshot(CognitiveTask.model_validate(c["task"]), {proposal.schema_id: proposed}) for c in suite["cases"]}
            conn.execute("INSERT INTO cognitive_learning_updates VALUES (?,?,?,?,?,?,?)", (uid, row["bank_id"], proposal.schema_id, proposal.suite_id, "proposed", _json(payload), _now()))
        return {"ok": True, "update_id": uid, "status": "proposed"}

    def get_update(self, update_id):
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM cognitive_learning_updates WHERE id=?", (update_id,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload_json"])} if row else None

    def _evaluate(self, update):
        payload = update["payload"]
        with self.db.exclusive() as conn:
            row = conn.execute("SELECT * FROM cognitive_validation_suites WHERE id=?", (update["suite_id"],)).fetchone()
        if not row or row["digest"] != payload["suite_digest"]:
            return {"ok": False, "detail": "suite_changed"}
        current = self.service.get_cognitive_schema(update["schema_id"])
        if current != payload["before"]:
            return {"ok": False, "detail": "target_changed"}
        suite = json.loads(row["payload_json"])
        if _digest(suite) != row["digest"]:
            return {"ok": False, "detail": "suite_integrity_mismatch"}
        proposed = {**current, "state": "ACTIVE", "executable_rule": payload["proposal"]["rule"]}
        results, gains, regressions = [], 0, 0
        for case in suite["cases"]:
            task = CognitiveTask.model_validate(case["task"])
            snapshot = self._snapshot(task)
            if snapshot != suite["dependencies"][case["id"]]:
                return {"ok": False, "detail": "validation_dependencies_changed"}
            baseline = self._decide(task, snapshot)
            candidate_snapshot = self._snapshot(task, {update["schema_id"]: proposed})
            if candidate_snapshot != payload["candidate_dependencies"][case["id"]]:
                return {"ok": False, "detail": "candidate_dependencies_changed"}
            candidate = self._decide(task, candidate_snapshot)
            if baseline.get("reason") == "budget_exhausted" or candidate.get("reason") == "budget_exhausted":
                return {"ok": False, "detail": "validation_budget_exhausted"}
            before = baseline["selected_action"] == case["expected_action"]
            after = candidate["selected_action"] == case["expected_action"]
            gains += int(after) - int(before) if case["partition"] == "heldout" else 0
            regressions += int(before and not after)
            results.append({"case_id": case["id"], "partition": case["partition"], "baseline_correct": before,
                            "candidate_correct": after, "baseline": baseline, "candidate": candidate})
        passed = gains >= suite["min_gain"] and regressions <= suite["max_regressions"]
        return {"ok": True, "passed": passed, "net_heldout_gain": gains, "regressions": regressions,
                "cases": results, "suite_digest": row["digest"], "method": "deterministic_preregistered_replay.v1"}

    def evaluate(self, update_id):
        with self.db.transaction() as conn:
            update = self.get_update(update_id)
            if not update or update["status"] not in {"proposed", "evaluated"}:
                return {"ok": False, "detail": "update_not_evaluable"}
            report = self._evaluate(update)
            payload = update["payload"]
            payload["report"] = report
            conn.execute("UPDATE cognitive_learning_updates SET status='evaluated',payload_json=? WHERE id=?", (_json(payload), update_id))
        return report

    def apply(self, update_id, *, confirm=False):
        from cognitive_memory.governance.schema_governance import authorize_schema_mutation
        with self.db.transaction() as conn:
            update = self.get_update(update_id)
            if not update or update["status"] != "evaluated":
                return {"ok": False, "detail": "evaluated_update_required"}
            gate = authorize_schema_mutation(self.db, confirm=confirm, operation="validated_learning")
            if not gate["ok"]:
                return gate
            report = self._evaluate(update)  # recompute, never trust stored/caller score
            if not report.get("passed"):
                return {"ok": False, "detail": "validation_failed", "report": report}
            schema = self.service.update_cognitive_schema(update["schema_id"],
                patch={"executable_rule": update["payload"]["proposal"]["rule"], "validation": report},
                state="ACTIVE", change_type="VALIDATED_LEARNING", reason=update["payload"]["proposal"]["reason"])
            payload = update["payload"]
            payload.update(report=report, applied_schema=schema)
            conn.execute("UPDATE cognitive_learning_updates SET status='applied',payload_json=? WHERE id=?", (_json(payload), update_id))
        return {"ok": True, "schema": schema, "report": report}

    def rollback(self, update_id, *, confirm=False):
        from cognitive_memory.governance.schema_governance import authorize_schema_mutation
        with self.db.transaction() as conn:
            update = self.get_update(update_id)
            if not update or update["status"] != "applied":
                return {"ok": False, "detail": "applied_update_required"}
            gate = authorize_schema_mutation(self.db, confirm=confirm, operation="learning_rollback")
            if not gate["ok"]:
                return gate
            payload = update["payload"]
            if self.service.get_cognitive_schema(update["schema_id"]) != payload["applied_schema"]:
                return {"ok": False, "detail": "target_changed"}
            before = payload["before"]
            restored = self.service.update_cognitive_schema(update["schema_id"],
                patch={"executable_rule": before.get("executable_rule"), "validation": before.get("validation")},
                state=before["state"], change_type="LEARNING_ROLLBACK", reason=update_id)
            conn.execute("UPDATE cognitive_learning_updates SET status='rolled_back' WHERE id=?", (update_id,))
        return {"ok": True, "schema": restored}

    def record_profile(self, profile: ProfileHypothesis):
        for ref in profile.evidence_ids:
            ev = self.evidence.get(ref)
            if not ev or ev["bank_id"] != profile.bank_id or not self.evidence.lineage(ref)["ok"]:
                return {"ok": False, "detail": "profile_evidence_unavailable"}
        if profile.expires_at and not _current(None, profile.expires_at):
            return {"ok": False, "detail": "invalid_profile_expiry"}
        memory = self.service.propose_memory(MemoryProposeIn(
            type=MemoryType.SEMANTIC, status=MemoryStatus.CANDIDATE, content=profile.statement,
            bank_id=profile.bank_id, expires_at=profile.expires_at,
            metadata={"epistemic": "inference", "profile": profile.model_dump(),
                      "allowed_effects": ["expression", "attention"], "confidence_kind": "uncalibrated"}))
        return {"ok": True, "memory_id": memory.id, "status": "hypothesis", "grants_changed": False}
