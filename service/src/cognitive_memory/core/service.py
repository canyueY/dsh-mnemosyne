# -*- coding: utf-8 -*-
"""MemoryService — 唯一业务核心（MCP / REST 共用）。"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

from cognitive_memory.domain.models import (
    ContextBuildIn,
    ContextBuildOut,
    FeedbackIn,
    Memory,
    MemoryProposeIn,
    MemoryStatus,
    MemoryType,
    MemoryUpdateIn,
    Observation,
    ObservationIn,
    SearchQuery,
    Task,
    TaskCreateIn,
)
from cognitive_memory.domain.emotion import emotion_retrieval_score, get_mnemosyne
from cognitive_memory.domain.spatiotemporal import (
    attach_spatiotemporal,
    spatiotemporal_score,
)
from cognitive_memory.events.bus import EventBus
from cognitive_memory.storage.db import Database
from cognitive_memory.storage.ids import new_id


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_QUERY_SPLIT_RE = re.compile(r"[\s,，。.!！?？;；:：/\\|]+")


def _query_terms(query: str, *, max_terms: int = 12) -> list[str]:
    """Chinese has no spaces; derive 2-char shingles for LIKE fallback."""
    terms: list[str] = []
    for token in _QUERY_SPLIT_RE.split(query or ""):
        token = token.strip()
        if not token:
            continue
        if _CJK_RE.search(token):
            if len(token) <= 2:
                terms.append(token)
            else:
                terms.extend(token[i:i + 2] for i in range(len(token) - 1))
        else:
            terms.append(token)
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            out.append(term)
        if len(out) >= max_terms:
            break
    return out


class ConflictError(Exception):
    def __init__(self, message: str, *, code: str = "conflict"):
        super().__init__(message)
        self.code = code


class NotFoundError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.code = "not_found"


class MemoryService:
    def __init__(
        self,
        db: Database,
        bus: EventBus | None = None,
        *,
        context_token_budget: int = 1200,
        spatiotemporal_enable: bool = True,
        emotion_enable: bool = False,
        emotion_write_enabled: bool = False,
        emotion_shadow_only: bool = True,
        emotion_retrieval_enabled: bool = False,
    ):
        self.db = db
        self.bus = bus or EventBus()
        self.context_token_budget = context_token_budget
        self.spatiotemporal_enable = bool(spatiotemporal_enable)
        # Mnemosyne：默认关；由 DSH_MEMORY_EMOTION / bootstrap 打开
        self.emotion_enable = bool(emotion_enable)
        self.emotion_write_enabled = bool(emotion_write_enabled)
        self.emotion_shadow_only = bool(emotion_shadow_only)
        self.emotion_retrieval_enabled = bool(emotion_retrieval_enabled)
        self.call_budget_config = {
            "max_external_calls": 8,
            "max_retrieval_rounds": 4,
            "max_duration_ms": 30000,
        }
        self.db.ensure_schema()

    # ---------- helpers ----------

    def _row_memory(self, row: sqlite3.Row) -> Memory:
        keys = set(row.keys())
        meta = _loads(row["metadata_json"], {})
        bank = "personal"
        if "bank_id" in keys and row["bank_id"]:
            bank = str(row["bank_id"])
        elif isinstance(meta, dict) and meta.get("bank_id"):
            bank = str(meta.get("bank_id"))
        return Memory(
            id=row["id"],
            type=MemoryType(row["type"]),
            status=MemoryStatus(row["status"]),
            content=row["content"],
            source_ids=_loads(row["source_ids_json"], []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_confirmed_at=row["last_confirmed_at"],
            version=int(row["version"]),
            confidence=row["confidence"],
            expires_at=row["expires_at"],
            metadata=meta if isinstance(meta, dict) else {},
            bank_id=bank,
        )

    def _idempotent_get(self, conn: sqlite3.Connection, key: str | None, scope: str) -> Any | None:
        if not key:
            return None
        row = conn.execute(
            "SELECT response_json FROM idempotency_keys WHERE key=? AND scope=?",
            (key, scope),
        ).fetchone()
        if not row:
            return None
        return _loads(row["response_json"], None)

    def _idempotent_put(
        self, conn: sqlite3.Connection, key: str | None, scope: str, response: Any
    ) -> None:
        if not key:
            return
        conn.execute(
            """
            INSERT OR REPLACE INTO idempotency_keys(key, scope, response_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, scope, json.dumps(response, ensure_ascii=False), _now()),
        )

    def _emit(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        payload: dict[str, Any],
        *,
        producer: str = "cognitive-memory",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        eid = new_id("evt")
        now = _now()
        envelope = {
            "event_id": eid,
            "event_type": event_type,
            "event_version": 1,
            "occurred_at": now,
            "recorded_at": now,
            "producer": producer,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "payload_version": 1,
            "payload": payload,
        }
        conn.execute(
            """
            INSERT INTO events(
              event_id, event_type, event_version, occurred_at, recorded_at,
              producer, correlation_id, causation_id, payload_version, payload_json
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                eid,
                event_type,
                now,
                now,
                producer,
                correlation_id,
                causation_id,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            INSERT INTO outbox(event_id, status, attempts, next_attempt_at, created_at, updated_at)
            VALUES (?, 'pending', 0, ?, ?, ?)
            """,
            (eid, now, now, now),
        )
        return eid

    def _snapshot_version(self, conn: sqlite3.Connection, mem: Memory) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_versions(memory_id, version, snapshot_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (mem.id, mem.version, mem.model_dump_json(), _now()),
        )

    def _fts_upsert(self, conn: sqlite3.Connection, mem: Memory) -> None:
        conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (mem.id,))
        if mem.status == MemoryStatus.INVALIDATED:
            return
        conn.execute(
            "INSERT INTO memories_fts(memory_id, content, type, status) VALUES (?, ?, ?, ?)",
            (mem.id, mem.content, mem.type.value, mem.status.value),
        )

    # ---------- observations ----------

    def capture_observation(self, body: ObservationIn) -> Observation:
        from cognitive_memory.evidence.source_contract import (
            attach_source_contract,
            normalize_coverage,
            normalize_source_ref,
        )
        from cognitive_memory.governance.workspace import assert_workspace_authorized
        from cognitive_memory.ingestion.normalize import normalize_observation

        body = normalize_observation(body)
        assert_workspace_authorized(
            self.db, dict(body.metadata or {}), action="capture"
        )
        with self.db.transaction() as conn:
            cached = self._idempotent_get(conn, body.idempotency_key, "observation")
            if cached:
                return Observation.model_validate(cached)
            oid = new_id("obs")
            now = _now()
            occurred = body.occurred_at or now
            meta = dict(body.metadata or {})
            # E01：若调用方声明了来源/覆盖，规范化写入元数据（不伪造未知页码）。
            if meta.get("source_ref") or meta.get("coverage") or meta.get("revision"):
                existing_src = meta.get("source_ref") if isinstance(meta.get("source_ref"), dict) else {}
                sref = normalize_source_ref(
                    source_id=str(existing_src.get("source_id") or oid),
                    root_source_id=str(existing_src.get("root_source_id") or meta.get("root_source_id") or ""),
                    revision=str(existing_src.get("revision") or meta.get("revision") or "0"),
                    content=body.content,
                    source_type=body.source_kind.value,
                    source_role=existing_src.get("source_role") or meta.get("source_role"),
                    bank_id=str(meta.get("bank_id") or "personal"),
                    producer=body.producer,
                    locator=existing_src.get("locator") if isinstance(existing_src.get("locator"), dict) else None,
                    allow_self_attest=True,
                )
                cov = normalize_coverage(meta.get("coverage")) if meta.get("coverage") is not None else None
                meta = attach_source_contract(meta, source_ref=sref, coverage=cov)
            if self.spatiotemporal_enable:
                meta = attach_spatiotemporal(
                    meta,
                    occurred_at=occurred,
                    recorded_at=now,
                    source_kind=body.source_kind.value,
                )
            conn.execute(
                """
                INSERT INTO observations(
                  id, source_kind, content, occurred_at, created_at, producer,
                  correlation_id, data_ref, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    oid,
                    body.source_kind.value,
                    body.content,
                    occurred,
                    now,
                    body.producer,
                    body.correlation_id,
                    body.data_ref,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            dump = body.model_dump(exclude={"idempotency_key"})
            dump["metadata"] = meta
            dump["occurred_at"] = occurred
            obs = Observation(id=oid, created_at=now, **dump)
            from cognitive_memory.memory.shallow import create_shallow
            create_shallow(self, conn, obs, now)
            self._emit(
                conn,
                "observation.captured",
                {"observation_id": oid, "source_kind": body.source_kind.value},
                producer=body.producer,
                correlation_id=body.correlation_id,
            )
            # 快速通道：对话类观察同步写一条 working 记忆
            if body.source_kind.value in ("dialog", "user_statement"):
                wid = new_id("mem")
                wm_meta: dict[str, Any] = {"from_observation": oid}
                if self.spatiotemporal_enable and isinstance(meta.get("spatiotemporal"), dict):
                    wm_meta["spatiotemporal"] = meta["spatiotemporal"]
                wm_meta.setdefault("bank_id", "personal")
                wm = Memory(
                    id=wid,
                    type=MemoryType.WORKING,
                    status=MemoryStatus.ACTIVE,
                    content=body.content[:2000],
                    source_ids=[oid],
                    created_at=now,
                    updated_at=now,
                    last_confirmed_at=now,
                    version=1,
                    metadata=wm_meta,
                    bank_id="personal",
                )
                self._insert_memory(conn, wm)
                self._emit(
                    conn,
                    "memory.activated",
                    {"memory_id": wid, "type": "working"},
                    producer=body.producer,
                    correlation_id=body.correlation_id,
                )
            # 慢速：请求归纳 + 变化检测（默认 dry-run，见 dispatcher）
            self._create_task_conn(
                conn,
                TaskCreateIn(
                    task_type="memory.consolidation",
                    payload={"observation_id": oid},
                ),
            )
            if self.spatiotemporal_enable:
                self._create_task_conn(
                    conn,
                    TaskCreateIn(
                        task_type="memory.change_detect",
                        payload={"observation_id": oid, "dry_run": True, "apply_l2": False},
                        idempotency_key=f"chgdet:{oid}",
                    ),
                )
            if self.emotion_enable:
                self._create_task_conn(
                    conn,
                    TaskCreateIn(
                        task_type="memory.emotion_extract",
                        payload={
                            "observation_id": oid,
                            "shadow_only": self.emotion_shadow_only,
                            "write_enabled": self.emotion_write_enabled,
                        },
                        idempotency_key=f"emoext:{oid}",
                    ),
                )
            payload = obs.model_dump()
            self._idempotent_put(conn, body.idempotency_key, "observation", payload)
            return obs

    # ---------- memories ----------

    def _insert_memory(self, conn: sqlite3.Connection, mem: Memory) -> None:
        from cognitive_memory.memory.banks import normalize_bank_id

        bank_id = normalize_bank_id(getattr(mem, "bank_id", None) or "personal")
        meta = dict(mem.metadata or {})
        meta.setdefault("bank_id", bank_id)
        conn.execute(
            """
            INSERT INTO memories(
              id, type, status, content, source_ids_json, created_at, updated_at,
              last_confirmed_at, version, confidence, expires_at, metadata_json, bank_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mem.id,
                mem.type.value,
                mem.status.value,
                mem.content,
                json.dumps(mem.source_ids, ensure_ascii=False),
                mem.created_at,
                mem.updated_at,
                mem.last_confirmed_at,
                mem.version,
                mem.confidence,
                mem.expires_at,
                json.dumps(meta, ensure_ascii=False),
                bank_id,
            ),
        )
        mem.metadata = meta
        mem.bank_id = bank_id
        self._snapshot_version(conn, mem)
        self._fts_upsert(conn, mem)
        for sid in mem.source_ids:
            conn.execute(
                """
                INSERT OR IGNORE INTO memory_evidence(
                  memory_id, observation_id, relation, note, created_at,
                  confidence, evidence_kind, validation_state, producer, metadata_json
                ) VALUES (?, ?, 'supports', '', ?, NULL, 'observation', 'supports', '', '{}')
                """,
                (mem.id, sid, _now()),
            )

    def propose_memory(
        self,
        body: MemoryProposeIn,
        *,
        write_guard: Callable[[], None] | None = None,
    ) -> Memory:
        from cognitive_memory.ingestion.pipeline import coerce_long_term_status
        from cognitive_memory.governance.policy import assert_allow_store
        from cognitive_memory.governance.workspace import assert_workspace_authorized

        meta = dict(body.metadata or {})
        assert_allow_store(meta)
        assert_workspace_authorized(self.db, meta, action="ingest")

        with self.db.transaction() as conn:
            # The guard must run inside the same write transaction, after any
            # caller-side interleaving point and immediately before the insert.
            # A stale worker therefore cannot leave a real Memory behind while
            # only its task-completion record is rejected.
            if write_guard is not None:
                write_guard()
            if body.type != MemoryType.WORKING and body.source_ids:
                from cognitive_memory.memory.shallow import is_external_perception
                for oid in body.source_ids:
                    source = conn.execute("SELECT source_kind, metadata_json FROM observations WHERE id=?", (oid,)).fetchone()
                    if source and is_external_perception(source[0], _loads(source[1], {})):
                        raise ConflictError("external perception requires shallow selection", code="shallow_selection_required")
            cached = self._idempotent_get(conn, body.idempotency_key, "memory_propose")
            if cached:
                return Memory.model_validate(cached)
            now = _now()
            mid = new_id("mem")
            status_raw, meta = coerce_long_term_status(
                memory_type=body.type.value,
                status=body.status.value,
                source_ids=list(body.source_ids),
                metadata=meta,
            )
            status = MemoryStatus(status_raw)
            from cognitive_memory.memory.banks import normalize_bank_id

            bank_id = normalize_bank_id(
                getattr(body, "bank_id", None) or meta.get("bank_id") or "personal"
            )
            meta["bank_id"] = bank_id
            if self.spatiotemporal_enable and (
                meta.get("temporal")
                or meta.get("spatial")
                or meta.get("spatiotemporal")
            ):
                meta = attach_spatiotemporal(meta, recorded_at=now)
            mem = Memory(
                id=mid,
                type=body.type,
                status=status,
                content=body.content,
                source_ids=list(body.source_ids),
                created_at=now,
                updated_at=now,
                last_confirmed_at=now if status == MemoryStatus.ACTIVE else None,
                version=1,
                confidence=body.confidence,
                expires_at=body.expires_at,
                metadata=meta,
                bank_id=bank_id,
            )
            self._insert_memory(conn, mem)
            self._emit(
                conn,
                "memory.candidate_created"
                if status == MemoryStatus.CANDIDATE
                else "memory.activated",
                {"memory_id": mid, "type": body.type.value, "status": status.value},
            )
            if status == MemoryStatus.CANDIDATE:
                self._create_task_conn(
                    conn,
                    TaskCreateIn(
                        task_type="memory.promote_check",
                        payload={"memory_id": mid},
                    ),
                )
            payload = mem.model_dump()
            self._idempotent_put(conn, body.idempotency_key, "memory_propose", payload)
            return mem

    def get_memory(self, memory_id: str, *, include_evidence: bool = False) -> dict[str, Any]:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise NotFoundError(f"memory not found: {memory_id}")
        mem = self._row_memory(row)
        out = mem.model_dump()
        if include_evidence:
            from cognitive_memory.evidence import list_evidence_for_memory

            out["evidence"] = [
                e.model_dump() for e in list_evidence_for_memory(self.db, memory_id)
            ]
            vers = conn.execute(
                "SELECT version, created_at FROM memory_versions WHERE memory_id=? ORDER BY version",
                (memory_id,),
            ).fetchall()
            out["versions"] = [dict(r) for r in vers]
            out["relations"] = self.list_relations(memory_id, limit=40)
        return out

    def attach_evidence(self, body: Any) -> dict[str, Any]:
        from cognitive_memory.evidence import EvidenceAttachIn, attach_evidence

        if not isinstance(body, EvidenceAttachIn):
            body = EvidenceAttachIn.model_validate(body)
        try:
            rec = attach_evidence(self.db, body)
        except KeyError as exc:
            raise NotFoundError(str(exc)) from exc
        return rec.model_dump()

    def list_evidence(self, memory_id: str) -> list[dict[str, Any]]:
        from cognitive_memory.evidence import list_evidence_for_memory

        return [e.model_dump() for e in list_evidence_for_memory(self.db, memory_id)]

    def list_banks(self) -> list[dict[str, Any]]:
        from cognitive_memory.memory.banks import list_banks

        return list_banks(self.db)

    def get_authority(self) -> dict[str, Any]:
        from cognitive_memory.governance.authority import get_authority

        return get_authority(self.db)

    def transition_authority(
        self,
        state: str,
        *,
        reason: str = "",
        allow_dual: bool = False,
        actor: str = "api",
    ) -> dict[str, Any]:
        from cognitive_memory.governance.authority import (
            AuthorityTransitionError,
            transition_authority,
        )

        try:
            return transition_authority(
                self.db,
                state,
                reason=reason,
                allow_dual=allow_dual,
                actor=actor,
            )
        except AuthorityTransitionError as exc:
            raise ConflictError(str(exc), code=getattr(exc, "code", "conflict")) from exc
        except ValueError as exc:
            raise ConflictError(str(exc), code="invalid_authority_state") from exc

    def update_memory(self, memory_id: str, body: MemoryUpdateIn) -> Memory:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise NotFoundError(f"memory not found: {memory_id}")
            mem = self._row_memory(row)
            if body.expected_version is not None and body.expected_version != mem.version:
                raise ConflictError(
                    f"version mismatch: expected {body.expected_version}, got {mem.version}",
                    code="version_conflict",
                )
            if body.content is not None:
                mem.content = body.content
            if body.status is not None:
                mem.status = body.status
            if body.metadata is not None:
                mem.metadata = body.metadata
            if body.confidence is not None:
                mem.confidence = body.confidence
            if "expires_at" in body.model_fields_set:
                mem.expires_at = body.expires_at
            mem.version += 1
            mem.updated_at = _now()
            if mem.status == MemoryStatus.ACTIVE:
                mem.last_confirmed_at = mem.updated_at
            conn.execute(
                """
                UPDATE memories SET
                  content=?, status=?, metadata_json=?, confidence=?, expires_at=?,
                  version=?, updated_at=?, last_confirmed_at=?
                WHERE id=?
                """,
                (
                    mem.content,
                    mem.status.value,
                    json.dumps(mem.metadata, ensure_ascii=False),
                    mem.confidence,
                    mem.expires_at,
                    mem.version,
                    mem.updated_at,
                    mem.last_confirmed_at,
                    mem.id,
                ),
            )
            self._snapshot_version(conn, mem)
            self._fts_upsert(conn, mem)
            et = (
                "memory.invalidated"
                if mem.status == MemoryStatus.INVALIDATED
                else "memory.updated"
            )
            self._emit(conn, et, {"memory_id": mem.id, "version": mem.version})
            return mem

    def forget_memory(self, memory_id: str, *, hard: bool = False) -> dict[str, Any]:
        """软删除默认：invalidated；hard=True 时清理主表+FTS+证据（版本保留审计）。

        P4 R17：级联 Claim/Evidence tombstone + TMS invalidate（Claim 行保留）。
        E02：登记 source_revision_tombstone，阻止重复导入复活。
        """
        from cognitive_memory.evidence.derivation import DerivationStore
        from cognitive_memory.evidence.source_contract import content_hash_of

        content_hash = ""
        root_source_id = f"mem:{memory_id}"
        bank_id = "personal"
        try:
            existing = self.get_memory(memory_id)
            if isinstance(existing, dict):
                content_hash = content_hash_of(str(existing.get("content") or ""))
                bank_id = str(existing.get("bank_id") or "personal")
                meta = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
                src = meta.get("source_ref") if isinstance(meta.get("source_ref"), dict) else {}
                if src.get("root_source_id"):
                    root_source_id = str(src["root_source_id"])
                elif existing.get("source_ids"):
                    root_source_id = str(existing["source_ids"][0])
            elif existing is not None:
                content_hash = content_hash_of(str(getattr(existing, "content", "") or ""))
                bank_id = str(getattr(existing, "bank_id", None) or "personal")
        except Exception:
            pass

        if not hard:
            out = self.update_memory(
                memory_id,
                MemoryUpdateIn(status=MemoryStatus.INVALIDATED),
            ).model_dump()
            try:
                from cognitive_memory.tms.tombstone import cascade_memory_invalidated

                out["cascade"] = cascade_memory_invalidated(
                    self.db, memory_id, hard=False, actor="forget_memory"
                )
            except Exception as exc:  # noqa: BLE001 — cascade best-effort
                out["cascade_error"] = str(exc)
            try:
                drv = DerivationStore(self.db)
                out["source_tombstone"] = drv.record_tombstone(
                    root_source_id=root_source_id,
                    content_hash=content_hash,
                    bank_id=bank_id,
                    reason="authorized_forget",
                    actor="forget_memory",
                )
                # 同时按 memory 根留下空 hash 标记，覆盖无 hash 的重复导入
                drv.record_tombstone(
                    root_source_id=root_source_id,
                    content_hash="",
                    bank_id=bank_id,
                    reason="authorized_forget",
                    actor="forget_memory",
                )
            except Exception as exc:  # noqa: BLE001
                out["source_tombstone_error"] = str(exc)
            return out
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise NotFoundError(f"memory not found: {memory_id}")
            conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM memory_evidence WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM memory_feedback WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            self._emit(conn, "memory.invalidated", {"memory_id": memory_id, "hard": True})
        pack: dict[str, Any] = {"id": memory_id, "deleted": True, "hard": True}
        try:
            from cognitive_memory.tms.tombstone import cascade_memory_invalidated

            pack["cascade"] = cascade_memory_invalidated(
                self.db, memory_id, hard=True, actor="forget_memory"
            )
        except Exception as exc:  # noqa: BLE001
            pack["cascade_error"] = str(exc)
        try:
            drv = DerivationStore(self.db)
            pack["source_tombstone"] = drv.record_tombstone(
                root_source_id=root_source_id,
                content_hash=content_hash,
                bank_id=bank_id,
                reason="authorized_hard_delete",
                actor="forget_memory",
            )
            drv.record_tombstone(
                root_source_id=root_source_id,
                content_hash="",
                bank_id=bank_id,
                reason="authorized_hard_delete",
                actor="forget_memory",
            )
        except Exception as exc:  # noqa: BLE001
            pack["source_tombstone_error"] = str(exc)
        return pack

    def search(self, q: SearchQuery) -> list[Memory]:
        from cognitive_memory.memory.banks import resolve_search_banks

        conn = self.db.connect()
        limit = max(1, min(50, int(q.limit or 10)))
        statuses = [s.value for s in (q.status or [MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE])]
        types = [t.value for t in (q.types or list(MemoryType))]
        banks = resolve_search_banks(getattr(q, "banks", None))
        query = (q.query or "").strip()
        bank_clause = f" AND bank_id IN ({','.join('?' * len(banks))})"
        m_bank_clause = f" AND m.bank_id IN ({','.join('?' * len(banks))})"
        # Keep filtering inside every recall branch, before LIMIT: a different
        # scope or expired record must not displace a valid result.
        banks = list(banks)
        for key in ("user_id", "agent_id", "run_id"):
            value = getattr(q, key, None)
            if value is not None:
                expr = f"json_extract(CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{{}}' END, '$.{key}') = ?"
                bank_clause += f" AND {expr}"
                m_bank_clause += " AND " + expr.replace("metadata_json", "m.metadata_json")
                banks.append(value)
        if q.mode == "current":
            # SQLite parses ISO offsets consistently; malformed expiry fails
            # closed in current mode but remains inspectable in history mode.
            expiry = "(expires_at IS NULL OR julianday(expires_at) > julianday('now'))"
            bank_clause += f" AND {expiry}"
            m_bank_clause += " AND " + expiry.replace("expires_at", "m.expires_at")
        rows: list[sqlite3.Row]
        if query:
            # FTS5：简单转义双引号
            safe = query.replace('"', '""')
            # FTS5：多词用 AND；失败则降级 LIKE
            fts_q = " AND ".join(
                f'"{t.replace(chr(34), "")}"' for t in safe.split() if t.strip()
            ) or f'"{safe}"'
            sql = """
              SELECT m.* FROM memories_fts f
              JOIN memories m ON m.id = f.memory_id
              WHERE memories_fts MATCH ?
                AND m.status IN ({sts})
                AND m.type IN ({tys})
                {banks}
              ORDER BY bm25(memories_fts), m.updated_at DESC, m.id
              LIMIT ?
            """.format(
                sts=",".join("?" * len(statuses)),
                tys=",".join("?" * len(types)),
                banks=m_bank_clause,
            )
            try:
                rows = conn.execute(
                    sql, (fts_q, *statuses, *types, *banks, limit)
                ).fetchall()
            except (sqlite3.OperationalError, SystemError):
                # Some Python/SQLite builds raise SystemError for a NULL FTS
                # result instead of an sqlite3 error; degrade to LIKE either way.
                rows = []
            if not rows:
                # 降级：LIKE
                like = f"%{query}%"
                rows = conn.execute(
                    f"""
                    SELECT * FROM memories
                    WHERE content LIKE ?
                      AND status IN ({",".join("?" * len(statuses))})
                      AND type IN ({",".join("?" * len(types))})
                      {bank_clause}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (like, *statuses, *types, *banks, limit),
                ).fetchall()
                if not rows:
                    # 中文没有空格：整句 FTS/LIKE 都命中不了时，用 2 字滑窗再试一次
                    terms = _query_terms(query)
                    if terms:
                        likes = " OR ".join(["content LIKE ?"] * len(terms))
                        rows = conn.execute(
                            f"""
                            SELECT * FROM memories
                            WHERE ({likes})
                              AND status IN ({",".join("?" * len(statuses))})
                              AND type IN ({",".join("?" * len(types))})
                              {bank_clause}
                            ORDER BY updated_at DESC
                            LIMIT ?
                            """,
                            (*[f"%{t}%" for t in terms], *statuses, *types, *banks, limit),
                        ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT * FROM memories
                WHERE status IN ({",".join("?" * len(statuses))})
                  AND type IN ({",".join("?" * len(types))})
                  {bank_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*statuses, *types, *banks, limit),
            ).fetchall()
        memories = [self._row_memory(r) for r in rows]
        memories = self._rerank_spatiotemporal(
            memories,
            mode=q.mode,
            enable=bool(q.spatiotemporal_rerank),
            limit=None,
        )
        memories = self._rerank_emotion(
            memories,
            query=q.query or "",
            enable=bool(q.emotion_rerank),
            limit=None,
        )
        from cognitive_memory.domains.retrieval import apply_domain_gate, boost_music_domain

        memories = apply_domain_gate(
            memories,
            domain=getattr(q, "domain", None),
            query=q.query or "",
        )
        memories = boost_music_domain(
            memories,
            query=q.query or "",
            domain=getattr(q, "domain", None),
        )
        return memories[:limit]

    def _rerank_spatiotemporal(
        self,
        memories: list[Memory],
        *,
        mode: str = "current",
        enable: bool = True,
        limit: int | None = None,
    ) -> list[Memory]:
        if not memories:
            return []
        if not (self.spatiotemporal_enable and enable):
            return memories[:limit] if limit else memories
        scored: list[tuple[float, int, Memory]] = []
        for i, m in enumerate(memories):
            st_score = spatiotemporal_score(
                m.metadata,
                expires_at=m.expires_at,
                mode=mode,
            )
            # 保留原序作为次级键，避免完全打乱语义命中
            scored.append((st_score, -i, m))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out = [m for _, _, m in scored]
        return out[:limit] if limit else out

    def _query_emotion_pack(self, query: str) -> dict[str, Any]:
        """从查询文本抽情绪信号（规则）；失败则空。"""
        text = (query or "").strip()
        if not text:
            return {"emotion_state": None, "behaviors": []}
        try:
            from cognitive_memory.cognition.emotion_extract import extract_emotion_from_text

            pack = extract_emotion_from_text(text)
            if not pack.get("has_signal"):
                return {"emotion_state": None, "behaviors": []}
            return {
                "emotion_state": pack.get("emotion_state"),
                "behaviors": list(pack.get("behaviors") or []),
            }
        except Exception:
            return {"emotion_state": None, "behaviors": []}

    def _rerank_emotion(
        self,
        memories: list[Memory],
        *,
        query: str = "",
        enable: bool = False,
        limit: int | None = None,
        query_pack: dict[str, Any] | None = None,
    ) -> list[Memory]:
        if not memories:
            return []
        # 须服务 retrieval_enabled 且请求 emotion_rerank 同时打开
        if not (self.emotion_retrieval_enabled and enable):
            return memories[:limit] if limit else memories
        pack = query_pack if query_pack is not None else self._query_emotion_pack(query)
        scored: list[tuple[float, int, Memory]] = []
        for i, m in enumerate(memories):
            e_score = emotion_retrieval_score(
                m.metadata,
                query_emotion=pack.get("emotion_state"),
                query_behaviors=list(pack.get("behaviors") or []),
                enable=True,
            )
            scored.append((e_score, -i, m))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out = [m for _, _, m in scored]
        return out[:limit] if limit else out

    def build_context(self, body: ContextBuildIn) -> ContextBuildOut:
        from cognitive_memory.retrieval.context_budget import (
            build_context_envelope,
            estimate_tokens_chars,
            normalize_budget_config,
        )

        budget_cfg = normalize_budget_config(
            getattr(self, "context_budget_config", None)
            or {"default_token_budget": self.context_token_budget}
        )
        budget = body.token_budget or int(
            budget_cfg.get("default_token_budget") or self.context_token_budget
        )
        # 先多取一些再时空/情绪重排截断，避免过期项占满限额
        fetch_n = max(body.limit, min(50, body.limit * 2))
        emo_rerank = bool(body.emotion_rerank) and bool(self.emotion_retrieval_enabled)
        q_emo = self._query_emotion_pack(body.query) if emo_rerank else None
        hits = self.search(
            SearchQuery(
                query=body.query,
                types=body.types,
                limit=fetch_n,
                mode=body.mode,
                spatiotemporal_rerank=body.spatiotemporal_rerank,
                emotion_rerank=emo_rerank,
                domain=getattr(body, "domain", None),
            )
        )

        envelope_in = body.envelope if isinstance(getattr(body, "envelope", None), dict) else {}
        sections_in = envelope_in.get("sections") if isinstance(envelope_in.get("sections"), dict) else {}
        stable_prefix = sections_in.get("stable_prefix") or "【认知记忆·仅供参考】"
        tools = sections_in.get("tools") or ""
        history = sections_in.get("history") or ""
        header_tags = sections_in.get("header_tags") or "source|negation|expiry"
        output_reserve = sections_in.get("output_reserve") or ""

        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def _cand(m: Memory, *, via: str | None = None) -> dict[str, Any] | None:
            if m.id in seen_ids:
                return None
            seen_ids.add(m.id)
            st = (m.metadata or {}).get("spatiotemporal") if isinstance(m.metadata, dict) else None
            mnemo = get_mnemosyne(m.metadata)
            epistemic = (
                "hypothesis"
                if (m.metadata or {}).get("epistemic")
                in {"inference", "inferred_signal", "summary", "generated"}
                else "unverified_record"
            )
            tags = [m.type.value, epistemic]
            if (m.metadata or {}).get("negated") or (m.metadata or {}).get("negation"):
                tags.append("negated")
            if m.expires_at:
                tags.append("expiry")
            if m.source_ids:
                tags.append("sourced")
            frag = {
                "id": m.id,
                "type": m.type.value,
                "status": m.status.value,
                "content": m.content,
                "version": m.version,
                "source_ids": list(m.source_ids or []),
                "expires_at": m.expires_at,
                "epistemic_status": epistemic,
                "confidence_kind": "uncalibrated",
                "tags": tags,
                "spatiotemporal_score": spatiotemporal_score(
                    m.metadata, expires_at=m.expires_at, mode=body.mode
                )
                if self.spatiotemporal_enable
                else None,
                "emotion_score": emotion_retrieval_score(
                    m.metadata,
                    query_emotion=(q_emo or {}).get("emotion_state"),
                    query_behaviors=list((q_emo or {}).get("behaviors") or []),
                    enable=True,
                )
                if emo_rerank
                else None,
            }
            if via:
                frag["via_relation"] = via
            if isinstance(st, dict):
                frag["spatiotemporal"] = st
            if mnemo:
                frag["mnemosyne"] = {
                    "dominant": (mnemo.get("assessment") or {}).get("dominant_emotion"),
                    "association_level": (mnemo.get("assessment") or {}).get(
                        "association_level"
                    ),
                }
            return frag

        for m in hits:
            item = _cand(m)
            if item:
                candidates.append(item)
        if self.spatiotemporal_enable and candidates:
            for rm in self.query_related_for_context(list(seen_ids), limit=4):
                item = _cand(rm, via="relation")
                if item:
                    candidates.append(item)

        # 限额：只把前 limit 个候选交给信封裁剪
        candidates = candidates[: max(1, int(body.limit))]

        # measurement/hard_limit_verified 只认服务端真实计量器。
        # 顶层和 envelope 内的客户端布尔声明都不可信；当前服务端固定使用
        # estimate_tokens_chars，所以不存在 accurate tokenizer。
        accurate_tokenizer = False
        measurement_override = None
        envelope = build_context_envelope(
            token_budget=budget,
            sections={
                "stable_prefix": stable_prefix,
                "tools": tools,
                "history": history,
                "header_tags": header_tags,
                "output_reserve": output_reserve,
            },
            memory_candidates=candidates,
            estimate_fn=estimate_tokens_chars,
            measurement=measurement_override,
            has_accurate_tokenizer=accurate_tokenizer,
            config={**budget_cfg, **(envelope_in.get("budget_config") or {})},
            preserve_tags=True,
        )

        fragments = list(envelope.get("memory_fragments") or [])
        lines: list[str] = [str(stable_prefix)]
        for frag in fragments:
            tag = f"{frag.get('type')}|{frag.get('epistemic_status')}"
            if frag.get("via_relation"):
                tag = f"{tag}|rel"
            if "negated" in (frag.get("tags") or []):
                tag = f"{tag}|neg"
            if frag.get("expires_at"):
                tag = f"{tag}|exp"
            lines.append(f"- [{tag}] {frag.get('content')}")

        fits = envelope.get("fits")
        status = str(envelope.get("status") or ("ok" if fits else "incompatible"))
        brief = (
            "【认知记忆】上下文固定开销已超预算，无法安全装入记忆"
            if fits is False
            else ("\n".join(lines) if fragments else "【认知记忆】暂无相关条目")
        )
        used = int(envelope.get("tokens_used") or 0)
        # 不适配时按请求预算封顶报告，避免固定开销估算值“突破”调用方限额语义
        token_estimate = min(used, int(budget)) if fits is False else used
        return ContextBuildOut(
            fragments=fragments if fits is not False else [],
            token_estimate=token_estimate,
            brief=brief,
            fits=fits,
            measurement=str(envelope.get("measurement") or "estimated"),
            hard_limit_verified=bool(envelope.get("hard_limit_verified")),
            budget_envelope=envelope,
            reason_codes=list(envelope.get("reason_codes") or []),
            status=status,
        )

    def feedback(self, body: FeedbackIn) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM memories WHERE id=?", (body.memory_id,)).fetchone()
            if not row:
                raise NotFoundError(f"memory not found: {body.memory_id}")
            now = _now()
            conn.execute(
                """
                INSERT INTO memory_feedback(memory_id, useful, incorrect, note, producer, created_at, feedback_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    body.memory_id,
                    None if body.useful is None else int(bool(body.useful)),
                    None if body.incorrect is None else int(bool(body.incorrect)),
                    body.note,
                    body.producer,
                    now,
                    body.feedback_kind,
                ),
            )
            self._emit(
                conn,
                "memory.feedback_received",
                {
                    "memory_id": body.memory_id,
                    "useful": body.useful,
                    "incorrect": body.incorrect,
                    "feedback_kind": body.feedback_kind,
                },
                producer=body.producer,
            )
            if body.incorrect or body.feedback_kind in {"fact_correction", "execution_failure", "constraint_violation"}:
                self._create_task_conn(
                    conn,
                    TaskCreateIn(
                        task_type="memory.review_feedback",
                        payload={"memory_id": body.memory_id, "note": body.note,
                                 "feedback_kind": body.feedback_kind, "attribution": "unresolved"},
                    ),
                )
            useful_count = conn.execute(
                "SELECT COUNT(*) AS c FROM memory_feedback WHERE memory_id=? AND useful=1",
                (body.memory_id,),
            ).fetchone()["c"]
        return {
            "ok": True,
            "memory_id": body.memory_id,
            "useful_feedback_count": int(useful_count),
            "promoted": False,
            "feedback_kind": body.feedback_kind,
            "attribution": "unresolved",
            "fact_confidence_changed": False,
        }

    # ---------- tasks ----------

    def _create_task_conn(self, conn: sqlite3.Connection, body: TaskCreateIn) -> Task:
        cached = self._idempotent_get(conn, body.idempotency_key, "task")
        if cached:
            return Task.model_validate(cached)
        tid = new_id("task")
        now = _now()
        conn.execute(
            """
            INSERT INTO tasks(id, task_type, status, payload_json, attempts, created_at, updated_at, next_attempt_at)
            VALUES (?, ?, 'pending', ?, 0, ?, ?, ?)
            """,
            (tid, body.task_type, json.dumps(body.payload, ensure_ascii=False), now, now, now),
        )
        task = Task(
            id=tid,
            task_type=body.task_type,
            status="pending",
            payload=body.payload,
            created_at=now,
            updated_at=now,
        )
        self._idempotent_put(conn, body.idempotency_key, "task", task.model_dump())
        return task

    def create_task(self, body: TaskCreateIn) -> Task:
        with self.db.transaction() as conn:
            return self._create_task_conn(conn, body)

    def get_task(self, task_id: str) -> Task:
        row = self.db.connect().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise NotFoundError(f"task not found: {task_id}")
        return Task(
            id=row["id"],
            task_type=row["task_type"],
            status=row["status"],
            payload=_loads(row["payload_json"], {}),
            attempts=int(row["attempts"]),
            last_error=row["last_error"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ---------- P3 relations / P2 candidates ----------

    _RELATION_TYPES = frozenset(
        {
            "supports",
            "contradicts",
            "derives",
            "supersedes",
            "before",
            "after",
            "continues",
            "occurred_in",
            "located_at",
            "depends_on",
            "observed_during",
            # Music Domain instance edges
            "contains",
            "performed_by",
            "composed_by",
            "belongs_to",
            "tagged_as",
            "listened_to",
            "saves",
            "follows",
            "likes",
            "favorites",
            "imported_from",
        }
    )

    def link_memories(
        self,
        *,
        from_id: str,
        to_id: str,
        relation: str,
        note: str = "",
        valid_from: str | None = None,
        valid_until: str | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """写入 memory_relations（同一库）。dry_run 只校验不落盘。"""
        rel = (relation or "").strip().lower()
        if rel not in self._RELATION_TYPES:
            raise ConflictError(f"unsupported relation: {relation}", code="bad_relation")
        if not from_id or not to_id or from_id == to_id:
            raise ConflictError("invalid from_id/to_id", code="bad_ids")
        payload = {
            "from_id": from_id,
            "to_id": to_id,
            "relation": rel,
            "note": note or "",
            "valid_from": valid_from,
            "valid_until": valid_until,
            "confidence": confidence,
            "metadata": metadata or {},
        }
        if dry_run:
            return {"ok": True, "dry_run": True, **payload}
        with self.db.transaction() as conn:
            # 两端至少一端是已知记忆（允许观察 id 作 from 时跳过严格检查）
            now = _now()
            conn.execute(
                """
                INSERT INTO memory_relations(
                  from_id, to_id, relation, note, created_at,
                  valid_from, valid_until, confidence, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    from_id,
                    to_id,
                    rel,
                    note or "",
                    now,
                    valid_from,
                    valid_until,
                    confidence,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            rid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
            self._emit(
                conn,
                "memory.relation_created",
                {"id": rid, "from_id": from_id, "to_id": to_id, "relation": rel},
            )
            return {"ok": True, "id": rid, "dry_run": False, **payload}

    def list_relations(self, memory_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT id, from_id, to_id, relation, note, created_at,
                   valid_from, valid_until, confidence, metadata_json
            FROM memory_relations
            WHERE from_id=? OR to_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (memory_id, memory_id, max(1, min(200, int(limit)))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = {
                "id": r["id"],
                "from_id": r["from_id"],
                "to_id": r["to_id"],
                "relation": r["relation"],
                "note": r["note"],
                "created_at": r["created_at"],
                "valid_from": r["valid_from"] if "valid_from" in r.keys() else None,
                "valid_until": r["valid_until"] if "valid_until" in r.keys() else None,
                "confidence": r["confidence"] if "confidence" in r.keys() else None,
            }
            try:
                item["metadata"] = json.loads(r["metadata_json"] or "{}")
            except Exception:
                item["metadata"] = {}
            out.append(item)
        return out

    def store_change_candidates(
        self,
        *,
        observation_id: str,
        candidates: list[dict[str, Any]],
        dry_run: bool = True,
        applied: list[dict[str, Any]] | None = None,
    ) -> int:
        """把变化候选写入 change_candidates（运行审计，非个人记忆正文）。"""
        if not candidates:
            return 0
        now = _now()
        n = 0
        with self.db.transaction() as conn:
            # 表可能尚未迁移：忽略
            try:
                for c in candidates:
                    cid = new_id("chg")
                    conn.execute(
                        """
                        INSERT INTO change_candidates(
                          id, change_type, source_observation_id, target_memory_id,
                          payload_json, dry_run, applied, created_at, applied_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cid,
                            str(c.get("change_type") or "uncertain"),
                            observation_id,
                            c.get("memory_id"),
                            json.dumps(c, ensure_ascii=False),
                            1 if dry_run else 0,
                            1 if (applied and any(a.get("old") == c.get("memory_id") for a in applied)) else 0,
                            now,
                            now if applied else None,
                        ),
                    )
                    n += 1
            except sqlite3.OperationalError:
                return 0
        return n

    def store_emotion_candidates(
        self,
        *,
        observation_id: str,
        candidates: list[dict[str, Any]],
        shadow_only: bool = True,
    ) -> int:
        """情绪候选写入 emotion_candidates（审计，非 LT）。"""
        if not candidates:
            return 0
        now = _now()
        n = 0
        with self.db.transaction() as conn:
            try:
                for c in candidates:
                    cid = new_id("emoc")
                    conn.execute(
                        """
                        INSERT INTO emotion_candidates(
                          id, observation_id, source_kind, payload_json,
                          shadow_only, written, created_at
                        ) VALUES (?, ?, ?, ?, ?, 0, ?)
                        """,
                        (
                            cid,
                            observation_id,
                            str(c.get("source") or ""),
                            json.dumps(c, ensure_ascii=False),
                            1 if shadow_only else 0,
                            now,
                        ),
                    )
                    n += 1
            except sqlite3.OperationalError:
                return 0
        return n

    def list_emotion_candidates(
        self,
        *,
        limit: int = 40,
        written: bool | None = False,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit or 40)))
        with self.db.exclusive() as conn:
            try:
                sql = """
                    SELECT id, observation_id, source_kind, payload_json,
                           shadow_only, written, created_at
                    FROM emotion_candidates WHERE 1=1
                """
                args: list[Any] = []
                if written is True:
                    sql += " AND written=1"
                elif written is False:
                    sql += " AND written=0"
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(lim)
                rows = conn.execute(sql, tuple(args)).fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[dict[str, Any]] = []
        for r in rows:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            out.append(
                {
                    "id": r["id"],
                    "observation_id": r["observation_id"],
                    "source": r["source_kind"],
                    "shadow_only": bool(r["shadow_only"]),
                    "written": bool(r["written"]),
                    "created_at": r["created_at"],
                    "payload": payload,
                    "association_level": (payload.get("assessment") or {}).get("association_level"),
                }
            )
        return out

    def list_change_candidates(
        self,
        *,
        limit: int = 40,
        applied: bool | None = False,
        change_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """列出变化候选（审计表）。applied=None 表示不限。"""
        lim = max(1, min(200, int(limit or 40)))
        with self.db.exclusive() as conn:
            try:
                sql = """
                    SELECT id, change_type, source_observation_id, target_memory_id,
                           payload_json, dry_run, applied, created_at, applied_at
                    FROM change_candidates
                    WHERE 1=1
                """
                args: list[Any] = []
                if applied is True:
                    sql += " AND applied=1"
                elif applied is False:
                    sql += " AND applied=0"
                if change_types:
                    placeholders = ",".join("?" for _ in change_types)
                    sql += f" AND change_type IN ({placeholders})"
                    args.extend(str(t) for t in change_types)
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(lim)
                rows = conn.execute(sql, tuple(args)).fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[dict[str, Any]] = []
        for r in rows:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            out.append(
                {
                    "id": r["id"],
                    "change_type": r["change_type"],
                    "observation_id": r["source_observation_id"],
                    "memory_id": r["target_memory_id"],
                    "dry_run": bool(r["dry_run"]),
                    "applied": bool(r["applied"]),
                    "created_at": r["created_at"],
                    "applied_at": r["applied_at"],
                    "payload": payload,
                    "note": payload.get("note") or payload.get("reason") or "",
                    "content_preview": str(
                        payload.get("new_content")
                        or payload.get("old_content")
                        or payload.get("content")
                        or ""
                    )[:160],
                }
            )
        return out

    def store_change_plan(self, plan: dict[str, Any]) -> str:
        """持久化 ChangePlan（治理审计表）。"""
        now = _now()
        pid = str(plan.get("change_id") or new_id("cpl"))
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO change_plans(
                  id, observation_id, change_type, risk_level, confidence,
                  approval_requirement, execution_status, verification_status,
                  payload_json, rollback_reference, created_at, updated_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    plan.get("observation_id"),
                    str(plan.get("change_type") or "NO_CHANGE"),
                    str(plan.get("risk_level") or "medium"),
                    float(plan.get("confidence") or 0),
                    str(plan.get("approval_requirement") or "human"),
                    str(plan.get("execution_status") or "proposed"),
                    str(plan.get("verification_status") or "pending"),
                    json.dumps(plan, ensure_ascii=False),
                    plan.get("rollback_reference"),
                    now,
                    now,
                    None,
                ),
            )
        return pid

    def get_change_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM change_plans WHERE id=?", (plan_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.update(
            {
                "change_id": row["id"],
                "observation_id": row["observation_id"],
                "change_type": row["change_type"],
                "risk_level": row["risk_level"],
                "confidence": row["confidence"],
                "approval_requirement": row["approval_requirement"],
                "execution_status": row["execution_status"],
                "verification_status": row["verification_status"],
                "rollback_reference": row["rollback_reference"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "applied_at": row["applied_at"],
            }
        )
        return payload

    def list_change_plans(
        self,
        *,
        limit: int = 40,
        execution_status: str | None = None,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit or 40)))
        with self.db.exclusive() as conn:
            try:
                sql = "SELECT id FROM change_plans WHERE 1=1"
                args: list[Any] = []
                if execution_status:
                    sql += " AND execution_status=?"
                    args.append(execution_status)
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(lim)
                rows = conn.execute(sql, tuple(args)).fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[dict[str, Any]] = []
        for r in rows:
            p = self.get_change_plan(str(r["id"]))
            if p:
                out.append(p)
        return out

    def update_change_plan(
        self,
        plan_id: str,
        *,
        execution_status: str | None = None,
        verification_status: str | None = None,
        rollback_reference: str | None = None,
        payload_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        cur = self.get_change_plan(plan_id)
        if not cur:
            return None
        if payload_patch:
            cur.update(payload_patch)
        now = _now()
        exec_s = execution_status or cur.get("execution_status")
        ver_s = verification_status or cur.get("verification_status")
        rb = rollback_reference if rollback_reference is not None else cur.get("rollback_reference")
        applied_at = now if exec_s == "applied" else cur.get("applied_at")
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE change_plans SET
                  execution_status=?, verification_status=?, rollback_reference=?,
                  payload_json=?, updated_at=?, applied_at=?
                WHERE id=?
                """,
                (
                    exec_s,
                    ver_s,
                    rb,
                    json.dumps(cur, ensure_ascii=False),
                    now,
                    applied_at,
                    plan_id,
                ),
            )
        return self.get_change_plan(plan_id)

    def query_related_for_context(
        self,
        memory_ids: list[str],
        *,
        limit: int = 6,
    ) -> list[Memory]:
        """P3：按关系边补充关联记忆（supersedes/located_at/depends_on 等）。"""
        if not memory_ids:
            return []
        conn = self.db.connect()
        related_ids: list[str] = []
        for mid in memory_ids[:12]:
            rows = conn.execute(
                """
                SELECT CASE WHEN from_id=? THEN to_id ELSE from_id END AS other
                FROM memory_relations
                WHERE (from_id=? OR to_id=?)
                  AND relation IN ('supersedes','contradicts','depends_on','located_at','occurred_in','continues')
                ORDER BY id DESC LIMIT 4
                """,
                (mid, mid, mid),
            ).fetchall()
            for r in rows:
                oid = str(r["other"])
                if oid not in memory_ids and oid not in related_ids:
                    related_ids.append(oid)
        out: list[Memory] = []
        for rid in related_ids[:limit]:
            row = conn.execute("SELECT * FROM memories WHERE id=?", (rid,)).fetchone()
            if row:
                out.append(self._row_memory(row))
        return out

    def health(self) -> dict[str, Any]:
        conn = self.db.connect()
        mem_n = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        obs_n = conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE status='pending'"
        ).fetchone()["n"]
        principals = 0
        try:
            principals = conn.execute(
                "SELECT COUNT(*) AS n FROM identity_principals"
            ).fetchone()["n"]
        except Exception:
            principals = 0
        authority_state = None
        try:
            from cognitive_memory.governance.authority import get_authority

            authority_state = get_authority(self.db).get("state")
        except Exception:
            authority_state = None
        probes = None
        try:
            from cognitive_memory.tracing.health_probes import CognitiveHealthProbes

            probes = CognitiveHealthProbes(self.db).probe(include_write_probe=False)
        except Exception as exc:  # noqa: BLE001 — health must still return
            probes = {
                "overall": "UNKNOWN",
                "all_green": False,
                "error": str(exc),
                "read_only": True,
            }
        overall = (probes or {}).get("overall") or "OK"
        return {
            "ok": overall in {"OK", "WARN"},
            "service": "cognitive-memory",
            "version": "2.0.0",
            "memories": mem_n,
            "observations": obs_n,
            "outbox_pending": pending,
            "schemas": self.count_cognitive_schemas(),
            "spatiotemporal": self.spatiotemporal_enable,
            "emotion": self.emotion_enable,
            "emotion_write_enabled": self.emotion_write_enabled,
            "emotion_shadow_only": self.emotion_shadow_only,
            "emotion_retrieval_enabled": self.emotion_retrieval_enabled,
            "mnemosyne_version": "2.0",
            "identity": True,
            "principals": principals,
            "banks": True,
            "authority": True,
            "authority_state": authority_state,
            "probes": probes,
            "overall": overall,
            "all_green": bool((probes or {}).get("all_green")),
            "time": _now(),
        }

    def service_contract(self) -> dict[str, Any]:
        from cognitive_memory.memory.contract import contract_snapshot

        return contract_snapshot()

    def mark_emotion_candidates_written(
        self,
        observation_id: str,
        *,
        memory_id: str | None = None,
    ) -> int:
        """P3：情绪候选已 propose 后标记 written（审计，不改记忆内容）。"""
        now = _now()
        n = 0
        with self.db.transaction() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT id, payload_json FROM emotion_candidates
                    WHERE observation_id=? AND written=0
                    """,
                    (observation_id,),
                ).fetchall()
                for r in rows:
                    payload: dict[str, Any] = {}
                    try:
                        payload = json.loads(r["payload_json"] or "{}")
                    except json.JSONDecodeError:
                        payload = {}
                    if memory_id:
                        payload["proposed_memory_id"] = memory_id
                    payload["written_at"] = now
                    conn.execute(
                        """
                        UPDATE emotion_candidates
                        SET written=1, payload_json=?
                        WHERE id=?
                        """,
                        (json.dumps(payload, ensure_ascii=False), r["id"]),
                    )
                    n += 1
            except sqlite3.OperationalError:
                return 0
        return n

    # ---- Mnemosyne 2.0：CognitiveSchema / CognitiveChangePlan ----

    def store_cognitive_schema(self, schema: dict[str, Any]) -> str:
        """持久化认知图式（规律/假设；证据只引用 memory_id）。"""
        now = _now()
        sid = str(schema.get("id") or new_id("sch"))
        name = str(schema.get("name") or "未命名图式").strip() or "未命名图式"
        description = str(schema.get("description") or "")
        schema_type = str(schema.get("type") or schema.get("schema_type") or "KNOWLEDGE")
        state = str(schema.get("state") or "PROPOSED").upper()
        confidence = float(schema.get("confidence") or 0.4)
        version = int(schema.get("version") or 1)
        payload = dict(schema)
        payload["id"] = sid
        payload["type"] = schema_type
        payload["state"] = state
        payload["confidence"] = confidence
        payload.setdefault("confidence_kind", "uncalibrated")
        payload["version"] = version
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO cognitive_schemas(
                  id, name, description, schema_type, state, confidence,
                  version, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  description=excluded.description,
                  schema_type=excluded.schema_type,
                  state=excluded.state,
                  confidence=excluded.confidence,
                  version=excluded.version,
                  payload_json=excluded.payload_json,
                  updated_at=excluded.updated_at
                """,
                (
                    sid,
                    name,
                    description,
                    schema_type,
                    state,
                    confidence,
                    version,
                    json.dumps(payload, ensure_ascii=False),
                    payload.get("created_at") or now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO schema_versions(
                  id, schema_id, version, change_type, reason, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("sv"),
                    sid,
                    version,
                    str(schema.get("change_type") or "CREATE"),
                    str(schema.get("reason") or "store"),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
        return sid

    def get_cognitive_schema(self, schema_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM cognitive_schemas WHERE id=?", (schema_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.update(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "type": row["schema_type"],
                "schema_type": row["schema_type"],
                "state": row["state"],
                "confidence": row["confidence"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return payload

    def list_cognitive_schemas(
        self,
        *,
        limit: int = 40,
        state: str | None = None,
        schema_type: str | None = None,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit or 40)))
        with self.db.exclusive() as conn:
            try:
                sql = "SELECT id FROM cognitive_schemas WHERE 1=1"
                args: list[Any] = []
                if state:
                    sql += " AND state=?"
                    args.append(str(state).upper())
                if schema_type:
                    sql += " AND schema_type=?"
                    args.append(schema_type)
                sql += " ORDER BY updated_at DESC LIMIT ?"
                args.append(lim)
                rows = conn.execute(sql, tuple(args)).fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[dict[str, Any]] = []
        for r in rows:
            s = self.get_cognitive_schema(str(r["id"]))
            if s:
                out.append(s)
        return out

    def update_cognitive_schema(
        self,
        schema_id: str,
        *,
        patch: dict[str, Any] | None = None,
        state: str | None = None,
        change_type: str = "REFINE",
        reason: str = "",
    ) -> dict[str, Any] | None:
        cur = self.get_cognitive_schema(schema_id)
        if not cur:
            return None
        now = _now()
        if patch:
            cur.update(patch)
        if state:
            cur["state"] = str(state).upper()
        cur["version"] = int(cur.get("version") or 1) + 1
        cur["updated_at"] = now
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE cognitive_schemas SET
                  name=?, description=?, schema_type=?, state=?, confidence=?,
                  version=?, payload_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(cur.get("name") or ""),
                    str(cur.get("description") or ""),
                    str(cur.get("type") or cur.get("schema_type") or "KNOWLEDGE"),
                    str(cur.get("state") or "PROPOSED"),
                    float(cur.get("confidence") or 0.4),
                    int(cur["version"]),
                    json.dumps(cur, ensure_ascii=False),
                    now,
                    schema_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO schema_versions(
                  id, schema_id, version, change_type, reason, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("sv"),
                    schema_id,
                    int(cur["version"]),
                    change_type,
                    reason or change_type,
                    json.dumps(cur, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_cognitive_schema(schema_id)

    def store_cognitive_change_plan(self, plan: dict[str, Any]) -> str:
        now = _now()
        pid = str(plan.get("id") or new_id("ccp"))
        payload = dict(plan)
        targets = plan.get("targetSchemaIds") or plan.get("target_schema_ids") or []
        payload.setdefault("expected_versions", {
            str(sid): current["version"] for sid in targets
            if (current := self.get_cognitive_schema(str(sid))) is not None
        })
        payload["id"] = pid
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO cognitive_change_plans(
                  id, operation, risk_level, approval_required, status,
                  payload_json, created_at, updated_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    str(plan.get("operation") or "UPDATE"),
                    str(plan.get("riskLevel") or plan.get("risk_level") or "MEDIUM"),
                    1 if plan.get("approvalRequired", plan.get("approval_required", True)) else 0,
                    str(plan.get("status") or "PROPOSED"),
                    json.dumps(payload, ensure_ascii=False),
                    payload.get("created_at") or now,
                    now,
                    None,
                ),
            )
        return pid

    def get_cognitive_change_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.db.exclusive() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM cognitive_change_plans WHERE id=?", (plan_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.update(
            {
                "id": row["id"],
                "operation": row["operation"],
                "risk_level": row["risk_level"],
                "approval_required": bool(row["approval_required"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "applied_at": row["applied_at"],
            }
        )
        return payload

    def list_cognitive_change_plans(
        self,
        *,
        limit: int = 40,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit or 40)))
        with self.db.exclusive() as conn:
            try:
                sql = "SELECT id FROM cognitive_change_plans WHERE 1=1"
                args: list[Any] = []
                if status:
                    sql += " AND status=?"
                    args.append(str(status).upper())
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(lim)
                rows = conn.execute(sql, tuple(args)).fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[dict[str, Any]] = []
        for r in rows:
            p = self.get_cognitive_change_plan(str(r["id"]))
            if p:
                out.append(p)
        return out

    def update_cognitive_change_plan(
        self,
        plan_id: str,
        *,
        status: str | None = None,
        payload_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        cur = self.get_cognitive_change_plan(plan_id)
        if not cur:
            return None
        now = _now()
        if payload_patch:
            cur.update(payload_patch)
        st = str(status or cur.get("status") or "PROPOSED").upper()
        cur["status"] = st
        cur["updated_at"] = now
        applied_at = now if st == "APPLIED" else cur.get("applied_at")
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE cognitive_change_plans SET
                  status=?, payload_json=?, updated_at=?, applied_at=?
                WHERE id=?
                """,
                (st, json.dumps(cur, ensure_ascii=False), now, applied_at, plan_id),
            )
        return self.get_cognitive_change_plan(plan_id)

    def count_cognitive_schemas(self) -> int:
        try:
            row = self.db.connect().execute(
                "SELECT COUNT(*) AS n FROM cognitive_schemas"
            ).fetchone()
            return int(row["n"] if row else 0)
        except sqlite3.OperationalError:
            return 0
