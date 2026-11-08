# -*- coding: utf-8 -*-
"""HTTP routes for governed metacognition."""

from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request

from cognitive_memory.governance.access import AccessContext, make_access_dep
from cognitive_memory.metacognition.roles import (
    EVALUATOR,
    EXECUTOR,
    PUBLISHER,
    reject_hidden_thinking,
    require_meta_role,
)
from cognitive_memory.metacognition.store import MetacogError, MetacogStore


def register_metacog_routes(app: FastAPI, service: Any, pref: str) -> None:
    def store() -> MetacogStore:
        if not hasattr(app.state, "metacog_store"):
            app.state.metacog_store = MetacogStore(service.db)
        return app.state.metacog_store

    def _guard(request: Request, access: AccessContext) -> None:
        actor = (request.headers.get("x-meta-actor") or access.actor_id or "").strip()
        if actor and store().is_revoked(actor):
            raise HTTPException(status_code=403, detail={"code": "actor_revoked", "actor_id": actor})

    def _err(exc: MetacogError) -> HTTPException:
        status = 403 if exc.code in {
            "executor_cannot_approve",
            "self_approve_forbidden",
            "publisher_required",
            "permission_expansion_forbidden",
            "cross_bank_denied",
            "snapshot_immutable",
            "actor_revoked",
        } else 400
        if exc.code.endswith("_not_found"):
            status = 404
        return HTTPException(status_code=status, detail=exc.as_detail())

    @app.post(f"{pref}/experiences/governed")
    def experience_governed_create(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experience.create")),
    ) -> dict[str, Any]:
        _guard(request, access)
        _role, actor = require_meta_role(request, access, EXECUTOR)
        from cognitive_memory.metacognition.online import stamp_experience, working_set

        pack = working_set(store(), bank_id=str(body.get("bank_id") or "personal"))
        return store().create_experience(stamp_experience(body, pack), actor=actor)

    @app.get(f"{pref}/experiences/{{experience_id}}")
    def experience_get(
        experience_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="experience.get")),
    ) -> dict[str, Any]:
        _guard(request, access)
        item = store().get_experience(experience_id)
        if item:
            return {"ok": True, "experience": item}
        from cognitive_memory.strategy.experience_store import ExperienceStore

        legacy = ExperienceStore(service.db).get(experience_id)
        if not legacy:
            raise HTTPException(status_code=404, detail="experience_not_found")
        return {"ok": True, "experience": legacy, "source": "legacy"}

    @app.post(f"{pref}/experiences/{{experience_id}}/trace")
    def experience_trace(
        experience_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experience.trace")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EXECUTOR)
        reject_hidden_thinking(body)
        try:
            return store().add_trace(experience_id, body, actor=(request.headers.get("x-meta-actor") or access.actor_id))
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/experiences/{{experience_id}}/outcome")
    def experience_outcome(
        experience_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experience.outcome")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EXECUTOR)
        try:
            actor = request.headers.get("x-meta-actor") or access.actor_id
            out = store().add_outcome(experience_id, body, actor=actor)
            from cognitive_memory.metacognition.loop import level1_after_outcome

            out["level1"] = level1_after_outcome(store(), experience_id, actor=actor)
            return out
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/evaluations")
    def evaluation_create(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="evaluation.create")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR)
        failed = bool(body.get("evaluator_failed"))
        try:
            return store().create_evaluation(
                body,
                actor=(request.headers.get("x-meta-actor") or access.actor_id),
                evaluator_failed=failed,
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/evaluations")
    def evaluation_list(
        experience_id: str | None = None,
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="evaluation.list")),
    ) -> dict[str, Any]:
        items = store().list_evaluations(experience_id=experience_id, limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.get(f"{pref}/evaluations/{{evaluation_id}}")
    def evaluation_get(
        evaluation_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="evaluation.get")),
    ) -> dict[str, Any]:
        item = store().get_evaluation(evaluation_id)
        if not item:
            raise HTTPException(status_code=404, detail="evaluation_not_found")
        return {"ok": True, "evaluation": item}

    @app.post(f"{pref}/evaluations/{{evaluation_id}}/review")
    def evaluation_review(
        evaluation_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="evaluation.review")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().review_evaluation(
                evaluation_id, body, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/hypotheses")
    def hypothesis_create(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="hypothesis.create")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR)
        return store().create_hypothesis(body, actor=(request.headers.get("x-meta-actor") or access.actor_id))

    @app.get(f"{pref}/hypotheses")
    def hypothesis_list(
        status: str | None = None,
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="hypothesis.list")),
    ) -> dict[str, Any]:
        items = store().list_hypotheses(status=status, limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.get(f"{pref}/hypotheses/{{hypothesis_id}}")
    def hypothesis_get(
        hypothesis_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="hypothesis.get")),
    ) -> dict[str, Any]:
        item = store().get_hypothesis(hypothesis_id)
        if not item:
            raise HTTPException(status_code=404, detail="hypothesis_not_found")
        return {"ok": True, "hypothesis": item}

    @app.post(f"{pref}/hypotheses/{{hypothesis_id}}/relate")
    def hypothesis_relate(
        hypothesis_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="hypothesis.relate")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR)
        try:
            return store().relate_hypothesis(
                hypothesis_id, body, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/hypotheses/{{hypothesis_id}}/transition")
    def hypothesis_transition(
        hypothesis_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="hypothesis.transition")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().transition_hypothesis(
                hypothesis_id, body, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/strategy-candidates")
    def candidate_create(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="strategy.candidate")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR)
        try:
            return store().create_candidate(body, actor=(request.headers.get("x-meta-actor") or access.actor_id))
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/strategy-candidates")
    def candidate_list(
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="strategy.candidate.list")),
    ) -> dict[str, Any]:
        items = store().list_candidates(limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/strategy-candidates/{{strategy_id}}/review")
    def candidate_review(
        strategy_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="strategy.candidate.review")),
    ) -> dict[str, Any]:
        _guard(request, access)
        role, actor = require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().review_candidate(strategy_id, body, actor=actor, role=role)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/strategy-candidates/{{strategy_id}}/shadow")
    def candidate_shadow(
        strategy_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="strategy.shadow")),
    ) -> dict[str, Any]:
        _guard(request, access)
        role, actor = require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().mark_shadow(strategy_id, actor=actor, role=role)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/strategy-candidates/{{strategy_id}}/change-plan")
    def candidate_change_plan(
        strategy_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="strategy.change_plan")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().attach_change_plan(
                strategy_id, body, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/experiments")
    def experiment_list(
        bank_id: str | None = None,
        limit: int = 50,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="experiment.list")),
    ) -> dict[str, Any]:
        items = store().list_experiments(bank_id=bank_id, limit=limit)
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/experiments")
    def experiment_create(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experiment.create")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().create_experiment(body, actor=(request.headers.get("x-meta-actor") or access.actor_id))
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/experiments/{{experiment_id}}")
    def experiment_get(
        experiment_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="experiment.get")),
    ) -> dict[str, Any]:
        item = store().get_experiment(experiment_id)
        if not item:
            raise HTTPException(status_code=404, detail="experiment_not_found")
        return {"ok": True, "experiment": item}

    @app.post(f"{pref}/experiments/{{experiment_id}}/run")
    def experiment_run(
        experiment_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experiment.run")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().run_experiment(
                experiment_id, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/experiments/{{experiment_id}}/cancel")
    def experiment_cancel(
        experiment_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="experiment.cancel")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().cancel_experiment(
                experiment_id, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/experiments/{{experiment_id}}/report")
    def experiment_report(
        experiment_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="experiment.report")),
    ) -> dict[str, Any]:
        item = store().get_experiment(experiment_id)
        if not item:
            raise HTTPException(status_code=404, detail="experiment_not_found")
        return {"ok": True, "report": item}

    @app.get(f"{pref}/self-model")
    def self_model_published(
        bank_id: str = "personal",
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="self_model.get")),
    ) -> dict[str, Any]:
        current = store().current_snapshot("self_model", bank_id)
        return {"ok": True, "snapshot": current, "candidates": []}

    @app.get(f"{pref}/self-model/candidates")
    def self_model_candidates(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="self_model.list")),
    ) -> dict[str, Any]:
        items = store().list_self_models(published_only=False)
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/self-model/candidates")
    def self_model_create(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="self_model.create")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR)
        try:
            return store().create_self_model_candidate(
                body, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/self-model/candidates/{{candidate_id}}/review")
    def self_model_review(
        candidate_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="self_model.review")),
    ) -> dict[str, Any]:
        _guard(request, access)
        role, actor = require_meta_role(request, access, EVALUATOR, PUBLISHER)
        try:
            return store().review_self_model(candidate_id, body, actor=actor, role=role)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/snapshots/build")
    def snapshot_build(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="snapshot.build")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, PUBLISHER)
        try:
            return store().build_snapshot(body, actor=(request.headers.get("x-meta-actor") or access.actor_id))
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/snapshots/{{snapshot_id}}/validate")
    def snapshot_validate(
        snapshot_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="snapshot.validate")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, PUBLISHER)
        try:
            return store().validate_snapshot(
                snapshot_id, actor=(request.headers.get("x-meta-actor") or access.actor_id)
            )
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/snapshots/{{snapshot_id}}/publish")
    def snapshot_publish(
        snapshot_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="snapshot.publish")),
    ) -> dict[str, Any]:
        _guard(request, access)
        role, actor = require_meta_role(request, access, PUBLISHER)
        try:
            return store().publish_snapshot(snapshot_id, actor=actor, role=role)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/snapshots/current")
    def snapshot_current(
        bank_id: str = "personal",
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="snapshot.current")),
    ) -> dict[str, Any]:
        return store().current_all(bank_id)

    @app.get(f"{pref}/snapshots/{{snapshot_id}}")
    def snapshot_get(
        snapshot_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="snapshot.get")),
    ) -> dict[str, Any]:
        item = store().get_snapshot(snapshot_id)
        if not item:
            raise HTTPException(status_code=404, detail="snapshot_not_found")
        return {"ok": True, "snapshot": item}

    @app.get(f"{pref}/snapshots/{{snapshot_id}}/diff")
    def snapshot_diff(
        snapshot_id: str,
        other_id: str | None = None,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="snapshot.diff")),
    ) -> dict[str, Any]:
        try:
            return store().diff_snapshots(snapshot_id, other_id)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/snapshots/{{snapshot_id}}/rollback")
    def snapshot_rollback(
        snapshot_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="snapshot.rollback")),
    ) -> dict[str, Any]:
        _guard(request, access)
        role, actor = require_meta_role(request, access, PUBLISHER)
        try:
            return store().rollback_snapshot(snapshot_id, actor=actor, role=role)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.post(f"{pref}/snapshots/{{snapshot_id}}/mutate")
    def snapshot_mutate(
        snapshot_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="snapshot.mutate")),
    ) -> dict[str, Any]:
        try:
            store().mutate_snapshot(snapshot_id)
        except MetacogError as exc:
            raise _err(exc) from exc
        raise HTTPException(status_code=403, detail={"code": "snapshot_immutable"})

    @app.get(f"{pref}/rubrics")
    def rubric_list(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="rubric.list")),
    ) -> dict[str, Any]:
        items = store().list_rubrics()
        return {"ok": True, "items": items, "count": len(items)}

    @app.get(f"{pref}/external-bundles")
    def external_bundle_list(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="external_bundle.list")),
    ) -> dict[str, Any]:
        items = store().list_external_bundles()
        return {"ok": True, "items": items, "count": len(items)}

    @app.post(f"{pref}/external-bundles")
    def external_bundle(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="external_bundle")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EXECUTOR)
        return store().log_external_bundle(body, actor=(request.headers.get("x-meta-actor") or access.actor_id))

    @app.post(f"{pref}/metacog/level1/{{experience_id}}")
    def metacog_level1(
        experience_id: str,
        request: Request,
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="metacog.level1")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, EXECUTOR)
        from cognitive_memory.metacognition.loop import level1_after_outcome

        return level1_after_outcome(
            store(), experience_id, actor=(request.headers.get("x-meta-actor") or access.actor_id)
        )

    @app.get(f"{pref}/metacog/working-set")
    def metacog_working_set(
        bank_id: str = "personal",
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="metacog.working_set")),
    ) -> dict[str, Any]:
        from cognitive_memory.metacognition.online import working_set

        pack = working_set(store(), bank_id=bank_id)
        pack["stable_ids"] = sorted(pack.get("stable_ids") or [])[:200]
        return pack

    @app.post(f"{pref}/metacog/canary-observe")
    def metacog_canary(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="metacog.canary")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR, PUBLISHER)
        from cognitive_memory.metacognition.loop import canary_observe

        return canary_observe(
            store(),
            body,
            actor=(request.headers.get("x-meta-actor") or access.actor_id),
        )

    @app.post(f"{pref}/metacog/cycle-review")
    def metacog_cycle_review(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="metacog.cycle")),
    ) -> dict[str, Any]:
        _guard(request, access)
        require_meta_role(request, access, EVALUATOR)
        from cognitive_memory.metacognition.loop import cycle_review

        return cycle_review(
            store(),
            actor=(request.headers.get("x-meta-actor") or access.actor_id),
            min_cluster=int(body.get("min_cluster") or 2),
            limit=int(body.get("limit") or 80),
        )

    @app.get(f"{pref}/metacog/governance")
    def metacog_governance(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="metacog.governance")),
    ) -> dict[str, Any]:
        return store().governance_queue()

    @app.get(f"{pref}/metacog/audit-chain/{{strategy_id}}")
    def metacog_audit_chain(
        strategy_id: str,
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="metacog.audit")),
    ) -> dict[str, Any]:
        try:
            return store().audit_chain(strategy_id)
        except MetacogError as exc:
            raise _err(exc) from exc

    @app.get(f"{pref}/metacog/graph")
    def metacog_graph(
        access: AccessContext = Depends(make_access_dep(permission="memory.read", resource="metacog.graph")),
    ) -> dict[str, Any]:
        st = store()
        nodes = []
        edges = []
        for exp in st.list_experiences(limit=80):
            nodes.append({"id": exp["id"], "object_type": "Experience", "title": exp.get("goal") or exp["id"], "publication_state": None})
        for ev in st.list_evaluations(limit=80):
            nodes.append({"id": ev["id"], "object_type": "Evaluation", "title": ev.get("result") or "unknown", "epistemic": "evaluation"})
            if ev.get("experience_id"):
                edges.append({"from": ev["id"], "to": ev["experience_id"], "relation_type": "derived_from"})
        for hyp in st.list_hypotheses(limit=80):
            nodes.append({"id": hyp["id"], "object_type": "Hypothesis", "title": hyp.get("statement") or hyp["id"], "inferred": True, "status": hyp.get("status")})
            for eid in hyp.get("supporting_evaluation_ids") or []:
                edges.append({"from": eid, "to": hyp["id"], "relation_type": "supports"})
            for cid in hyp.get("counter_evidence_ids") or []:
                edges.append({"from": cid, "to": hyp["id"], "relation_type": "contradicts"})
            for rel in st.list_hypothesis_relations(hyp["id"]):
                edges.append({"from": rel["hypothesis_id"], "to": rel["target_id"], "relation_type": rel["relation_type"]})
        for cand in st.list_candidates(limit=80):
            nodes.append({"id": cand["id"], "object_type": "StrategyCandidate", "title": cand.get("title") or cand["id"], "publication_state": cand.get("publication_state")})
            for hid in cand.get("source_hypothesis_ids") or []:
                edges.append({"from": hid, "to": cand["id"], "relation_type": "derived_from"})
        return {"ok": True, "nodes": nodes, "edges": edges, "note": "layout-only consumers must not write facts"}

    @app.post(f"{pref}/metacog/revoke")
    def metacog_revoke(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        access: AccessContext = Depends(make_access_dep(permission="memory.write", resource="metacog.revoke")),
    ) -> dict[str, Any]:
        require_meta_role(request, access, PUBLISHER)
        actor_id = str(body.get("actor_id") or "")
        if not actor_id:
            raise HTTPException(status_code=400, detail="actor_id_required")
        return store().revoke(actor_id, by=(request.headers.get("x-meta-actor") or access.actor_id), reason=str(body.get("reason") or ""))
