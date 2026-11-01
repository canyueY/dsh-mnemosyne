# -*- coding: utf-8 -*-
"""Outbox dispatcher + 任务 worker（至少一次投递 + 幂等消费）。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cognitive_memory.core.service import MemoryService
from cognitive_memory.events.bus import EventBus
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundRuntime:
    def __init__(
        self,
        db: Database,
        bus: EventBus,
        service: MemoryService,
        *,
        poll_interval_ms: int = 200,
        max_retries: int = 5,
    ):
        self.db = db
        self.bus = bus
        self.service = service
        self.poll_interval = max(0.05, poll_interval_ms / 1000.0)
        self.max_retries = max_retries
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task_handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "memory.consolidation": self._handle_consolidation,
            "memory.promote_check": self._handle_promote,
            "memory.review_feedback": self._handle_feedback_review,
            "memory.change_detect": self._handle_change_detect,
            "memory.reevaluate": self._handle_reevaluate,
            "memory.pattern_scan": self._handle_pattern_scan,
            "memory.emotion_extract": self._handle_emotion_extract,
            "memory.emotion_pattern_scan": self._handle_emotion_pattern_scan,
        }
        # E11 / FA20：主链路租约与消费水位（不再只存在于 helper 单测）
        from cognitive_memory.strategy.fencing import WorkerLeaseManager
        from cognitive_memory.strategy.watermarks import ConsumerWatermarkStore

        self.leases = WorkerLeaseManager(db)
        self.watermarks = ConsumerWatermarkStore(db)
        self._worker_id = f"dispatcher_{id(self) & 0xFFFF:x}"
        self._lease: dict[str, Any] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cm-dispatcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.dispatch_outbox_once()
                self.run_tasks_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[dispatcher] loop error: {exc}", flush=True)
            self._stop.wait(self.poll_interval)

    def dispatch_outbox_once(self, limit: int = 32) -> int:
        now = _now()
        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.event_id, o.attempts, e.event_type, e.payload_json,
                       e.producer, e.correlation_id, e.causation_id, e.occurred_at, e.recorded_at
                FROM outbox o
                JOIN events e ON e.event_id = o.event_id
                WHERE o.status='pending' AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= ?)
                ORDER BY o.id ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        n = 0
        for row in rows:
            event_id = row["event_id"]
            consumer = "local_bus"
            envelope = {
                "event_id": event_id,
                "event_type": row["event_type"],
                "event_version": 1,
                "occurred_at": row["occurred_at"],
                "recorded_at": row["recorded_at"],
                "producer": row["producer"],
                "correlation_id": row["correlation_id"],
                "causation_id": row["causation_id"],
                "payload_version": 1,
                "payload": json.loads(row["payload_json"] or "{}"),
            }
            try:
                with self.db.exclusive() as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM inbox WHERE event_id=? AND consumer=?",
                        (event_id, consumer),
                    ).fetchone()
                    if not exists:
                        self.bus.publish_local(envelope)
                        conn.execute(
                            "INSERT OR IGNORE INTO inbox(event_id, consumer, processed_at) VALUES (?, ?, ?)",
                            (event_id, consumer, _now()),
                        )
                    conn.execute(
                        "UPDATE outbox SET status='delivered', updated_at=? WHERE event_id=?",
                        (_now(), event_id),
                    )
                    conn.commit()
                n += 1
            except Exception as exc:  # noqa: BLE001
                attempts = int(row["attempts"]) + 1
                if attempts >= self.max_retries:
                    status = "dead"
                    next_at = None
                else:
                    status = "pending"
                    next_at = (_now_dt() + timedelta(seconds=2 ** attempts)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    self.bus.publish_local(
                        {
                            "event_id": f"retry_{event_id}_{attempts}",
                            "event_type": "task.retry_scheduled",
                            "event_version": 1,
                            "occurred_at": _now(),
                            "recorded_at": _now(),
                            "producer": "dispatcher",
                            "correlation_id": event_id,
                            "causation_id": event_id,
                            "payload_version": 1,
                            "payload": {"event_id": event_id, "attempts": attempts},
                        }
                    )
                with self.db.exclusive() as conn:
                    conn.execute(
                        """
                        UPDATE outbox SET status=?, attempts=?, next_attempt_at=?, last_error=?, updated_at=?
                        WHERE event_id=?
                        """,
                        (status, attempts, next_at, str(exc)[:500], _now(), event_id),
                    )
                    conn.commit()
        return n

    def ensure_lease(self, *, resource: str = "consolidation") -> dict[str, Any]:
        lease = self.leases.acquire(worker_id=self._worker_id, resource=resource)
        self._lease = lease
        return lease

    def run_tasks_once(self, limit: int = 16) -> int:
        now = _now()
        with self.db.exclusive() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE task_type!='cognitive.meta' AND status='pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            # 读完即提交，避免挂起隐式事务挡住主线程 capture
            if conn.in_transaction:
                conn.commit()
        n = 0
        for row in rows:
            tid = row["id"]
            ttype = row["task_type"]
            payload = json.loads(row["payload_json"] or "{}")
            handler = self._task_handlers.get(ttype)
            lease = self.ensure_lease(resource="consolidation")
            with self.db.exclusive() as conn:
                conn.execute(
                    "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                    (_now(), tid),
                )
                conn.commit()
            try:
                if handler:
                    handler({**payload, "_lease": lease})
                # 任务完成产物经租约 fencing 提交，旧 worker 不能靠最终 UPDATE 冒充成功
                self.leases.commit_artifact(
                    fencing_token=str(lease["fencing_token"]),
                    expected_generation=int(lease["generation"]),
                    artifact={
                        "task_id": tid,
                        "task_type": ttype,
                        "status": "done",
                    },
                    resource=str(lease.get("resource") or "consolidation"),
                )
                pos = self.watermarks.get_watermark("utility") + 1
                self.watermarks.apply_effect_with_watermark(
                    consumer="utility",
                    event_id=f"task_done:{tid}",
                    effect_payload={"task_id": tid, "task_type": ttype},
                    next_position=pos,
                    fencing_token=str(lease["fencing_token"]),
                    expected_generation=int(lease["generation"]),
                )
                with self.db.exclusive() as conn:
                    conn.execute(
                        "UPDATE tasks SET status='done', updated_at=?, last_error='' WHERE id=?",
                        (_now(), tid),
                    )
                    conn.commit()
                n += 1
            except Exception as exc:  # noqa: BLE001
                attempts = int(row["attempts"]) + 1
                if attempts >= self.max_retries:
                    status = "dead"
                    next_at = None
                else:
                    status = "pending"
                    next_at = (_now_dt() + timedelta(seconds=2 ** attempts)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                with self.db.exclusive() as conn:
                    conn.execute(
                        """
                        UPDATE tasks SET status=?, attempts=?, next_attempt_at=?, last_error=?, updated_at=?
                        WHERE id=?
                        """,
                        (status, attempts, next_at, str(exc)[:500], _now(), tid),
                    )
                    conn.commit()
        from cognitive_memory.strategy.task_queue import MetaTaskQueue
        meta=MetaTaskQueue(self.db).run_once()
        return n + int(meta.get("executed",False))

    def _handle_consolidation(self, payload: dict[str, Any]) -> None:
        """慢速通道 V1：把观察整理为 episodic 候选（规则式，可后续换 LLM）。"""
        from cognitive_memory.cognition.consolidate import consolidate_observation
        from cognitive_memory.strategy.fencing import assert_lease_writable

        lease = payload.get("_lease") or self._lease
        if lease:
            assert_lease_writable(
                self.db,
                fencing_token=str(lease.get("fencing_token") or ""),
                expected_generation=int(lease.get("generation") or 0),
                resource=str(lease.get("resource") or "consolidation"),
            )
        consolidate_observation(self.service, payload.get("observation_id") or "")

    def _handle_promote(self, payload: dict[str, Any]) -> None:
        from cognitive_memory.cognition.promote import maybe_promote

        maybe_promote(self.service, payload.get("memory_id") or "")

    def _handle_feedback_review(self, payload: dict[str, Any]) -> None:
        mid = payload.get("memory_id") or ""
        if not mid:
            return
        # 标记不正确 → 失效
        from cognitive_memory.domain.models import MemoryStatus, MemoryUpdateIn

        self.service.update_memory(mid, MemoryUpdateIn(status=MemoryStatus.INVALIDATED))

    def _handle_change_detect(self, payload: dict[str, Any]) -> None:
        from cognitive_memory.cognition.change_detect import detect_changes_for_observation

        oid = payload.get("observation_id") or ""
        dry = payload.get("dry_run", True) is not False
        apply_l2 = bool(payload.get("apply_l2"))
        result = detect_changes_for_observation(
            self.service, oid, dry_run=dry, apply_l2=apply_l2
        )
        self.service.store_change_candidates(
            observation_id=oid,
            candidates=list(result.get("candidates") or []),
            dry_run=dry,
            applied=list(result.get("applied") or []),
        )

    def _handle_reevaluate(self, payload: dict[str, Any]) -> None:
        from cognitive_memory.cognition.reevaluate import run_reevaluation

        run_reevaluation(
            self.service,
            dry_run=payload.get("dry_run", True) is not False,
            apply_l2=bool(payload.get("apply_l2")),
            limit=int(payload.get("limit") or 40),
        )

    def _handle_emotion_extract(self, payload: dict[str, Any]) -> None:
        from cognitive_memory.cognition.emotion_extract import extract_emotion_for_observation

        oid = payload.get("observation_id") or ""
        if not oid:
            return
        # 任务 payload 可覆盖；缺省跟服务宿主开关
        shadow = payload.get("shadow_only")
        if shadow is None:
            shadow = self.service.emotion_shadow_only
        write = payload.get("write_enabled")
        if write is None:
            write = self.service.emotion_write_enabled
        extract_emotion_for_observation(
            self.service,
            oid,
            shadow_only=shadow is not False,
            write_enabled=bool(write),
            min_confidence=float(payload.get("min_confidence") or 0.35),
        )

    def _handle_emotion_pattern_scan(self, payload: dict[str, Any]) -> None:
        from cognitive_memory.cognition.emotion_patterns import scan_emotion_behavior_patterns
        from cognitive_memory.domain.models import ObservationIn, SourceKind

        pack = scan_emotion_behavior_patterns(
            self.service,
            limit_obs=int(payload.get("limit_obs") or 400),
            min_days=int(payload.get("min_days") or 2),
            min_count=int(payload.get("min_count") or 2),
        )
        if not payload.get("persist_summary", True):
            return
        cands = pack.get("pattern_candidates") or []
        if not cands:
            return
        lines = ["【桌宠梳理·情绪行为模式候选】仅趋势，非已确认因果"]
        for c in cands[:10]:
            lines.append(
                f"- {c.get('emotion')}→{c.get('behavior')} "
                f"days={c.get('day_count')} count={c.get('count')}"
            )
        self.service.capture_observation(
            ObservationIn(
                source_kind=SourceKind.SYSTEM,
                content="\n".join(lines),
                producer="mnemosyne.pattern_scan",
                metadata={"mnemosyne_pattern_scan": True, "count": pack.get("count")},
            )
        )

    def _handle_pattern_scan(self, payload: dict[str, Any]) -> None:
        from cognitive_memory.cognition.persistence import scan_persistence_patterns

        # 结果写入一条 system observation，便于检索（仍经 Memory 入口）
        pack = scan_persistence_patterns(
            self.service,
            limit_obs=int(payload.get("limit_obs") or 400),
            min_days=int(payload.get("min_days") or 3),
            min_count=int(payload.get("min_count") or 3),
        )
        if not payload.get("persist_summary", True):
            return
        from cognitive_memory.domain.models import ObservationIn, SourceKind

        habits = pack.get("habit_candidates") or []
        scenes = pack.get("scene_candidates") or []
        lines = ["【桌宠梳理·行为模式候选】仅趋势，非已确认事实"]
        for h in habits[:8]:
            lines.append(
                f"- 跨日重复「{h.get('topic')}」days={len(h.get('days') or [])} count={h.get('count')}"
            )
        for s in scenes[:6]:
            lines.append(
                f"- 场景条件「{s.get('topic')}」@ {s.get('space')} count={s.get('count')}"
            )
        if len(lines) == 1:
            lines.append("- （本轮未形成稳定候选）")
        self.service.capture_observation(
            ObservationIn(
                source_kind=SourceKind.SYSTEM,
                content="\n".join(lines)[:8000],
                producer="murasame.pattern_scan",
                metadata={
                    "temporal": {"kind": "habit", "status": "uncertain"},
                    "digest": True,
                    "pattern_scan": True,
                },
                idempotency_key=f"pattern:{pack.get('generated_at')}",
            )
        )
