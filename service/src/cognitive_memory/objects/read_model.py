# -*- coding: utf-8 -*-
"""Authorized CognitiveObject queries. Server-side SecurityContext; no existence leak."""

from __future__ import annotations

from typing import Any

from cognitive_memory.governance.access import AccessContext
from cognitive_memory.objects.envelope import (
    from_claim_row,
    from_evidence_row,
    from_knowledge_row,
    from_memory_row,
    from_observation_row,
    kind_from_id,
    relation_envelope,
)
from cognitive_memory.storage.db import Database

NEIGHBORHOOD_CAP = 40
LIST_CAP = 50


class ObjectUnavailable(Exception):
    """Same response for missing and unauthorized."""


def _disclosure(access: AccessContext, **extra: Any) -> dict[str, Any]:
    channel = "node_admin_diagnostic" if access.is_node_admin else "agent_grant"
    pack = {
        "channel": channel,
        "actor_kind": access.kind,
        "actor_id": access.actor_id if access.is_node_admin else access.actor_id,
        "truncated": False,
        "reason": None,
    }
    pack.update(extra)
    return pack


def _banks(access: AccessContext) -> list[str] | None:
    if access.is_node_admin:
        return None
    return list(access.banks or [])


def _ok_bank(access: AccessContext, bank_id: str | None) -> bool:
    allowed = _banks(access)
    if allowed is None:
        return True
    if not allowed:
        return False
    if not bank_id:
        return False
    return bank_id in allowed


class CognitiveObjectStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _row(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            hit = conn.execute(sql, params).fetchone()
        return dict(hit) if hit else None

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            hits = conn.execute(sql, params).fetchall()
        return [dict(x) for x in hits]

    def _count(self, sql: str, params: tuple = ()) -> int:
        with self.db.transaction() as conn:
            hit = conn.execute(sql, params).fetchone()
        return int(hit[0] if hit else 0)

    def aggregates(self, access: AccessContext) -> dict[str, Any]:
        allowed = _banks(access)
        def cnt(table: str, bank_col: str | None = "bank_id") -> int:
            if allowed is not None and not allowed:
                return 0
            if allowed is not None and not bank_col:
                # Agent 不得从无 bank 列的表读取总量（会泄漏 Observation/Knowledge/Relation 规模）
                return 0
            if allowed is None or not bank_col:
                return self._count(f"SELECT COUNT(*) FROM {table}")
            ph = ",".join("?" * len(allowed))
            return self._count(f"SELECT COUNT(*) FROM {table} WHERE {bank_col} IN ({ph})", tuple(allowed))

        counts = {
            "Memory": cnt("memories"),
            "Observation": cnt("observations", None),
            "Evidence": cnt("evidences"),
            "Claim": cnt("claims"),
            "Knowledge": cnt("knowledge_nodes", None),
            "Relation": cnt("memory_relations", None),
        }
        return {"ok": True, "counts": counts, "disclosure": _disclosure(access)}

    def get(self, object_id: str, access: AccessContext) -> dict[str, Any]:
        env = self._load(object_id)
        if not env:
            raise ObjectUnavailable()
        bank = (env.get("ref") or {}).get("bank_id")
        if bank == "unknown":
            bank = None
        if not access.is_node_admin:
            kind = (env.get("ref") or {}).get("kind")
            if bank:
                if not _ok_bank(access, bank):
                    raise ObjectUnavailable()
            elif kind not in {"Observation"}:
                raise ObjectUnavailable()
            elif not (_banks(access) or []):
                raise ObjectUnavailable()
        env["disclosure"] = _disclosure(access)
        env["permissions"] = {"read": True, "write": "memory.write" in (access.permissions or []) or access.is_node_admin}
        return env

    def list(
        self,
        access: AccessContext,
        *,
        kind: str = "",
        bank_id: str | None = None,
        cursor: str = "",
        limit: int = LIST_CAP,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or LIST_CAP), LIST_CAP))
        allowed = _banks(access)
        if bank_id:
            if allowed is not None and bank_id not in allowed and not access.is_node_admin:
                return {"ok": True, "items": [], "next_cursor": None, "disclosure": _disclosure(access, reason="bank_denied")}
            banks = [bank_id]
        elif allowed is None:
            banks = None
        else:
            banks = allowed
        kind_key = str(kind or "").strip()
        items: list[dict[str, Any]] = []

        def _need() -> int:
            return max(0, limit + 1 - len(items))

        if kind_key == "Observation":
            items.extend(self._scope_list(access, self._list_observations(cursor, _need())))
        elif kind_key == "Claim":
            items.extend(self._list_table("claims", from_claim_row, banks, cursor, _need(), "bank_id"))
        elif kind_key == "Evidence":
            items.extend(self._list_table("evidences", from_evidence_row, banks, cursor, _need(), "bank_id"))
        elif kind_key == "Knowledge":
            items.extend(
                self._scope_list(
                    access,
                    self._list_table("knowledge_nodes", from_knowledge_row, None, cursor, _need(), None),
                )
            )
        elif kind_key == "Memory":
            items.extend(self._list_memories(banks, cursor, _need()))
        else:
            sequence = (
                ("clm_", lambda c, n: self._list_table("claims", from_claim_row, banks, c, n, "bank_id")),
                ("evd_", lambda c, n: self._list_table("evidences", from_evidence_row, banks, c, n, "bank_id")),
                (
                    "kn_",
                    lambda c, n: self._scope_list(
                        access,
                        self._list_table("knowledge_nodes", from_knowledge_row, None, c, n, None),
                    ),
                ),
                ("mem_", lambda c, n: self._list_memories(banks, c, n)),
            )
            started = not cursor
            for prefix, fetch in sequence:
                if not started:
                    if cursor.startswith(prefix):
                        started = True
                    else:
                        continue
                need = _need()
                if need <= 0:
                    break
                use_cursor = cursor if (cursor and cursor.startswith(prefix)) else ""
                items.extend(fetch(use_cursor, need))
                cursor = ""
        truncated = len(items) > limit
        page = items[:limit]
        next_cursor = page[-1]["ref"]["authority_id"] if truncated and page else None
        return {
            "ok": True,
            "items": page,
            "count": len(page),
            "next_cursor": next_cursor,
            "disclosure": _disclosure(access, truncated=truncated, reason="page_cap" if truncated else None),
        }

    def relations(self, object_id: str, access: AccessContext) -> dict[str, Any]:
        self.get(object_id, access)
        rows = self._rows(
            "SELECT * FROM memory_relations WHERE (from_id=? OR to_id=?) AND (deleted_at IS NULL OR deleted_at='') ORDER BY id LIMIT 200",
            (object_id, object_id),
        )
        items = [relation_envelope(r) for r in rows]
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "disclosure": _disclosure(access, truncated=len(rows) >= 200, reason="relation_cap" if len(rows) >= 200 else None),
        }

    def neighborhood(self, object_id: str, access: AccessContext, *, hops: int = 1) -> dict[str, Any]:
        root = self.get(object_id, access)
        hops = 1 if hops not in (1, 2) else hops
        frontier = {object_id}
        seen = {object_id}
        edges: list[dict[str, Any]] = []
        nodes = {object_id: root}
        truncated = False
        for _ in range(hops):
            nxt: set[str] = set()
            if len(nodes) >= NEIGHBORHOOD_CAP:
                truncated = True
                break
            for oid in list(frontier):
                rels = self._rows(
                    "SELECT * FROM memory_relations WHERE (from_id=? OR to_id=?) AND (deleted_at IS NULL OR deleted_at='') ORDER BY id LIMIT ?",
                    (oid, oid, NEIGHBORHOOD_CAP),
                )
                for row in rels:
                    env = relation_envelope(row)
                    edges.append(env)
                    for end in (env["from"], env["to"]):
                        if not end or end in seen:
                            continue
                        seen.add(end)
                        nxt.add(end)
                        if len(nodes) >= NEIGHBORHOOD_CAP:
                            truncated = True
                            break
                        try:
                            nodes[end] = self.get(end, access)
                        except ObjectUnavailable:
                            nodes[end] = {
                                "ref": {"authority_id": end, "kind": kind_from_id(end)[0], "source_table": kind_from_id(end)[1]},
                                "title": end,
                                "disclosure": {"reason": "unavailable", "truncated": False},
                            }
                if truncated:
                    break
            frontier = nxt
        return {
            "ok": True,
            "root": object_id,
            "hops": hops,
            "nodes": list(nodes.values()),
            "edges": edges,
            "disclosure": _disclosure(access, truncated=truncated, reason="neighborhood_cap" if truncated else None),
        }

    def lineage(self, object_id: str, access: AccessContext) -> dict[str, Any]:
        env = self.get(object_id, access)
        kind = (env.get("ref") or {}).get("kind")
        items: list[dict[str, Any]] = []
        epistemic: list[dict[str, Any]] = []
        if kind == "Claim":
            rows = self._rows("SELECT * FROM claim_evidence WHERE claim_id=? ORDER BY created_at", (object_id,))
            for row in rows:
                ref = row.get("evidence_ref")
                try:
                    ev = self.get(str(ref), access) if ref else {"raw": dict(row)}
                except ObjectUnavailable:
                    ev = {"ref": {"authority_id": ref, "kind": "Unknown"}, "disclosure": {"reason": "unavailable"}}
                items.append(ev)
            rel_pack = self.relations(object_id, access)
            for rel in rel_pack.get("items") or []:
                ctype = str(rel.get("canonical_type") or rel.get("relation_type") or "")
                if ctype in {"supports", "contradicts", "qualifies", "supersedes", "derived_from"}:
                    epistemic.append(rel)
        elif kind == "Evidence":
            items.append(env)
        elif kind == "Memory":
            rows = self._rows("SELECT * FROM memory_evidence WHERE memory_id=? LIMIT 50", (object_id,))
            items = [{"observation_id": r.get("observation_id"), "relation": r.get("relation")} for r in rows]
        timeline = self.timeline(object_id, access).get("items") or []
        return {
            "ok": True,
            "object_id": object_id,
            "kind": kind,
            "title": env.get("title"),
            "items": items,
            "epistemic_relations": epistemic,
            "timeline": timeline,
            "impact_preview": "revalidation_not_auto_overwrite",
            "verified_is_not_absolute_truth": True,
            "disclosure": _disclosure(access),
        }

    def timeline(self, object_id: str, access: AccessContext) -> dict[str, Any]:
        self.get(object_id, access)
        events: list[dict[str, Any]] = []
        kind, _ = kind_from_id(object_id)
        if kind in {"Memory", "Observation", "Unknown"}:
            events.extend(
                {"at": r.get("created_at"), "version": r.get("version"), "kind": "memory_version"}
                for r in self._rows(
                    "SELECT version, created_at FROM memory_versions WHERE memory_id=? ORDER BY version",
                    (object_id,),
                )
            )
        if kind == "Claim":
            events.extend(
                {
                    "at": r.get("created_at"),
                    "from": r.get("from_status"),
                    "to": r.get("to_status"),
                    "kind": "claim_lifecycle",
                }
                for r in self._rows(
                    "SELECT * FROM claim_lifecycle_events WHERE claim_id=? ORDER BY created_at",
                    (object_id,),
                )
            )
        return {"ok": True, "object_id": object_id, "items": events, "disclosure": _disclosure(access)}

    def _load(self, object_id: str) -> dict[str, Any] | None:
        kind, table = kind_from_id(object_id)
        if table == "memories" or object_id.startswith("mem_"):
            row = self._row("SELECT * FROM memories WHERE id=?", (object_id,))
            return from_memory_row(row) if row else None
        if table == "observations" or object_id.startswith("obs_"):
            row = self._row("SELECT * FROM observations WHERE id=?", (object_id,))
            return from_observation_row(row) if row else None
        if table == "evidences":
            row = self._row("SELECT * FROM evidences WHERE id=?", (object_id,))
            return from_evidence_row(row) if row else None
        if table == "claims":
            row = self._row("SELECT * FROM claims WHERE id=?", (object_id,))
            if not row:
                return None
            refs = [r["evidence_ref"] for r in self._rows("SELECT evidence_ref FROM claim_evidence WHERE claim_id=?", (object_id,))]
            return from_claim_row(row, evidence_refs=refs)
        if table == "knowledge_nodes":
            row = self._row("SELECT * FROM knowledge_nodes WHERE id=?", (object_id,))
            return from_knowledge_row(row) if row else None
        row = self._row("SELECT * FROM memories WHERE id=?", (object_id,))
        return from_memory_row(row) if row else None

    def _scope_list(self, access: AccessContext, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if access.is_node_admin:
            return items
        out: list[dict[str, Any]] = []
        for env in items:
            bank = (env.get("ref") or {}).get("bank_id")
            if bank in (None, "", "unknown"):
                continue
            if _ok_bank(access, str(bank)):
                out.append(env)
        return out

    def _list_memories(self, banks: list[str] | None, cursor: str, limit: int) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories"
        params: list[Any] = []
        clauses: list[str] = []
        if banks:
            clauses.append(f"bank_id IN ({','.join('?' * len(banks))})")
            params.extend(banks)
        if cursor.startswith("mem_"):
            clauses.append("id > ?")
            params.append(cursor)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id LIMIT ?"
        params.append(max(1, limit))
        return [from_memory_row(r) for r in self._rows(sql, tuple(params))]

    def _list_observations(self, cursor: str, limit: int) -> list[dict[str, Any]]:
        sql = "SELECT * FROM observations"
        params: list[Any] = []
        if cursor.startswith("obs_"):
            sql += " WHERE id > ?"
            params.append(cursor)
        sql += " ORDER BY id LIMIT ?"
        params.append(max(1, limit))
        return [from_observation_row(r) for r in self._rows(sql, tuple(params))]

    def _list_table(self, table, mapper, banks, cursor, limit, bank_col):
        sql = f"SELECT * FROM {table}"
        params: list[Any] = []
        clauses: list[str] = []
        if banks and bank_col:
            clauses.append(f"{bank_col} IN ({','.join('?' * len(banks))})")
            params.extend(banks)
        if cursor:
            clauses.append("id > ?")
            params.append(cursor)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id LIMIT ?"
        params.append(max(1, limit))
        return [mapper(r) for r in self._rows(sql, tuple(params))]
