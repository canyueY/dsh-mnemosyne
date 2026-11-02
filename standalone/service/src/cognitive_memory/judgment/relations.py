# -*- coding: utf-8 -*-
"""Claim relation results — same_as / contradicts / qualifies / supports."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from cognitive_memory.claims.ast import Atom, ClaimAST, ClaimNode, Conditional, Negation, Temporal
from cognitive_memory.claims.normalize import normalize_ast, normalize_scope, _canon_predicate


def _canon_text_loose(value: str) -> str:
    return str(value or "").strip().lower()


class ClaimRelation(str, Enum):
    SAME_AS = "same_as"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    SUPPORTS = "supports"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class RelationDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ABSTAINED = "abstained"


class RelationResult(BaseModel):
    candidate_relation: ClaimRelation
    decision: RelationDecision
    final_relation: ClaimRelation
    reason_codes: list[str] = Field(default_factory=list)
    compared_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    algorithm_version: str = "relation.v2"

    @model_validator(mode="after")
    def _legal_combo(self) -> "RelationResult":
        validate_relation_result(self.decision, self.final_relation)
        return self


def validate_relation_result(decision: RelationDecision, final_relation: ClaimRelation) -> None:
    if decision is RelationDecision.ACCEPTED and final_relation is ClaimRelation.UNKNOWN:
        raise ValueError("accepted_final_cannot_be_unknown")
    if decision is RelationDecision.REJECTED and final_relation not in (
        ClaimRelation.UNRELATED,
        ClaimRelation.UNKNOWN,
    ):
        raise ValueError("rejected_final_must_be_unrelated_or_unknown")
    if decision is RelationDecision.ABSTAINED and final_relation is not ClaimRelation.UNKNOWN:
        raise ValueError("abstained_final_must_be_unknown")


def _accepted(rel: ClaimRelation, reasons: list[str], compared: list[str]) -> RelationResult:
    return RelationResult(
        candidate_relation=rel,
        decision=RelationDecision.ACCEPTED,
        final_relation=rel,
        reason_codes=reasons,
        compared_fields=compared,
    )


def _rejected_unrelated(candidate: ClaimRelation, reasons: list[str], compared: list[str]) -> RelationResult:
    return RelationResult(
        candidate_relation=candidate,
        decision=RelationDecision.REJECTED,
        final_relation=ClaimRelation.UNRELATED,
        reason_codes=reasons,
        compared_fields=compared,
    )


def _abstain(candidate: ClaimRelation, reasons: list[str], unresolved: list[str], compared: list[str]) -> RelationResult:
    return RelationResult(
        candidate_relation=candidate,
        decision=RelationDecision.ABSTAINED,
        final_relation=ClaimRelation.UNKNOWN,
        reason_codes=reasons,
        compared_fields=compared,
        unresolved_fields=unresolved,
    )


def _unwrap(node: ClaimNode, *, polarity: int = 1) -> tuple[ClaimNode | None, int, list[str]]:
    """Return (core_node, polarity, wrappers). polarity -1 if odd negations."""
    wrappers: list[str] = []
    cur: ClaimNode | None = node
    pol = polarity
    while cur is not None:
        kind = getattr(cur, "kind", None)
        if kind == "negation" or isinstance(cur, Negation):
            wrappers.append("negation")
            pol *= -1
            cur = cur.target  # type: ignore[attr-defined]
            continue
        if kind == "temporal" or isinstance(cur, Temporal):
            wrappers.append("temporal")
            cur = cur.target  # type: ignore[attr-defined]
            continue
        break
    return cur, pol, wrappers


def _atom_signature(node: ClaimNode | None) -> dict[str, Any] | None:
    if node is None:
        return None
    if isinstance(node, Atom) or getattr(node, "kind", None) == "atom":
        return {
            "subject": getattr(node, "subject", ""),
            "predicate": _canon_predicate(str(getattr(node, "predicate", ""))),
            "arguments": list(getattr(node, "arguments", []) or []),
            "modality": getattr(node, "modality", None),
        }
    return None


_OPPOSITE = {
    ("likes", "dislikes"),
    ("dislikes", "likes"),
}


def evaluate_same_as(a: ClaimAST, b: ClaimAST) -> RelationResult:
    """Deterministic same_as. No LLM. Semantic similarity is not enough."""
    compared = ["ast_version", "normalized_root", "normalized_scope"]
    if a.ast_version != b.ast_version:
        return _abstain(ClaimRelation.SAME_AS, ["ast_version_mismatch"], ["ast_version"], compared)
    if a.is_unresolved() or b.is_unresolved():
        return _abstain(ClaimRelation.SAME_AS, ["unresolved_ast"], ["root"], compared)
    na = normalize_ast(a)
    nb = normalize_ast(b)
    if na == nb:
        return _accepted(ClaimRelation.SAME_AS, ["normalized_equal"], compared)
    if normalize_scope(a.scope) != normalize_scope(b.scope):
        return _rejected_unrelated(ClaimRelation.SAME_AS, ["scope_mismatch"], compared)
    return _rejected_unrelated(ClaimRelation.SAME_AS, ["normalized_not_equal"], compared)


def evaluate_contradicts(a: ClaimAST, b: ClaimAST) -> RelationResult:
    compared = ["root_polarity", "atom_signature", "normalized_scope"]
    if a.is_unresolved() or b.is_unresolved():
        return _abstain(ClaimRelation.CONTRADICTS, ["unresolved_ast"], ["root"], compared)
    ca, pa, _ = _unwrap(a.root)
    cb, pb, _ = _unwrap(b.root)
    sa = _atom_signature(ca)
    sb = _atom_signature(cb)
    if not sa or not sb:
        # Conditional contradiction: only when both are atoms after unwrap fails
        return _abstain(ClaimRelation.CONTRADICTS, ["non_atom_core"], ["root"], compared)
    same_args = sorted(sa["arguments"]) == sorted(sb["arguments"])
    same_subj = _canon_text_loose(sa["subject"]) == _canon_text_loose(sb["subject"])
    if sa["predicate"] == sb["predicate"] and same_args and same_subj and pa != pb:
        return _accepted(ClaimRelation.CONTRADICTS, ["negation_polarity_flip"], compared)
    if (sa["predicate"], sb["predicate"]) in _OPPOSITE and same_args and same_subj:
        return _accepted(ClaimRelation.CONTRADICTS, ["opposite_predicate"], compared)
    return _rejected_unrelated(ClaimRelation.CONTRADICTS, ["no_contradiction"], compared)


def evaluate_qualifies(a: ClaimAST, b: ClaimAST) -> RelationResult:
    """A qualifies B when A is a scoped/temporal/conditional narrowing of B's core."""
    compared = ["wrappers", "atom_signature", "scope"]
    if a.is_unresolved() or b.is_unresolved():
        return _abstain(ClaimRelation.QUALIFIES, ["unresolved_ast"], ["root"], compared)
    # Conditional: A.consequence same_as B
    if isinstance(a.root, Conditional) or getattr(a.root, "kind", None) == "conditional":
        try:
            cons = ClaimAST(ast_version=a.ast_version, root=a.root.consequence, scope=a.scope)  # type: ignore[attr-defined]
            core = evaluate_same_as(cons, b)
            if core.decision is RelationDecision.ACCEPTED:
                return _accepted(ClaimRelation.QUALIFIES, ["conditional_consequence_matches"], compared)
        except Exception:
            pass
    ca, pa, wa = _unwrap(a.root)
    cb, pb, wb = _unwrap(b.root)
    if pa != pb:
        return _rejected_unrelated(ClaimRelation.QUALIFIES, ["polarity_mismatch"], compared)
    sa = _atom_signature(ca)
    sb = _atom_signature(cb)
    if not sa or not sb:
        return _abstain(ClaimRelation.QUALIFIES, ["non_atom_core"], ["root"], compared)
    if sa["predicate"] != sb["predicate"]:
        return _rejected_unrelated(ClaimRelation.QUALIFIES, ["predicate_mismatch"], compared)
    # Temporal wrapper on one side only → qualifies
    if ("temporal" in wa) != ("temporal" in wb) and sorted(sa["arguments"]) == sorted(sb["arguments"]):
        return _accepted(ClaimRelation.QUALIFIES, ["temporal_narrowing"], compared)
    # Scope subset: A's scope has all of B's keys + more
    sa_scope = normalize_scope(a.scope)
    sb_scope = normalize_scope(b.scope)
    if sa_scope and sb_scope and sa_scope != sb_scope:
        if all(sa_scope.get(k) == v for k, v in sb_scope.items()) and len(sa_scope) > len(sb_scope):
            if sorted(sa["arguments"]) == sorted(sb["arguments"]):
                return _accepted(ClaimRelation.QUALIFIES, ["scope_narrowing"], compared)
    # Argument specialization: A's args are strict superset of B's
    set_a = set(sa["arguments"])
    set_b = set(sb["arguments"])
    if set_b and set_b < set_a and sa["predicate"] == sb["predicate"]:
        return _accepted(ClaimRelation.QUALIFIES, ["argument_specialization"], compared)
    return _rejected_unrelated(ClaimRelation.QUALIFIES, ["no_qualification"], compared)


