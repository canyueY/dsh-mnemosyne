# -*- coding: utf-8 -*-
"""Bridge cognitive schemas into TMS for impact / reevaluate (no auto overwrite)."""

from __future__ import annotations

from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.judgment.truth_value import TruthValue
from cognitive_memory.tms.engine import TruthMaintenanceEngine
from cognitive_memory.tms.models import BeliefStatus


_REL_TO_KIND = {
    "supports": "support",
    "SUPPORTS": "support",
    "contradicts": "contradict",
    "CONTRADICTS": "contradict",
    "dependsOn": "support",
    "DEPENDS_ON": "support",
    "refines": "support",
    "REFINES": "support",
    "generalizes": "support",
    "GENERALIZES": "support",
    "specializes": "support",
    "SPECIALIZES": "support",
    "SPLIT_FROM": "support",
    "SPLIT_INTO": "support",
    "MERGED_FROM": "support",
    "MERGED_INTO": "support",
}


def _schema_node(schema_id: str) -> str:
    return f"sch:{schema_id}"


def build_schema_tms(
    service: MemoryService,
    schema_id: str,
    *,
    engine: TruthMaintenanceEngine | None = None,
) -> tuple[TruthMaintenanceEngine, dict[str, Any]]:
    """Load seed schema + 1-hop neighbors into a TMS engine snapshot."""
    eng = engine or TruthMaintenanceEngine(max_nodes=48, max_depth=12)
    sch = service.get_cognitive_schema(schema_id)
    if not sch:
        return eng, {"ok": False, "detail": "schema_not_found"}

    seed = _schema_node(schema_id)
    # Seed belief from schema state
    state = str(sch.get("state") or "").upper()
    if state in ("ACTIVE", "VALIDATING"):
        eng.set_premise_truth(seed, TruthValue.TRUE)
    elif state in ("RETIRED", "REJECTED"):
        eng.set_premise_truth(seed, TruthValue.FALSE)
    else:
        eng.set_premise_truth(seed, TruthValue.UNKNOWN)

    # Evidence refs as premises supporting the schema
    evid = sch.get("evidence") or {}
    for mid in evid.get("supporting") or []:
        mid = str(mid)
        eng.set_premise_truth(f"mem:{mid}", TruthValue.TRUE)
        eng.add_justification(seed, [f"mem:{mid}"], kind="support", reason="schema_evidence")
    for mid in evid.get("counter") or []:
        mid = str(mid)
        eng.set_premise_truth(f"mem:{mid}", TruthValue.TRUE)
        eng.add_justification(seed, [f"mem:{mid}"], kind="contradict", reason="schema_counter")

    # network_links + relations
    links = list(sch.get("network_links") or sch.get("links") or [])
    for link in links:
        if not isinstance(link, dict):
            continue
        tid = str(link.get("target_id") or "")
        if not tid:
            continue
        rel = str(link.get("relation") or "dependsOn")
        kind = _REL_TO_KIND.get(rel, "support")
        other = _schema_node(tid)
        other_sch = service.get_cognitive_schema(tid)
        if other_sch:
            ost = str(other_sch.get("state") or "").upper()
            if ost in ("ACTIVE", "VALIDATING"):
                eng.set_premise_truth(other, TruthValue.TRUE)
            elif ost in ("RETIRED", "REJECTED"):
                eng.set_premise_truth(other, TruthValue.FALSE)
            else:
                eng.set_premise_truth(other, TruthValue.UNKNOWN)
        # dependency: neighbor supports/depends → conclusion seed uses neighbor as premise
        if kind == "contradict":
            eng.add_justification(seed, [other], kind="contradict", reason=rel)
        else:
            # seed depends on neighbor (or neighbor refined from seed)
            if rel in ("SPLIT_INTO", "MERGED_INTO", "SPECIALIZES", "specializes"):
                eng.add_justification(other, [seed], kind="support", reason=rel)
            else:
                eng.add_justification(seed, [other], kind="support", reason=rel)

    rel_map = sch.get("relations") or {}
    if isinstance(rel_map, dict):
        for rel_kind, targets in rel_map.items():
            kind = _REL_TO_KIND.get(str(rel_kind), "support")
            for tid in targets or []:
                tid = str(tid)
                other = _schema_node(tid)
                if kind == "contradict":
                    eng.add_justification(seed, [other], kind="contradict", reason=str(rel_kind))
                else:
                    eng.add_justification(seed, [other], kind="support", reason=str(rel_kind))

    eng.propagate([seed], max_nodes=48)
    return eng, {"ok": True, "seed": seed, "schema_id": schema_id}


def analyze_schema_impact_tms(
    service: MemoryService,
    schema_id: str,
) -> dict[str, Any]:
    """TMS-backed impact: reevaluate tasks only — never recursive overwrite."""
    from datetime import datetime, timezone

    eng, meta = build_schema_tms(service, schema_id)
    if not meta.get("ok"):
        return meta
    seed = str(meta["seed"])
    impact = eng.impact(seed, max_nodes=48)
    belief = eng.belief(seed)
    affected: list[dict[str, Any]] = []
    for nid in impact.get("affected") or []:
        bel = eng.belief(nid)
        affected.append(
            {
                "node_id": nid,
                "belief": bel.status.value,
                "action": "reevaluate",
                "auto_overwrite": False,
                "relation": "tms_dependent",
            }
        )
    # Also surface legacy-style relation neighbors for operators
    sch = service.get_cognitive_schema(schema_id) or {}
    rel = sch.get("relations") or {}
    for kind in (
        "supports",
        "contradicts",
        "generalizes",
        "specializes",
        "dependsOn",
        "refines",
    ):
        for nid in rel.get(kind) or []:
            affected.append(
                {
                    "node_id": str(nid),
                    "relation": kind,
                    "action": "reevaluate",
                    "auto_overwrite": False,
                    "belief": eng.belief(_schema_node(str(nid))).status.value
                    if service.get_cognitive_schema(str(nid))
                    else BeliefStatus.UNKNOWN.value,
                }
            )
    return {
        "ok": True,
        "changed_schema_id": schema_id,
        "seed_belief": belief.model_dump(mode="json"),
        "affected": affected,
        "count": len(affected),
        "tms_impact": impact,
        "note": "TMS impact preview；禁止无条件递归覆盖",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
