# -*- coding: utf-8 -*-
"""Governed metacognition persistence. Hypotheses never enter Knowledge/retrieve."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.metacognition.rubrics import DIMENSIONS, SEED_RUBRIC, evaluate_with_rubric
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return {} if default is None else default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {} if default is None else default


class MetacogError(Exception):
    def __init__(self, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra

    def as_detail(self) -> dict[str, Any]:
        d = {"code": self.code, "message": self.message}
        d.update(self.extra)
        return d


class MetacogStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()
        self._seed_rubric()

    def _seed_rubric(self) -> None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM meta_rubrics WHERE rubric_id=? AND version=?",
                (SEED_RUBRIC["rubric_id"], SEED_RUBRIC["version"]),
            ).fetchone()
            if row:
                return
            conn.execute(
                """
                INSERT INTO meta_rubrics(
                  id, rubric_id, version, body_json, created_by, status, audit_ref, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    _id("rub"),
                    SEED_RUBRIC["rubric_id"],
                    SEED_RUBRIC["version"],
                    _dumps(SEED_RUBRIC),
                    "system",
                    "active",
                    SEED_RUBRIC["audit_ref"],
                    _now(),
                ),
            )

    def list_rubrics(self) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_rubrics ORDER BY rubric_id, version"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = _loads(d.get("body_json"))
            body["id"] = d["id"]
            body["rubric_id"] = d.get("rubric_id")
            body["version"] = d.get("version")
            body["status"] = d.get("status")
            out.append(body)
        return out

    def list_external_bundles(self, *, include_expired: bool = False) -> list[dict[str, Any]]:
        if not include_expired:
            self.expire_external_bundles()
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, body_json, created_at FROM meta_external_bundles ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            body = _loads(d.get("body_json"))
            body["id"] = d["id"]
            body["created_at"] = d.get("created_at")
            items.append(body)
        return items

    def is_revoked(self, actor_id: str) -> bool:
        if not actor_id:
            return False
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM meta_revocations WHERE actor_id=? LIMIT 1",
                (actor_id,),
            ).fetchone()
        return bool(row)

    def revoke(self, actor_id: str, *, by: str, reason: str) -> dict[str, Any]:
        rid = _id("rev")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO meta_revocations(id, actor_id, revoked_by, reason, created_at) VALUES (?,?,?,?,?)",
                (rid, actor_id, by, reason, now),
            )
        return {"ok": True, "id": rid, "actor_id": actor_id, "created_at": now}

    # ---- Experience / Trace / Outcome ----

    def create_experience(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        eid = _id("exp")
        now = _now()
        bank = str(body.get("bank_id") or "personal")
        outcome_status = body.get("outcome_status") or "unknown"
        row = {
            "id": eid,
            "goal": body.get("goal") or "",
            "request_id": body.get("request_id") or "",
            "session_id": body.get("session_id") or "",
            "principal_id": body.get("principal_id") or actor,
            "bank_id": bank,
            "scope": body.get("scope") or "unknown",
            "memory_snapshot_id": body.get("memory_snapshot_id"),
            "strategy_snapshot_id": body.get("strategy_snapshot_id"),
            "self_model_snapshot_id": body.get("self_model_snapshot_id"),
            "selected_strategy_ids": list(body.get("selected_strategy_ids") or []),
            "context_refs": list(body.get("context_refs") or []),
            "evidence_refs": list(body.get("evidence_refs") or []),
            "tool_calls": list(body.get("tool_calls") or []),
            "actions": list(body.get("actions") or []),
            "result": body.get("result"),
            "outcome_status": outcome_status,
            "user_feedback_refs": list(body.get("user_feedback_refs") or []),
            "latency_ms": body.get("latency_ms"),
            "token_cost": body.get("token_cost"),
            "resource_cost": body.get("resource_cost"),
            "uncertainty": body.get("uncertainty"),
            "created_by": actor,
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_experiences(
                  id, goal, request_id, session_id, principal_id, bank_id, scope,
                  memory_snapshot_id, strategy_snapshot_id, self_model_snapshot_id,
                  selected_strategy_ids_json, context_refs_json, evidence_refs_json,
                  tool_calls_json, actions_json, result_json, outcome_status,
                  user_feedback_refs_json, latency_ms, token_cost, resource_cost,
                  uncertainty_json, created_by, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    eid, row["goal"], row["request_id"], row["session_id"], row["principal_id"],
                    bank, row["scope"], row["memory_snapshot_id"], row["strategy_snapshot_id"],
                    row["self_model_snapshot_id"], _dumps(row["selected_strategy_ids"]),
                    _dumps(row["context_refs"]), _dumps(row["evidence_refs"]),
                    _dumps(row["tool_calls"]), _dumps(row["actions"]), _dumps(row["result"]),
                    outcome_status, _dumps(row["user_feedback_refs"]),
                    row["latency_ms"], row["token_cost"], row["resource_cost"],
                    _dumps(row["uncertainty"]), actor, now,
                ),
            )
        return {"ok": True, "experience": row, "note": "Experience is a record, not fact certification"}

    def get_experience(self, experience_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM meta_experiences WHERE id=?", (experience_id,)).fetchone()
        return self._exp_row(row) if row else None

    def list_experiences(self, *, bank_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 500))
        with self.db.transaction() as conn:
            if bank_id:
                rows = conn.execute(
                    "SELECT * FROM meta_experiences WHERE bank_id=? ORDER BY created_at DESC LIMIT ?",
                    (bank_id, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM meta_experiences ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [self._exp_row(r) for r in rows]

    @staticmethod
    def _exp_row(row: Any) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "experience_id": d["id"],
            "goal": d.get("goal") or "",
            "request_id": d.get("request_id") or "",
            "session_id": d.get("session_id") or "",
            "principal_id": d.get("principal_id"),
            "bank_id": d.get("bank_id"),
            "scope": d.get("scope") or "unknown",
            "memory_snapshot_id": d.get("memory_snapshot_id"),
            "strategy_snapshot_id": d.get("strategy_snapshot_id"),
            "self_model_snapshot_id": d.get("self_model_snapshot_id"),
            "selected_strategy_ids": _loads(d.get("selected_strategy_ids_json"), []),
            "context_refs": _loads(d.get("context_refs_json"), []),
            "evidence_refs": _loads(d.get("evidence_refs_json"), []),
            "tool_calls": _loads(d.get("tool_calls_json"), []),
            "actions": _loads(d.get("actions_json"), []),
            "result": _loads(d.get("result_json")),
            "outcome_status": d.get("outcome_status") or "unknown",
            "user_feedback_refs": _loads(d.get("user_feedback_refs_json"), []),
            "latency_ms": d.get("latency_ms"),
            "token_cost": d.get("token_cost"),
            "resource_cost": d.get("resource_cost"),
            "uncertainty": _loads(d.get("uncertainty_json")),
            "created_by": d.get("created_by"),
            "created_at": d.get("created_at"),
        }

    def add_trace(self, experience_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        exp = self.get_experience(experience_id)
        if not exp:
            raise MetacogError("experience_not_found")
        tid = _id("trc")
        now = _now()
        summary = {
            "goal": body.get("goal") or exp.get("goal") or "",
            "data_used": list(body.get("data_used") or []),
            "tools_used": list(body.get("tools_used") or []),
            "assumptions": list(body.get("assumptions") or []),
            "selection_rationale": body.get("selection_rationale") or "",
            "alternatives_considered": list(body.get("alternatives_considered") or []),
            "uncertainties": list(body.get("uncertainties") or []),
            "permission_checks": list(body.get("permission_checks") or []),
            "final_action": body.get("final_action") or "",
            "audit_ref": body.get("audit_ref") or "",
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_traces(id, experience_id, summary_json, created_by, created_at)
                VALUES (?,?,?,?,?)
                """,
                (tid, experience_id, _dumps(summary), actor, now),
            )
        return {"ok": True, "trace": {"id": tid, "experience_id": experience_id, **summary, "created_at": now}}

    def add_outcome(self, experience_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        exp = self.get_experience(experience_id)
        if not exp:
            raise MetacogError("experience_not_found")
        oid = _id("out")
        now = _now()
        tool_failed = bool(body.get("tool_failed") or body.get("failure_code"))
        status = str(body.get("outcome_status") or body.get("status") or "unknown")
        if tool_failed and status == "succeeded":
            raise MetacogError("tool_failure_not_success", "tool failure cannot be recorded as succeeded")
        feedback = str(body.get("user_feedback_state") or "").strip().lower()
        if feedback in {"", "silence", "silent", "none", "missing"}:
            feedback = "unknown"
        if feedback == "success" and not body.get("user_feedback_explicit"):
            feedback = "unknown"
        row = {
            "id": oid,
            "experience_id": experience_id,
            "goal_completed": body.get("goal_completed"),
            "tool_returns": body.get("tool_returns"),
            "failure_code": body.get("failure_code"),
            "user_feedback_state": feedback,
            "user_feedback_explicit": bool(body.get("user_feedback_explicit")),
            "followup_verification": body.get("followup_verification"),
            "needs_review": bool(body.get("needs_review")),
            "expected_delta": body.get("expected_delta"),
            "outcome_status": status if status in {
                "succeeded", "partially_succeeded", "failed", "abstained", "clarified", "unknown"
            } else "unknown",
            "created_by": actor,
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_outcomes(
                  id, experience_id, body_json, user_feedback_state, outcome_status, created_by, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (oid, experience_id, _dumps(row), feedback, row["outcome_status"], actor, now),
            )
            conn.execute(
                "UPDATE meta_experiences SET outcome_status=? WHERE id=?",
                (row["outcome_status"], experience_id),
            )
        return {"ok": True, "outcome": row}

    def get_latest_outcome(self, experience_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM meta_outcomes WHERE experience_id=? ORDER BY created_at DESC LIMIT 1",
                (experience_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        body = _loads(d.get("body_json"))
        body["id"] = d["id"]
        body["user_feedback_state"] = d.get("user_feedback_state") or "unknown"
        body["outcome_status"] = d.get("outcome_status") or "unknown"
        return body

    # ---- Evaluation ----

    def create_evaluation(self, body: dict[str, Any], *, actor: str, evaluator_failed: bool = False) -> dict[str, Any]:
        eid = str(body.get("experience_id") or "")
        exp = self.get_experience(eid)
        if not exp:
            raise MetacogError("experience_not_found")
        supporting = list(body.get("supporting_evidence_ids") or [])
        counter = list(body.get("counter_evidence_ids") or [])
        if body.get("use_summary_as_evidence"):
            raise MetacogError("summary_not_evidence", "a summary cannot be its own evidence")
        outcome = self.get_latest_outcome(eid)
        fb = str(body.get("user_feedback_state") or (outcome or {}).get("user_feedback_state") or "unknown")
        judged = evaluate_with_rubric(
            scores=body.get("scores"),
            supporting_evidence_ids=supporting,
            counter_evidence_ids=counter,
            user_feedback_state=fb,
            outcome_status=(outcome or {}).get("outcome_status") or exp.get("outcome_status"),
            evaluator_failed=evaluator_failed,
        )
        vid = _id("evl")
        now = _now()
        row = {
            "id": vid,
            "evaluation_id": vid,
            "experience_id": eid,
            "evaluator_id": body.get("evaluator_id") or actor,
            "evaluator_kind": body.get("evaluator_kind") or "system",
            "rubric_id": body.get("rubric_id") or SEED_RUBRIC["rubric_id"],
            "rubric_version": int(body.get("rubric_version") or SEED_RUBRIC["version"]),
            "scores": judged["scores"],
            "correctness": judged["scores"].get("factual_correctness"),
            "completeness": judged["scores"].get("source_completeness"),
            "provenance_quality": judged["scores"].get("source_completeness"),
            "policy_compliance": judged["scores"].get("policy_compliance"),
            "task_completion": judged["scores"].get("task_completion"),
            "uncertainty_calibration": judged["scores"].get("uncertainty_calibration"),
            "user_feedback_state": judged["user_feedback_state"],
            "supporting_evidence_ids": supporting,
            "counter_evidence_ids": counter,
            "limitations": judged.get("limitations") or list(body.get("limitations") or []),
            "result": judged["result"],
            "created_at": now,
            "confidence": None,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_evaluations(
                  id, experience_id, evaluator_id, evaluator_kind, rubric_id, rubric_version,
                  body_json, result, user_feedback_state, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    vid, eid, row["evaluator_id"], row["evaluator_kind"],
                    row["rubric_id"], row["rubric_version"], _dumps(row),
                    row["result"], row["user_feedback_state"], now,
                ),
            )
        return {"ok": True, "evaluation": row}

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM meta_evaluations WHERE id=?", (evaluation_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        body = _loads(d.get("body_json"))
        body["id"] = d["id"]
        body["result"] = d.get("result")
        return body

    def list_evaluations(self, *, experience_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.transaction() as conn:
            if experience_id:
                rows = conn.execute(
                    "SELECT * FROM meta_evaluations WHERE experience_id=? ORDER BY created_at DESC LIMIT ?",
                    (experience_id, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM meta_evaluations ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = _loads(d.get("body_json"))
            body["id"] = d["id"]
            body["result"] = d.get("result")
            out.append(body)
        return out

    def review_evaluation(self, evaluation_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        ev = self.get_evaluation(evaluation_id)
        if not ev:
            raise MetacogError("evaluation_not_found")
        note = body.get("note") or ""
        decision = str(body.get("decision") or "needs_review")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO meta_audit(id, kind, object_id, actor_id, action, detail_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (_id("aud"), "evaluation", evaluation_id, actor, "review", _dumps({"decision": decision, "note": note}), _now()),
            )
        return {"ok": True, "evaluation_id": evaluation_id, "decision": decision, "reviewed_by": actor}

    # ---- Hypothesis ----

    def create_hypothesis(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        hid = _id("hyp")
        now = _now()
        status = str(body.get("status") or "Candidate")
        if status not in {"Candidate", "NeedsReview", "Conflicted", "Rejected"}:
            status = "Candidate"
        row = {
            "id": hid,
            "hypothesis_id": hid,
            "statement": body.get("statement") or "",
            "inferred": True,
            "modality": body.get("modality") or "possible",
            "source_experience_ids": list(body.get("source_experience_ids") or []),
            "supporting_evaluation_ids": list(body.get("supporting_evaluation_ids") or []),
            "counter_evidence_ids": list(body.get("counter_evidence_ids") or []),
            "scope": body.get("scope") or "unknown",
            "valid_from": body.get("valid_from"),
            "valid_to": body.get("valid_to"),
            "status": status,
            "confidence": None,
            "created_by": actor,
            "audit_ref": body.get("audit_ref") or "",
            "created_at": now,
            "epistemic": "hypothesis",
            "not_knowledge": True,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_hypotheses(
                  id, statement, status, body_json, created_by, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (hid, row["statement"], status, _dumps(row), actor, now),
            )
        return {"ok": True, "hypothesis": row}

    def list_hypotheses(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM meta_hypotheses WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM meta_hypotheses ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [self._hyp_row(r) for r in rows]

    def get_hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM meta_hypotheses WHERE id=?", (hypothesis_id,)).fetchone()
        return self._hyp_row(row) if row else None

    @staticmethod
    def _hyp_row(row: Any) -> dict[str, Any]:
        d = dict(row)
        body = _loads(d.get("body_json"))
        body["id"] = d["id"]
        body["status"] = d.get("status")
        body["inferred"] = True
        body["confidence"] = None
        return body

    def relate_hypothesis(self, hypothesis_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        hyp = self.get_hypothesis(hypothesis_id)
        if not hyp:
            raise MetacogError("hypothesis_not_found")
        rel_id = _id("hrl")
        rel_type = str(body.get("relation_type") or "supports")
        if rel_type not in {"supports", "contradicts", "qualifies"}:
            raise MetacogError("invalid_relation_type")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_hypothesis_relations(
                  id, hypothesis_id, target_id, relation_type, created_by, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (rel_id, hypothesis_id, str(body.get("target_id") or ""), rel_type, actor, now),
            )
        return {"ok": True, "relation_id": rel_id, "from": hypothesis_id, "to": body.get("target_id"), "relation_type": rel_type}

    def list_hypothesis_relations(self, hypothesis_id: str) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_hypothesis_relations WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def transition_hypothesis(self, hypothesis_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        hyp = self.get_hypothesis(hypothesis_id)
        if not hyp:
            raise MetacogError("hypothesis_not_found")
        status = str(body.get("status") or "")
        if status not in {"Candidate", "NeedsReview", "Conflicted", "Rejected"}:
            raise MetacogError("invalid_status")
        payload = dict(hyp)
        payload["status"] = status
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_hypotheses SET status=?, body_json=? WHERE id=?",
                (status, _dumps(payload), hypothesis_id),
            )
        return {"ok": True, "hypothesis": payload, "transitioned_by": actor}

    # ---- Strategy candidates ----

    def create_candidate(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        hyps = list(body.get("source_hypothesis_ids") or [])
        exps = list(body.get("source_experience_ids") or [])
        if not hyps:
            raise MetacogError("hypothesis_required", "strategy candidate needs a source hypothesis")
        if len(exps) < 2:
            raise MetacogError(
                "insufficient_experience",
                "one failure cannot become a global strategy",
            )
        perms = body.get("permissions_delta") or body.get("permission_expansion")
        if perms:
            raise MetacogError("permission_expansion_forbidden")
        sid = _id("scd")
        now = _now()
        row = {
            "id": sid,
            "strategy_id": sid,
            "title": body.get("title") or "",
            "applicable_scope": body.get("applicable_scope") or "unknown",
            "prerequisites": list(body.get("prerequisites") or []),
            "execution_steps": list(body.get("execution_steps") or []),
            "forbidden_conditions": list(body.get("forbidden_conditions") or []),
            "expected_outcome": body.get("expected_outcome") or "",
            "success_criteria": list(body.get("success_criteria") or []),
            "failure_modes": list(body.get("failure_modes") or []),
            "source_hypothesis_ids": hyps,
            "source_experience_ids": exps,
            "risk": body.get("risk") or "unknown",
            "status": "Candidate",
            "publication_state": "draft",
            "version": 1,
            "rollback_reference": body.get("rollback_reference"),
            "created_by": actor,
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_strategy_candidates(
                  id, title, status, publication_state, body_json, created_by, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (sid, row["title"], row["status"], row["publication_state"], _dumps(row), actor, now),
            )
        return {"ok": True, "candidate": row}

    def get_candidate(self, strategy_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM meta_strategy_candidates WHERE id=?", (strategy_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        body = _loads(d.get("body_json"))
        body["id"] = d["id"]
        body["status"] = d.get("status")
        body["publication_state"] = d.get("publication_state")
        return body

    def list_candidates(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_strategy_candidates ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = _loads(d.get("body_json"))
            body["id"] = d["id"]
            body["publication_state"] = d.get("publication_state")
            out.append(body)
        return out

    def review_candidate(self, strategy_id: str, body: dict[str, Any], *, actor: str, role: str) -> dict[str, Any]:
        cand = self.get_candidate(strategy_id)
        if not cand:
            raise MetacogError("candidate_not_found")
        if role == "executor":
            raise MetacogError("executor_cannot_approve")
        if actor == cand.get("created_by") and str(body.get("decision") or "").lower() in {"approve", "approved"}:
            raise MetacogError("self_approve_forbidden")
        decision = str(body.get("decision") or "needs_review")
        pub = cand.get("publication_state") or "draft"
        if decision in {"approve", "approved"}:
            pub = "validated"
        elif decision in {"reject", "rejected"}:
            pub = "rejected"
        payload = dict(cand)
        payload["publication_state"] = pub
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_strategy_candidates SET publication_state=?, body_json=? WHERE id=?",
                (pub, _dumps(payload), strategy_id),
            )
        return {"ok": True, "candidate": payload, "reviewed_by": actor, "role": role}

    def mark_shadow(self, strategy_id: str, *, actor: str, role: str) -> dict[str, Any]:
        if role == "executor":
            raise MetacogError("executor_cannot_approve")
        cand = self.get_candidate(strategy_id)
        if not cand:
            raise MetacogError("candidate_not_found")
        payload = dict(cand)
        payload["publication_state"] = "shadow"
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_strategy_candidates SET publication_state=?, body_json=? WHERE id=?",
                ("shadow", _dumps(payload), strategy_id),
            )
        return {"ok": True, "candidate": payload, "note": "shadow does not change published snapshot"}

    def attach_change_plan(self, strategy_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        cand = self.get_candidate(strategy_id)
        if not cand:
            raise MetacogError("candidate_not_found")
        if not cand.get("rollback_reference"):
            raise MetacogError("rollback_reference_required")
        pid = _id("cpl")
        now = _now()
        plan = {
            "id": pid,
            "strategy_id": strategy_id,
            "actions": list(body.get("actions") or []),
            "risk": body.get("risk") or cand.get("risk") or "unknown",
            "approval": "human",
            "status": "draft",
            "created_by": actor,
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO meta_change_plans(id, strategy_id, body_json, status, created_by, created_at) VALUES (?,?,?,?,?,?)",
                (pid, strategy_id, _dumps(plan), "draft", actor, now),
            )
        return {"ok": True, "change_plan": plan}

    # ---- Experiments ----

    def create_experiment(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        xid = _id("xpr")
        now = _now()
        bank = str(body.get("bank_id") or "personal")
        requested = str(body.get("requested_bank_id") or bank)
        if requested != bank:
            raise MetacogError("cross_bank_denied", bank_id=bank, requested=requested)
        fixture = list(body.get("fixture") or body.get("frozen_inputs") or [])
        row = {
            "id": xid,
            "strategy_candidate_id": body.get("strategy_candidate_id"),
            "baseline_strategy_id": body.get("baseline_strategy_id"),
            "fixture_set_id": body.get("fixture_set_id") or _id("fix"),
            "replay_snapshot_ids": list(body.get("replay_snapshot_ids") or []),
            "evaluator_rubric_version": int(body.get("evaluator_rubric_version") or 1),
            "status": "proposed",
            "bank_id": bank,
            "frozen_inputs": fixture,
            "min_samples": int(body.get("min_samples") or 5),
            "created_by": actor,
            "created_at": now,
            "completed_at": None,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_experiments(
                  id, strategy_candidate_id, baseline_strategy_id, bank_id, status, body_json, created_by, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (xid, row["strategy_candidate_id"], row["baseline_strategy_id"], bank, "proposed", _dumps(row), actor, now),
            )
        return {"ok": True, "experiment": row}

    def list_experiments(self, *, bank_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with self.db.transaction() as conn:
            if bank_id:
                rows = conn.execute(
                    "SELECT * FROM meta_experiments WHERE bank_id=? ORDER BY created_at DESC LIMIT ?",
                    (bank_id, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM meta_experiments ORDER BY created_at DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = _loads(d.get("body_json"))
            body["id"] = d["id"]
            body["status"] = d.get("status")
            body["bank_id"] = d.get("bank_id")
            out.append(body)
        return out

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM meta_experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        body = _loads(d.get("body_json"))
        body["id"] = d["id"]
        body["status"] = d.get("status")
        return body

    def run_experiment(self, experiment_id: str, *, actor: str) -> dict[str, Any]:
        exp = self.get_experiment(experiment_id)
        if not exp:
            raise MetacogError("experiment_not_found")
        fixture = list(exp.get("frozen_inputs") or [])
        min_n = int(exp.get("min_samples") or 5)
        snap_ids = list(exp.get("replay_snapshot_ids") or [])
        baseline_snap = snap_ids[0] if snap_ids else None
        candidate_snap = snap_ids[1] if len(snap_ids) > 1 else baseline_snap
        same_freeze = True
        if baseline_snap and candidate_snap and len(snap_ids) >= 2:
            same_freeze = True
        result = "inconclusive"
        limitations = []
        if len(fixture) < min_n:
            limitations.append("insufficient_samples")
            result = "inconclusive"
        else:
            base_ok = sum(1 for x in fixture if (x or {}).get("baseline_ok", True))
            cand_ok = sum(1 for x in fixture if (x or {}).get("candidate_ok", True))
            safety = [x for x in fixture if (x or {}).get("safety_fail")]
            if safety:
                result = "failed"
                limitations.append("safety_failures")
            elif cand_ok < base_ok:
                result = "failed"
                limitations.append("regression")
            else:
                result = "passed"
        now = _now()
        report = {
            **exp,
            "status": result if result != "inconclusive" else "inconclusive",
            "result": result,
            "baseline_metrics": {"n": len(fixture), "snapshot_id": baseline_snap, "same_frozen_inputs": True},
            "candidate_metrics": {"n": len(fixture), "snapshot_id": candidate_snap, "same_frozen_inputs": same_freeze},
            "regressions": ["metric_drop"] if "regression" in limitations else [],
            "safety_failures": [x.get("id") for x in fixture if (x or {}).get("safety_fail")],
            "limitations": limitations,
            "completed_at": now,
            "run_by": actor,
        }
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_experiments SET status=?, body_json=?, completed_at=? WHERE id=?",
                (report["status"], _dumps(report), now, experiment_id),
            )
        return {"ok": True, "experiment": report}

    def cancel_experiment(self, experiment_id: str, *, actor: str) -> dict[str, Any]:
        exp = self.get_experiment(experiment_id)
        if not exp:
            raise MetacogError("experiment_not_found")
        payload = dict(exp)
        payload["status"] = "cancelled"
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_experiments SET status=?, body_json=? WHERE id=?",
                ("cancelled", _dumps(payload), experiment_id),
            )
        return {"ok": True, "experiment": payload, "cancelled_by": actor}

    # ---- Self model ----

    def create_self_model_candidate(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        eids = list(body.get("evaluation_ids") or body.get("supporting_evaluation_ids") or [])
        sample = body.get("sample_size")
        if not eids:
            raise MetacogError("evaluation_required", "SelfModel conclusions must cite Evaluation")
        if sample in (None, "", 0, "0"):
            raise MetacogError("sample_size_required")
        sid = _id("slf")
        now = _now()
        row = {
            "id": sid,
            "supported_task_types": list(body.get("supported_task_types") or []),
            "verified_capabilities": list(body.get("verified_capabilities") or []),
            "known_failure_modes": list(body.get("known_failure_modes") or []),
            "tool_reliability": body.get("tool_reliability") or {},
            "retrieval_gaps": list(body.get("retrieval_gaps") or []),
            "uncertainty_calibration": body.get("uncertainty_calibration"),
            "current_permissions": list(body.get("current_permissions") or []),
            "resource_bounds": body.get("resource_bounds") or {},
            "known_unknowns": list(body.get("known_unknowns") or []),
            "sample_size": int(sample),
            "evaluation_ids": eids,
            "snapshot_id": body.get("snapshot_id"),
            "status": "Candidate",
            "publication_state": "draft",
            "inferred": True,
            "confidence": None,
            "created_by": actor,
            "created_at": now,
            "last_verified_at": None,
        }
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO meta_self_models(id, status, publication_state, body_json, created_by, created_at) VALUES (?,?,?,?,?,?)",
                (sid, "Candidate", "draft", _dumps(row), actor, now),
            )
        return {"ok": True, "candidate": row}

    def list_self_models(self, *, published_only: bool = False) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            if published_only:
                rows = conn.execute(
                    "SELECT * FROM meta_self_models WHERE publication_state='published' ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM meta_self_models ORDER BY created_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = _loads(d.get("body_json"))
            body["id"] = d["id"]
            body["publication_state"] = d.get("publication_state")
            out.append(body)
        return out

    def review_self_model(self, candidate_id: str, body: dict[str, Any], *, actor: str, role: str) -> dict[str, Any]:
        items = {x["id"]: x for x in self.list_self_models()}
        row = items.get(candidate_id)
        if not row:
            raise MetacogError("self_model_not_found")
        if role == "executor":
            raise MetacogError("executor_cannot_approve")
        pub = "validated" if str(body.get("decision") or "") in {"approve", "approved"} else row.get("publication_state")
        payload = dict(row)
        payload["publication_state"] = pub
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_self_models SET publication_state=?, body_json=? WHERE id=?",
                (pub, _dumps(payload), candidate_id),
            )
        return {"ok": True, "candidate": payload}

    # ---- Snapshots ----

    def build_snapshot(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        kind = str(body.get("kind") or "strategy")
        if kind not in {"memory", "strategy", "self_model"}:
            raise MetacogError("invalid_snapshot_kind")
        bank = str(body.get("bank_id") or "personal")
        items = list(body.get("items") or [])
        sid = _id("snp")
        now = _now()
        manifest = {"kind": kind, "bank_id": bank, "items": items, "built_at": now}
        digest = hashlib.sha256(_dumps(manifest).encode("utf-8")).hexdigest()
        current = self.current_snapshot(kind, bank)
        generation = int((current or {}).get("generation") or 0) + 1
        exclusive = bool(body.get("exclusive_stable")) if "exclusive_stable" in body else bool(
            items and all(isinstance(x, dict) and x.get("id") for x in items)
        )
        row = {
            "id": sid,
            "kind": kind,
            "bank_id": bank,
            "generation": generation,
            "base_snapshot_id": (current or {}).get("id"),
            "manifest_hash": digest,
            "publication_state": "draft",
            "items": items,
            "exclusive_stable": exclusive,
            "rollback_reference": (current or {}).get("id"),
            "created_by": actor,
            "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_snapshots(
                  id, kind, bank_id, generation, manifest_hash, publication_state, body_json, created_by, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (sid, kind, bank, generation, digest, "draft", _dumps(row), actor, now),
            )
        return {"ok": True, "snapshot": row}

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM meta_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        body = _loads(d.get("body_json"))
        body["id"] = d["id"]
        body["publication_state"] = d.get("publication_state")
        body["generation"] = d.get("generation")
        body["manifest_hash"] = d.get("manifest_hash")
        return body

    def current_snapshot(self, kind: str, bank_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            head = conn.execute(
                "SELECT snapshot_id FROM meta_snapshot_heads WHERE bank_id=? AND kind=?",
                (bank_id, kind),
            ).fetchone()
        if not head:
            return None
        snap = self.get_snapshot(dict(head)["snapshot_id"])
        if snap and snap.get("publication_state") != "published":
            return None
        return snap

    def current_all(self, bank_id: str = "personal") -> dict[str, Any]:
        return {
            "ok": True,
            "bank_id": bank_id,
            "memory": self.current_snapshot("memory", bank_id),
            "strategy": self.current_snapshot("strategy", bank_id),
            "self_model": self.current_snapshot("self_model", bank_id),
        }

    def validate_snapshot(self, snapshot_id: str, *, actor: str) -> dict[str, Any]:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise MetacogError("snapshot_not_found")
        payload = dict(snap)
        payload["publication_state"] = "validated"
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE meta_snapshots SET publication_state=?, body_json=? WHERE id=?",
                ("validated", _dumps(payload), snapshot_id),
            )
        return {"ok": True, "snapshot": payload, "validated_by": actor}

    def publish_snapshot(self, snapshot_id: str, *, actor: str, role: str) -> dict[str, Any]:
        if role != "publisher":
            raise MetacogError("publisher_required")
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise MetacogError("snapshot_not_found")
        if snap.get("kind") == "strategy":
            items = snap.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                cand_id = item.get("strategy_id") or item.get("id")
                if not cand_id:
                    continue
                cand = self.get_candidate(str(cand_id))
                if not cand:
                    continue
                if str(cand.get("publication_state") or "") == "shadow":
                    raise MetacogError("shadow_not_online", "shadow strategy cannot enter published snapshot")
                if not cand.get("rollback_reference"):
                    raise MetacogError("rollback_reference_required")
        payload = dict(snap)
        now = _now()
        payload["publication_state"] = "published"
        payload["published_at"] = now
        payload["published_by"] = actor
        bank = snap["bank_id"]
        kind = snap["kind"]
        gen = int(snap["generation"])
        try:
            with self.db.transaction() as conn:
                taken = conn.execute(
                    """
                    SELECT id FROM meta_snapshots
                    WHERE bank_id=? AND kind=? AND generation=? AND publication_state='published' AND id!=?
                    """,
                    (bank, kind, gen, snapshot_id),
                ).fetchone()
                if taken:
                    raise MetacogError("generation_conflict", "concurrent publish lost")
                conn.execute(
                    "UPDATE meta_snapshots SET publication_state=?, body_json=? WHERE id=?",
                    ("published", _dumps(payload), snapshot_id),
                )
                conn.execute(
                    """
                    INSERT INTO meta_snapshot_heads(bank_id, kind, snapshot_id, generation, updated_at)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(bank_id, kind) DO UPDATE SET
                      snapshot_id=excluded.snapshot_id,
                      generation=excluded.generation,
                      updated_at=excluded.updated_at
                    WHERE meta_snapshot_heads.generation < excluded.generation
                    """,
                    (bank, kind, snapshot_id, gen, now),
                )
                head = conn.execute(
                    "SELECT snapshot_id, generation FROM meta_snapshot_heads WHERE bank_id=? AND kind=?",
                    (bank, kind),
                ).fetchone()
                if not head or dict(head)["snapshot_id"] != snapshot_id:
                    raise MetacogError("generation_conflict", "concurrent publish lost")
                conn.execute(
                    "INSERT INTO meta_snapshot_audit(id, snapshot_id, actor_id, action, created_at) VALUES (?,?,?,?,?)",
                    (_id("sau"), snapshot_id, actor, "publish", now),
                )
        except MetacogError:
            raise
        except Exception as exc:
            raise MetacogError("publish_failed", str(exc)[:200]) from exc
        return {"ok": True, "snapshot": payload}

    def mutate_snapshot(self, snapshot_id: str) -> None:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise MetacogError("snapshot_not_found")
        if snap.get("publication_state") == "published":
            raise MetacogError("snapshot_immutable", "online requests cannot modify published snapshots")
        raise MetacogError("snapshot_immutable")

    def diff_snapshots(self, snapshot_id: str, other_id: str | None = None) -> dict[str, Any]:
        a = self.get_snapshot(snapshot_id)
        if not a:
            raise MetacogError("snapshot_not_found")
        b = self.get_snapshot(other_id) if other_id else self.current_snapshot(a["kind"], a["bank_id"])
        a_items = {json.dumps(x, sort_keys=True) for x in (a.get("items") or [])}
        b_items = {json.dumps(x, sort_keys=True) for x in ((b or {}).get("items") or [])}
        return {
            "ok": True,
            "from": (b or {}).get("id"),
            "to": a["id"],
            "added": list(a_items - b_items),
            "removed": list(b_items - a_items),
        }

    def rollback_snapshot(self, snapshot_id: str, *, actor: str, role: str) -> dict[str, Any]:
        if role != "publisher":
            raise MetacogError("publisher_required")
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            raise MetacogError("snapshot_not_found")
        target_id = snap.get("rollback_reference") or snap.get("base_snapshot_id")
        if not target_id:
            raise MetacogError("no_rollback_target")
        target = self.get_snapshot(target_id)
        if not target:
            raise MetacogError("rollback_target_missing")
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta_snapshot_heads(bank_id, kind, snapshot_id, generation, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(bank_id, kind) DO UPDATE SET
                  snapshot_id=excluded.snapshot_id,
                  generation=excluded.generation,
                  updated_at=excluded.updated_at
                """,
                (target["bank_id"], target["kind"], target["id"], int(target["generation"]), now),
            )
            conn.execute(
                "INSERT INTO meta_snapshot_audit(id, snapshot_id, actor_id, action, created_at) VALUES (?,?,?,?,?)",
                (_id("sau"), snapshot_id, actor, "rollback", now),
            )
        return {"ok": True, "current": self.current_snapshot(target["kind"], target["bank_id"]), "rolled_back_from": snapshot_id}

    # ---- External bundle ----

    def log_external_bundle(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        bid = _id("ext")
        now = _now()
        row = {
            "id": bid,
            "ttl_seconds": int(3600 if body.get("ttl_seconds") is None else body.get("ttl_seconds")),
            "payload": body.get("payload") or {},
            "source": body.get("source") or "external_search",
            "created_by": actor,
            "created_at": now,
            "promoted": False,
        }
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO meta_external_bundles(id, body_json, created_by, created_at) VALUES (?,?,?,?)",
                (bid, _dumps(row), actor, now),
            )
        return {"ok": True, "bundle": row, "note": "online-only; cannot become Knowledge without offline consolidation"}

    def expire_external_bundles(self, *, now: str | None = None) -> dict[str, Any]:
        stamp = now or _now()
        try:
            now_dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            now_dt = datetime.now(timezone.utc)
        expired_ids: list[str] = []
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT id, body_json, created_at FROM meta_external_bundles").fetchall()
            for row in rows:
                d = dict(row)
                body = _loads(d.get("body_json"))
                ttl = int(3600 if body.get("ttl_seconds") is None else body.get("ttl_seconds"))
                created = str(d.get("created_at") or body.get("created_at") or "")
                try:
                    created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ttl <= 0 or created_dt.timestamp() + ttl <= now_dt.timestamp():
                    conn.execute("DELETE FROM meta_external_bundles WHERE id=?", (d["id"],))
                    expired_ids.append(d["id"])
        return {"ok": True, "expired": expired_ids, "count": len(expired_ids)}

    def governance_queue(self) -> dict[str, Any]:
        cands = [c for c in self.list_candidates() if c.get("publication_state") in {"draft", "validated", "shadow"}]
        hyps = [h for h in self.list_hypotheses() if h.get("status") in {"Candidate", "NeedsReview", "Conflicted"}]
        return {
            "ok": True,
            "pending_candidates": cands,
            "pending_hypotheses": hyps,
            "high_impact": [c for c in cands if str(c.get("risk") or "").lower() in {"high", "unknown"}],
        }

    def audit_chain(self, strategy_id: str) -> dict[str, Any]:
        cand = self.get_candidate(strategy_id)
        if not cand:
            raise MetacogError("candidate_not_found")
        hyps = [self.get_hypothesis(h) for h in cand.get("source_hypothesis_ids") or []]
        evals = []
        for h in hyps:
            if not h:
                continue
            for eid in h.get("supporting_evaluation_ids") or []:
                evals.append(self.get_evaluation(eid))
        exps = [self.get_experience(x) for x in cand.get("source_experience_ids") or []]
        return {
            "ok": True,
            "strategy": cand,
            "hypotheses": [h for h in hyps if h],
            "evaluations": [e for e in evals if e],
            "experiences": [e for e in exps if e],
        }