def evaluate_supports(a: ClaimAST, b: ClaimAST) -> RelationResult:
    """A supports B when they share core atom and A is not a contradiction/qualification."""
    compared = ["atom_signature", "modality"]
    if a.is_unresolved() or b.is_unresolved():
        return _abstain(ClaimRelation.SUPPORTS, ["unresolved_ast"], ["root"], compared)
    # same_as is stronger than supports
    if evaluate_same_as(a, b).decision is RelationDecision.ACCEPTED:
        return _rejected_unrelated(ClaimRelation.SUPPORTS, ["same_as_not_support"], compared)
    if evaluate_contradicts(a, b).decision is RelationDecision.ACCEPTED:
        return _rejected_unrelated(ClaimRelation.SUPPORTS, ["contradiction_blocks_support"], compared)
    ca, pa, _ = _unwrap(a.root)
    cb, pb, _ = _unwrap(b.root)
    if pa != pb:
        return _rejected_unrelated(ClaimRelation.SUPPORTS, ["polarity_mismatch"], compared)
    sa = _atom_signature(ca)
    sb = _atom_signature(cb)
    if not sa or not sb:
        return _abstain(ClaimRelation.SUPPORTS, ["non_atom_core"], ["root"], compared)
    if sa["predicate"] == sb["predicate"] and (
        not sa["arguments"]
        or not sb["arguments"]
        or set(sa["arguments"]) & set(sb["arguments"])
    ):
        return _accepted(ClaimRelation.SUPPORTS, ["shared_predicate_overlap"], compared)
    return _rejected_unrelated(ClaimRelation.SUPPORTS, ["no_support"], compared)


