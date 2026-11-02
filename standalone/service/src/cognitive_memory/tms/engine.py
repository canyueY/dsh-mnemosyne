# -*- coding: utf-8 -*-
"""Truth Maintenance Engine — AND/OR justifications + budgeted propagation."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from typing import Any

from cognitive_memory.judgment.truth_value import TruthValue
from cognitive_memory.tms.models import (
    Belief,
    BeliefStatus,
    Justification,
    PropagateResult,
)
from cognitive_memory.tms.scc import strongly_connected_components


class TruthMaintenanceEngine:
    def __init__(self, *, max_nodes: int = 64, max_depth: int = 16) -> None:
        self.max_nodes = max(1, int(max_nodes))
        self.max_depth = max(1, int(max_depth))
        self._justifications: dict[str, Justification] = {}
        self._by_conclusion: dict[str, list[str]] = defaultdict(list)
        self._by_premise: dict[str, list[str]] = defaultdict(list)
        self._belief: dict[str, BeliefStatus] = {}
        self._premise_truth: dict[str, TruthValue] = {}

    def set_premise_truth(self, node_id: str, value: TruthValue | str) -> None:
        if isinstance(value, str):
            value = TruthValue(value)
        self._premise_truth[node_id] = value
        # leaf nodes without justifications mirror premise truth
        if node_id not in self._by_conclusion:
            if value is TruthValue.TRUE:
                self._belief[node_id] = BeliefStatus.IN
            elif value is TruthValue.FALSE:
                self._belief[node_id] = BeliefStatus.OUT
            else:
                self._belief[node_id] = BeliefStatus.UNKNOWN

    def add_justification(
        self,
        conclusion_id: str,
        premises: list[str],
        *,
        kind: str = "support",
        reason: str = "",
        actor: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Justification:
        jid = f"jst_{secrets.token_hex(6)}"
        just = Justification(
            justification_id=jid,
            conclusion_id=conclusion_id,
            premises=list(premises),
            kind=kind,
            valid=True,
            reason=reason,
            actor=actor,
            metadata=dict(metadata or {}),
        )
        self._justifications[jid] = just
        self._by_conclusion[conclusion_id].append(jid)
        for p in premises:
            self._by_premise[p].append(jid)
            self._belief.setdefault(p, BeliefStatus.OUT)
        self._belief.setdefault(conclusion_id, BeliefStatus.OUT)
        return just

    def retract_justification(self, justification_id: str) -> bool:
        just = self._justifications.get(justification_id)
        if not just:
            return False
        just.valid = False
        return True

    def belief(self, node_id: str) -> Belief:
        status = self._belief.get(node_id, BeliefStatus.OUT)
        supporting = [
            jid
            for jid in self._by_conclusion.get(node_id, [])
            if self._justifications[jid].valid
            and self._justification_holds(self._justifications[jid])
        ]
        return Belief(
            node_id=node_id,
            status=status,
            supporting_justification_ids=supporting,
            reason_codes=[status.value],
        )

    def dependents(self, node_id: str) -> list[str]:
        out: list[str] = []
        for jid in self._by_premise.get(node_id, []):
            just = self._justifications[jid]
            if just.conclusion_id not in out:
                out.append(just.conclusion_id)
        return out

    def _node_in(self, node_id: str) -> bool:
        return self._belief.get(node_id) is BeliefStatus.IN

    def _premise_holds(self, premise_id: str) -> TruthValue:
        if premise_id in self._premise_truth:
            return self._premise_truth[premise_id]
        st = self._belief.get(premise_id, BeliefStatus.OUT)
        if st is BeliefStatus.IN:
            return TruthValue.TRUE
        if st is BeliefStatus.OUT:
            return TruthValue.FALSE
        return TruthValue.UNKNOWN

    def _justification_holds(self, just: Justification) -> bool:
        if not just.valid:
            return False
        if not just.premises:
            return True  # empty AND = vacuously true (assertion)
        for p in just.premises:
            if self._premise_holds(p) is not TruthValue.TRUE:
                return False
        return True

    def _recompute_node(self, node_id: str) -> BeliefStatus:
        jids = self._by_conclusion.get(node_id, [])
        if not jids and node_id in self._premise_truth:
            tv = self._premise_truth[node_id]
            if tv is TruthValue.TRUE:
                return BeliefStatus.IN
            if tv is TruthValue.FALSE:
                return BeliefStatus.OUT
            return BeliefStatus.UNKNOWN
        # Contradictions win over support (fail-closed for contested claims)
        any_unknown = False
        support_in = False
        for jid in jids:
            just = self._justifications[jid]
            if not just.valid:
                continue
            if just.kind == "contradict":
                if self._justification_holds(just):
                    return BeliefStatus.OUT
                for p in just.premises:
                    if self._premise_holds(p) is TruthValue.UNKNOWN:
                        any_unknown = True
                continue
            if self._justification_holds(just):
                support_in = True
            else:
                for p in just.premises:
                    if self._premise_holds(p) is TruthValue.UNKNOWN:
                        any_unknown = True
        if support_in:
            return BeliefStatus.IN
        if any_unknown:
            return BeliefStatus.UNKNOWN
        return BeliefStatus.OUT

    def _dependency_edges(self) -> dict[str, set[str]]:
        """premise → conclusion edges for SCC."""
        edges: dict[str, set[str]] = defaultdict(set)
        for just in self._justifications.values():
            if not just.valid:
                continue
            for p in just.premises:
                edges[p].add(just.conclusion_id)
        return edges

    def mark_scc_unknown(self) -> list[str]:
        nodes = set(self._belief.keys()) | set(self._by_conclusion.keys())
        edges = self._dependency_edges()
        marked: list[str] = []
        for comp in strongly_connected_components(nodes, edges):
            if len(comp) < 2 and not any(n in edges.get(n,set()) for n in comp):
                continue
            # cycle without external premise support → UNKNOWN
            external = False
            for n in comp:
                for jid in self._by_conclusion.get(n, []):
                    just = self._justifications[jid]
                    if not just.valid:
                        continue
                    if any(p not in comp for p in just.premises) and self._justification_holds(
                        just
                    ):
                        external = True
                        break
                if external:
                    break
            if not external:
                for n in comp:
                    self._belief[n] = BeliefStatus.UNKNOWN
                    marked.append(n)
        return marked

    def propagate(
        self,
        seed_ids: list[str],
        *,
        max_nodes: int | None = None,
        max_depth: int | None = None,
        max_elapsed_ms: float = 1000,
    ) -> PropagateResult:
        budget = self.max_nodes if max_nodes is None else max(0,max_nodes)
        depth_lim = self.max_depth if max_depth is None else max(0,max_depth)
        started = time.perf_counter()
        q: deque[tuple[str, int]] = deque((s, 0) for s in seed_ids)
        visited: set[str] = set()
        changed: list[dict[str, Any]] = []
        exhausted = False

        while q:
            if len(visited) >= budget or (time.perf_counter()-started)*1000 >= max_elapsed_ms:
                exhausted = True
                break
            node_id, depth = q.popleft()
            if node_id in visited:
                continue
            if depth > depth_lim:
                exhausted = True
                continue
            visited.add(node_id)
            old = self._belief.get(node_id, BeliefStatus.OUT)
            new = self._recompute_node(node_id)
            self._belief[node_id] = new
            if old is not new:
                changed.append(
                    {
                        "node_id": node_id,
                        "from": old.value,
                        "to": new.value,
                    }
                )
            # Seeds always fan out; otherwise only propagate on change
            if old is not new or depth == 0:
                for dep in self.dependents(node_id):
                    if dep not in visited:
                        q.append((dep, depth + 1))

        if len(self._belief)>budget or (time.perf_counter()-started)*1000>=max_elapsed_ms:
            exhausted=True
            scc_unknown=[]
        else:
            scc_unknown = self.mark_scc_unknown()
        reasons = []
        if exhausted:
            reasons.append("budget_exceeded")
        if scc_unknown:
            reasons.append("scc_marked_unknown")
        return PropagateResult(
            ok=not exhausted,
            changed=changed,
            visited=len(visited),
            budget_exhausted=exhausted,
            scc_unknown=scc_unknown,
            reason_codes=reasons,
        )

    def impact(self, node_id: str, *, max_nodes: int = 32) -> dict[str, Any]:
        """Downstream impact preview without mutating beyond a dry walk."""
        seen: list[str] = []
        q = deque([node_id])
        while q and len(seen) < max_nodes:
            n = q.popleft()
            if n in seen:
                continue
            seen.append(n)
            for d in self.dependents(n):
                if d not in seen:
                    q.append(d)
        return {
            "ok": True,
            "seed": node_id,
            "affected": seen[1:],
            "count": max(0, len(seen) - 1),
            "belief": self.belief(node_id).model_dump(mode="json"),
            "note": "impact_preview_no_auto_overwrite",
        }
