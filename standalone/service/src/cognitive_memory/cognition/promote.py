# -*- coding: utf-8 -*-
"""慢速通道：候选晋升（E2.3 条件显式化）。禁止静默 ACTIVE。"""

from __future__ import annotations

from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.models import MemoryStatus, MemoryType, MemoryUpdateIn

# 显式晋升规则（文档化 + 测试锚点）
PROMOTION_RULES: dict[str, Any] = {
    "version": "theory.v1",
    "rules": [
        {
            "id": "force_activate",
            "when": "retired: metadata.force_activate is not evidence",
            "effect": "no automatic promotion",
        },
        {
            "id": "multi_evidence",
            "when": "linked claim has current database-backed verification",
            "effect": "candidate → active",
        },
        {
            "id": "user_semantic_fact",
            "when": "retired: user statement alone does not verify content",
            "effect": "no automatic promotion",
        },
        {
            "id": "useful_feedback",
            "when": "retired: usefulness is utility, not truth",
            "effect": "no automatic promotion",
        },
    ],
    "forbidden": [
        "silent_active_without_rule",
        "agent_inference_direct_active",
    ],
}


def evaluate_promotion(data: dict[str, Any]) -> dict[str, Any]:
    """只评估、不写库。返回 matched 规则 id 列表。"""
    if data.get("status") != MemoryStatus.CANDIDATE.value:
        return {"promote": False, "matched": [], "reason": "not_candidate"}
    meta = data.get("metadata") or {}
    epistemic = str(meta.get("epistemic") or "")
    if epistemic in {"inference", "inferred_signal", "summary", "generated"}:
        return {
            "promote": False,
            "matched": [],
            "reason": "hypothesis_requires_explicit_reclassification",
        }
    matched = ["verified_claim"] if data.get("verified_claim_assessment") is True else []
    return {
        "promote": bool(matched),
        "matched": matched,
        "reason": "matched" if matched else "no_rule",
    }


def maybe_promote(service: MemoryService, memory_id: str) -> bool:
    with service.db.transaction():
        return _maybe_promote(service, memory_id)


def _maybe_promote(service: MemoryService, memory_id: str) -> bool:
    if not memory_id:
        return False
    data = service.get_memory(memory_id, include_evidence=True)
    from cognitive_memory.claims.store import ClaimStore
    from cognitive_memory.verification.assessment import assess_evidence
    from cognitive_memory.cognition.loops import _current
    with service.db.exclusive() as conn:
        rows = conn.execute("SELECT id FROM claims WHERE source_memory_id=? AND lifecycle='verified' AND verification_status='verified'", (memory_id,)).fetchall()
    store = ClaimStore(service.db)
    data["verified_claim_assessment"] = False
    for row in rows:
        claim = store.get(row[0], follow_redirect=False)
        refs = [x["evidence_ref"] for x in store.list_evidence(row[0]) if x["role"] == "support"]
        if claim and _current(claim.get("validity_from"), claim.get("validity_to")) and assess_evidence(service.db, claim, refs)["ok"]:
            data["verified_claim_assessment"] = True
            break
    decision = evaluate_promotion(data)
    if not decision.get("promote"):
        return False
    service.update_memory(
        memory_id,
        MemoryUpdateIn(status=MemoryStatus.ACTIVE, expected_version=data.get("version")),
    )
    return True


def maybe_promote_with_feedback(
    service: MemoryService, memory_id: str, *, useful_count: int
) -> bool:
    """feedback → promote 闭环入口：把 useful 计数注入评估。"""
    return maybe_promote(service, memory_id)