def evaluate_relation(a: ClaimAST, b: ClaimAST) -> RelationResult:
    """Priority: same_as → contradicts → qualifies → supports → unrelated."""
    same = evaluate_same_as(a, b)
    if same.decision is RelationDecision.ACCEPTED:
        return same
    if same.decision is RelationDecision.ABSTAINED:
        return same
    contra = evaluate_contradicts(a, b)
    if contra.decision is RelationDecision.ACCEPTED:
        return contra
    qual = evaluate_qualifies(a, b)
    if qual.decision is RelationDecision.ACCEPTED:
        return qual
    # Also check reverse qualifies (B qualifies A) → still report qualifies
    qual_rev = evaluate_qualifies(b, a)
    if qual_rev.decision is RelationDecision.ACCEPTED:
        return RelationResult(
            candidate_relation=ClaimRelation.QUALIFIES,
            decision=RelationDecision.ACCEPTED,
            final_relation=ClaimRelation.QUALIFIES,
            reason_codes=qual_rev.reason_codes + ["reversed"],
            compared_fields=qual_rev.compared_fields,
            algorithm_version=qual_rev.algorithm_version,
        )
    supp = evaluate_supports(a, b)
    if supp.decision is RelationDecision.ACCEPTED:
        return supp
    return RelationResult(
        candidate_relation=ClaimRelation.UNRELATED,
        decision=RelationDecision.ACCEPTED,
        final_relation=ClaimRelation.UNRELATED,
        reason_codes=["no_typed_relation"],
        compared_fields=["normalized_root"],
        algorithm_version="relation.v2",
    )
