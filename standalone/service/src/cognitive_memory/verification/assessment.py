"""Database-backed evidence assessment; caller booleans are never proof."""
from datetime import datetime, timezone
from cognitive_memory.evidence.object_store import EvidenceObjectStore

POLICY_VERSION = "verification.v2"


def assess_evidence(db, claim, refs, *, principal=None):
    refs = list(dict.fromkeys(refs))
    if not refs:
        return {"ok": False, "detail": "verified_requires_evidence_refs"}
    store = EvidenceObjectStore(db)
    evidence = []
    for ref in refs:
        ev = store.get(ref)
        if ev is None:
            return {"ok": False, "detail": "evidence_missing"}
        if ev["bank_id"] != claim["bank_id"]:
            return {"ok": False, "detail": "evidence_scope_denied"}
        if principal and not principal.allows_bank(ev["bank_id"]):
            return {"ok": False, "detail": "evidence_scope_denied"}
        if ev["validation_state"] not in {"valid", "validated", "verified", "active"}:
            return {"ok": False, "detail": "evidence_not_valid"}
        if ev["provenance_status"] != "complete" or not ev["chain"] or not ev["source_ref"]:
            return {"ok": False, "detail": "insufficient_provenance"}
        security = ev.get("security") or {}
        if principal:
            if security.get("owner_id") and security["owner_id"] != principal.owner_id:
                if not set(security.get("authorized_grants", [])) & set(principal.authorized_grants):
                    return {"ok": False, "detail": "evidence_scope_denied"}
            if security.get("purpose_constraints") and "verify" not in security["purpose_constraints"]:
                return {"ok": False, "detail": "evidence_purpose_denied"}
        meta = ev.get("metadata") or {}
        # Reviewed provenance does not turn a model's completion into observation.
        if ev["source_type"] in {"inference", "summary", "model_output", "self_eval", "generated"} or meta.get("epistemic") in {"inference", "inferred", "inferred_signal", "generated", "summary"}:
            return {"ok": False, "detail": "derived_evidence_requires_external_validation"}
        if not store.lineage(ref)["ok"]:
            return {"ok": False, "detail": "evidence_lineage_unresolved"}
        if meta.get("scope") is not None and meta["scope"] != claim.get("scope", {}):
            return {"ok": False, "detail": "evidence_scope_mismatch"}
        expiry = meta.get("valid_until")
        if expiry:
            try:
                if datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    return {"ok": False, "detail": "evidence_expired"}
            except (ValueError, TypeError, AttributeError):
                return {"ok": False, "detail": "evidence_expiry_unknown"}
        if ev["source_type"] == "observation":
            with db.exclusive() as conn:
                if conn.execute("SELECT 1 FROM observations WHERE id=?", (ev["source_ref"],)).fetchone() is None:
                    return {"ok": False, "detail": "evidence_source_missing"}
        evidence.append(ev)
    with db.exclusive() as conn:
        counters = conn.execute(
            "SELECT evidence_ref FROM claim_evidence WHERE claim_id=? AND role IN ('counter','contradicts')",
            (claim["id"],),
        ).fetchall()
    for row in counters:
        ev = store.get(row[0])
        # A missing/unknown counter cannot silently be treated as refuted.
        if ev is None or ev["validation_state"] not in {"revoked","invalidated","rejected","expired"}:
            return {"ok": False, "detail": "disputed", "counterevidence_refs":[row[0]]}
    if len(refs) > 1:
        indep = store.independence_conflict(refs)
        if not indep["independent"]:
            return {"ok": False, "detail":"evidence_not_independent", "independence":indep}
    return {"ok": True, "evidence_refs":refs, "policy_version":POLICY_VERSION}
