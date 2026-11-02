# -*- coding: utf-8 -*-
"""P4 R17 / P8：Memory 失效 → Evidence → Claim → Knowledge → Strategy → TMS/cache 级联。

Claim 永不静默硬删；forget → tombstone。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database
from cognitive_memory.tms.store import TmsStore


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "cle") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_list_contains(raw: str | None, needle: str) -> bool:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return False
    return needle in {str(x) for x in items}


def cascade_memory_invalidated(
    db: Database,
    memory_id: str,
    *,
    hard: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    """Propagate delete/tombstone:

    Memory → Evidence → Claim → Knowledge → Strategy/Experience → TMS → grant cache note.

    Never invents replacements. Hard delete of memory does not hard-delete Claims —
    Claims keep rows with lifecycle superseded, verification unverified.
    """
    db.ensure_schema()
    tms = TmsStore(db)
    now = _now()
    claims_touched: list[dict[str, Any]] = []
    evidence_touched: list[str] = []
    knowledge_touched: list[str] = []
    strategies_touched: list[str] = []
    experiences_touched: list[str] = []

    with db.transaction() as conn:
        # Claims sourced from this memory
        rows = conn.execute(
            """
            SELECT id, lifecycle, verification_status FROM claims
            WHERE source_memory_id=? AND redirects_to IS NULL
            """,
            (memory_id,),
        ).fetchall()
        for row in rows:
            cid = row["id"]
            conn.execute(
                """
                UPDATE claims
                SET lifecycle='superseded',
                    verification_status='unverified',
                    updated_at=?
                WHERE id=?
                """,
                (now, cid),
            )
            conn.execute(
                """
                INSERT INTO claim_lifecycle_events(
                  id, claim_id, from_status, to_status, reason, actor,
                  policy_version, evidence_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("cle"),
                    cid,
                    row["lifecycle"] or "proposed",
                    "superseded",
                    f"memory_tombstone:{memory_id}:hard={hard}",
                    actor,
                    "claim.lifecycle.v1",
                    f'["{memory_id}"]',
                    now,
                ),
            )
            claims_touched.append({"claim_id": cid, "lifecycle": "superseded"})

        # Evidence objects linked via legacy_memory_id
        try:
            ev_rows = conn.execute(
                "SELECT id FROM evidences WHERE legacy_memory_id=?",
                (memory_id,),
            ).fetchall()
        except Exception:
            ev_rows = []
        for er in ev_rows:
            eid = er["id"]
            meta = {"tombstone": True, "tombstone_memory_id": memory_id}
            conn.execute(
                """
                UPDATE evidences
                SET validation_state='revoked',
                    provenance_status='unknown',
                    updated_at=?,
                    metadata_json=?
                WHERE id=?
                """,
                (now, json.dumps(meta, ensure_ascii=False), eid),
            )
            evidence_touched.append(eid)

        # claim_evidence links that reference this memory id directly
        try:
            conn.execute(
                """
                UPDATE claim_evidence
                SET role='revoked'
                WHERE evidence_ref=? OR evidence_ref=?
                """,
                (memory_id, f"mem:{memory_id}"),
            )
        except Exception:
            pass

        claim_ids = {c["claim_id"] for c in claims_touched}

        # Knowledge nodes that cite superseded claims → retire (tombstone status)
        try:
            kn_rows = conn.execute(
                "SELECT id, claim_ids_json, status FROM knowledge_nodes"
            ).fetchall()
        except Exception:
            kn_rows = []
        for kn in kn_rows:
            if any(_json_list_contains(kn["claim_ids_json"], cid) for cid in claim_ids):
                conn.execute(
                    """
                    UPDATE knowledge_nodes
                    SET status='retired',
                        updated_at=?,
                        validation_json=?
                    WHERE id=?
                    """,
                    (
                        now,
                        json.dumps(
                            {
                                "tombstone": True,
                                "reason": "memory_cascade",
                                "memory_id": memory_id,
                                "claim_ids": sorted(claim_ids),
                            },
                            ensure_ascii=False,
                        ),
                        kn["id"],
                    ),
                )
                knowledge_touched.append(kn["id"])

        # Experiences referencing memory / claims — mark outcome tombstoned in payload
        try:
            ex_rows = conn.execute(
                "SELECT id, memory_ids_json, claim_ids_json, outcome_json FROM experiences"
            ).fetchall()
        except Exception:
            ex_rows = []
        for ex in ex_rows:
            hit_mem = _json_list_contains(ex["memory_ids_json"], memory_id)
            hit_claim = any(
                _json_list_contains(ex["claim_ids_json"], cid) for cid in claim_ids
            )
            if not (hit_mem or hit_claim):
                continue
            try:
                outcome = json.loads(ex["outcome_json"] or "{}")
            except json.JSONDecodeError:
                outcome = {}
            if not isinstance(outcome, dict):
                outcome = {"value": outcome}
            outcome["tombstone"] = True
            outcome["tombstone_memory_id"] = memory_id
            conn.execute(
                "UPDATE experiences SET outcome_json=? WHERE id=?",
                (json.dumps(outcome, ensure_ascii=False), ex["id"]),
            )
            experiences_touched.append(ex["id"])

        # Strategies linked via knowledge strategy_id — soft-retire for review
        try:
            for kid in knowledge_touched:
                row = conn.execute(
                    "SELECT strategy_id FROM knowledge_nodes WHERE id=?", (kid,)
                ).fetchone()
                sid = (row["strategy_id"] if row else None) or None
                if not sid:
                    continue
                conn.execute(
                    """
                    UPDATE strategies
                    SET status='retired', updated_at=?
                    WHERE id=? AND status NOT IN ('retired', 'archived')
                    """,
                    (now, sid),
                )
                strategies_touched.append(sid)
        except Exception:
            pass

    # TMS invalidate outside the same exclusive txn (uses own transactions)
    tms_results = []
    tms_results.append(
        tms.invalidate_node(f"mem:{memory_id}", reason="memory_tombstone", actor=actor)
    )
    for c in claims_touched:
        tms_results.append(
            tms.invalidate_node(c["claim_id"], reason="memory_tombstone", actor=actor)
        )
    for eid in evidence_touched:
        tms_results.append(
            tms.invalidate_node(eid, reason="memory_tombstone", actor=actor)
        )
    for kid in knowledge_touched:
        tms_results.append(
            tms.invalidate_node(kid, reason="memory_tombstone", actor=actor)
        )

    # Visibility / grant caches are process-local; drop grant cache entirely on cascade
    cache_note = {"grant_cache_invalidated": False}
    try:
        from cognitive_memory.identity.grant_cache import invalidate_grant_cache

        n = invalidate_grant_cache(None)
        cache_note = {"grant_cache_invalidated": True, "entries_dropped": n}
    except Exception as exc:  # noqa: BLE001
        cache_note = {"grant_cache_invalidated": False, "error": str(exc)}

    return {
        "ok": True,
        "memory_id": memory_id,
        "hard": hard,
        "claims_superseded": claims_touched,
        "evidence_revoked": evidence_touched,
        "knowledge_retired": knowledge_touched,
        "strategies_retired": strategies_touched,
        "experiences_tombstoned": experiences_touched,
        "tms": tms_results,
        "cache": cache_note,
        "chain": [
            "memory",
            "evidence",
            "claim",
            "knowledge",
            "strategy",
            "experience",
            "tms",
            "cache",
        ],
        "note": "claims_kept_as_tombstone_rows_not_hard_deleted",
        "generated_at": now,
    }
