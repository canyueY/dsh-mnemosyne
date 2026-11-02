# -*- coding: utf-8 -*-
"""Claim/lifecycle invariants for P7 earliest-deviation detection."""

from __future__ import annotations

from typing import Any


def check_claim_invariants(claim: dict[str, Any] | None, *, evidence_n: int = 0) -> dict[str, Any]:
    """Return ok=False with violations when Claim state breaks hard rules."""
    violations: list[dict[str, str]] = []
    if not claim:
        return {
            "ok": False,
            "violations": [{"code": "claim_missing", "detail": "claim row absent"}],
        }
    life = str(claim.get("lifecycle") or "")
    ver = str(claim.get("verification_status") or "")
    # More-specific codes first (pipeline uses violations[0])
    if life == "proposed" and ver == "verified":
        violations.append(
            {
                "code": "silent_verified_on_proposed",
                "detail": "proposed must stay unverified",
            }
        )
    elif ver == "verified" and life != "verified":
        violations.append(
            {
                "code": "verified_status_lifecycle_mismatch",
                "detail": f"verification={ver} lifecycle={life}",
            }
        )
    if life == "verified" and evidence_n < 1:
        violations.append(
            {
                "code": "verified_without_evidence",
                "detail": "verified claim requires ≥1 evidence link",
            }
        )
    if claim.get("redirects_to") and life not in ("superseded", "invalidated"):
        violations.append(
            {
                "code": "redirect_without_tombstone",
                "detail": f"redirects_to set but lifecycle={life}",
            }
        )
    return {"ok": not violations, "violations": violations, "claim_id": claim.get("id")}
