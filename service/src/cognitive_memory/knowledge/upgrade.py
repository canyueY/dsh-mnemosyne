# -*- coding: utf-8 -*-
"""Batch episodic/working → Claim candidate → Knowledge *proposed*.

Never auto-ACTIVE without verified Claim + Evidence.
Never treats Verified/ACTIVE as absolute truth.
Does not rewrite memory.type.
"""
from __future__ import annotations

import json
from typing import Any

from cognitive_memory.claims.store import ClaimStore
from cognitive_memory.knowledge.store import KnowledgeStore
from cognitive_memory.storage.db import Database

TAG = "ew_batch_knowledge"
ALLOWED = frozenset({"episodic", "working"})


def _title(content: str, mid: str) -> str:
    text = " ".join(str(content or "").split())
    if not text:
        return f"记忆候选 {mid}"
    return text[:48]


def batch_from_memories(
    db: Database,
    *,
    types: list[str],
    after_id: str = "",
    limit: int = 200,
    confirm: bool = False,
) -> dict[str, Any]:
    kinds = [t for t in types if t in ALLOWED]
    if not kinds:
        return {"ok": False, "detail": "types_must_be_episodic_or_working"}
    if not confirm:
        return {"ok": False, "detail": "confirm_required"}
    lim = max(1, min(int(limit or 200), 500))
    claim_store = ClaimStore(db)
    kn_store = KnowledgeStore(db)
    with db.exclusive() as conn:
        placeholders = ",".join("?" * len(kinds))
        args: list[Any] = list(kinds)
        sql = f"""
            SELECT id, content, bank_id, type, status
              FROM memories
             WHERE type IN ({placeholders})
               AND COALESCE(status,'') != 'invalidated'
        """
        if after_id:
            sql += " AND id > ?"
            args.append(after_id)
        sql += " ORDER BY id LIMIT ?"
        args.append(lim)
        rows = conn.execute(sql, tuple(args)).fetchall()
        claimed = {
            r[0]
            for r in conn.execute(
                "SELECT source_memory_id, id FROM claims WHERE source_memory_id IS NOT NULL AND source_memory_id != ''"
            )
        }
        kn_claims = set()
        for r in conn.execute("SELECT claim_ids_json FROM knowledge_nodes"):
            try:
                ids = json.loads(r[0] or "[]")
            except json.JSONDecodeError:
                ids = []
            if isinstance(ids, list):
                kn_claims.update(str(x) for x in ids)

    created_claims = 0
    reused_claims = 0
    created_knowledge = 0
    skipped_knowledge = 0
    activated = 0
    errors: list[dict[str, str]] = []
    last = after_id
    for row in rows:
        mid = str(row["id"])
        last = mid
        content = str(row["content"] or "").strip()
        if not content:
            continue
        bank = str(row["bank_id"] or "personal")
        try:
            if mid in claimed:
                with db.exclusive() as conn:
                    hit = conn.execute(
                        "SELECT id, lifecycle FROM claims WHERE source_memory_id=? ORDER BY created_at LIMIT 1",
                        (mid,),
                    ).fetchone()
                cid = str(hit["id"]) if hit else ""
                reused_claims += 1
            else:
                out = claim_store.propose_from_text(
                    content,
                    bank_id=bank,
                    source_memory_id=mid,
                    persist_unresolved=True,
                )
                cid = str(out.get("same_as") or (out.get("claim") or {}).get("id") or out.get("claim_id") or "")
                if out.get("same_as"):
                    reused_claims += 1
                elif cid:
                    created_claims += 1
                    claimed.add(mid)
            if not cid:
                errors.append({"id": mid, "detail": "claim_not_created"})
                continue
            if cid in kn_claims:
                skipped_knowledge += 1
                continue
            name = _title(content, mid)
            proposed = kn_store.propose(
                kind="claim_knowledge",
                name=name,
                description="batch from episodic/working memory; proposed only unless already verified",
                claim_ids=[cid],
                payload={
                    "upgrade_tag": TAG,
                    "source_memory_id": mid,
                    "source_memory_type": row["type"],
                    "inferred": True,
                    "not_absolute_truth": True,
                    "not_71k_type_rewrite": True,
                },
            )
            if not proposed.get("ok"):
                errors.append({"id": mid, "detail": str(proposed.get("detail") or "knowledge_propose_failed")})
                continue
            created_knowledge += 1
            kn_claims.add(cid)
            kid = (proposed.get("knowledge") or {}).get("id")
            if kid:
                act = kn_store.set_status(kid, "active")
                if act.get("ok"):
                    activated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"id": mid, "detail": str(exc)[:200]})

    return {
        "ok": True,
        "processed": len(rows),
        "next_cursor": last if len(rows) == lim else "",
        "done": len(rows) < lim,
        "created_claims": created_claims,
        "reused_claims": reused_claims,
        "created_knowledge": created_knowledge,
        "skipped_knowledge": skipped_knowledge,
        "activated": activated,
        "errors": errors[:50],
        "error_count": len(errors),
        "note": "ACTIVE only when Claim is verified and has Evidence; otherwise proposed. Not a type rewrite.",
    }
