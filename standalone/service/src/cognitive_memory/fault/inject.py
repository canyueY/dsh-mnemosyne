# -*- coding: utf-8 -*-
"""Controlled fault injection for P7 repair validation (test / drill only)."""

from __future__ import annotations

from typing import Any

from cognitive_memory.storage.db import Database


def inject_redirect_without_tombstone(
    db: Database, *, claim_id: str, redirects_to: str
) -> dict[str, Any]:
    """Set redirects_to while leaving lifecycle as proposed (invariant break)."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE claims SET redirects_to=? WHERE id=?",
            (redirects_to, claim_id),
        )
    return {
        "ok": True,
        "injected": "redirect_without_tombstone",
        "claim_id": claim_id,
        "redirects_to": redirects_to,
    }


def inject_verified_without_evidence(db: Database, *, claim_id: str) -> dict[str, Any]:
    """Force lifecycle=verified with zero claim_evidence links."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM claim_evidence WHERE claim_id=?", (claim_id,))
        conn.execute(
            """
            UPDATE claims
            SET lifecycle='verified', verification_status='verified'
            WHERE id=?
            """,
            (claim_id,),
        )
        conn.execute(
            """
            INSERT INTO claim_lifecycle_events(
              id, claim_id, from_status, to_status, reason, actor,
              policy_version, evidence_refs_json, created_at
            ) VALUES (
              lower(hex(randomblob(6))), ?, 'proposed', 'verified',
              'fault_inject:verified_without_evidence', 'inject',
              'claim.lifecycle.v1', '[]',
              strftime('%Y-%m-%dT%H:%M:%SZ','now')
            )
            """,
            (claim_id,),
        )
    return {"ok": True, "injected": "verified_without_evidence", "claim_id": claim_id}


def inject_silent_verified_on_proposed(db: Database, *, claim_id: str) -> dict[str, Any]:
    """Keep lifecycle=proposed but set verification_status=verified (silent Verified)."""
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE claims
            SET lifecycle='proposed', verification_status='verified'
            WHERE id=?
            """,
            (claim_id,),
        )
        conn.execute(
            """
            INSERT INTO claim_lifecycle_events(
              id, claim_id, from_status, to_status, reason, actor,
              policy_version, evidence_refs_json, created_at
            ) VALUES (
              lower(hex(randomblob(6))), ?, 'proposed', 'proposed',
              'fault_inject:silent_verified_on_proposed', 'inject',
              'claim.lifecycle.v1', '[]',
              strftime('%Y-%m-%dT%H:%M:%SZ','now')
            )
            """,
            (claim_id,),
        )
    return {
        "ok": True,
        "injected": "silent_verified_on_proposed",
        "claim_id": claim_id,
    }


def inject_verified_status_lifecycle_mismatch(
    db: Database, *, claim_id: str
) -> dict[str, Any]:
    """verification_status=verified while lifecycle stays supported (mismatch)."""
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE claims
            SET lifecycle='supported', verification_status='verified'
            WHERE id=?
            """,
            (claim_id,),
        )
        conn.execute(
            """
            INSERT INTO claim_lifecycle_events(
              id, claim_id, from_status, to_status, reason, actor,
              policy_version, evidence_refs_json, created_at
            ) VALUES (
              lower(hex(randomblob(6))), ?, 'proposed', 'supported',
              'fault_inject:verified_status_lifecycle_mismatch', 'inject',
              'claim.lifecycle.v1', '[]',
              strftime('%Y-%m-%dT%H:%M:%SZ','now')
            )
            """,
            (claim_id,),
        )
    return {
        "ok": True,
        "injected": "verified_status_lifecycle_mismatch",
        "claim_id": claim_id,
    }


INJECTORS = {
    "redirect_without_tombstone": inject_redirect_without_tombstone,
    "verified_without_evidence": inject_verified_without_evidence,
    "silent_verified_on_proposed": inject_silent_verified_on_proposed,
    "verified_status_lifecycle_mismatch": inject_verified_status_lifecycle_mismatch,
}
