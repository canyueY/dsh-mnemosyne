# -*- coding: utf-8 -*-
"""消费者水位与事件去重（E11）。

utility / co_retrieval 独立水位；效果写入与水位推进同事务。
崩溃窗口重放等价于单次成功。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

CONSUMERS = ("utility", "co_retrieval")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConsumerWatermarkStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def get_watermark(self, consumer: str, *, stream: str = "default") -> int:
        if consumer not in CONSUMERS:
            raise ValueError(f"unknown_consumer:{consumer}")
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT position FROM consumer_watermarks WHERE consumer=? AND stream=?",
                (consumer, stream),
            ).fetchone()
        return int(row["position"]) if row else 0

    def apply_effect_with_watermark(
        self,
        *,
        consumer: str,
        event_id: str,
        effect_payload: dict[str, Any],
        next_position: int,
        stream: str = "default",
        crash_after_effect: bool = False,
        fencing_token: str | None = None,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        """效果与水位同事务。crash_after_effect 模拟写效果后、推水位前崩溃 → 整体回滚。"""
        if consumer not in CONSUMERS:
            raise ValueError(f"unknown_consumer:{consumer}")

        # 租约 fencing：过期代次不得写真实效果
        if fencing_token is not None or expected_generation is not None:
            from cognitive_memory.strategy.fencing import assert_lease_writable

            assert_lease_writable(
                self.db,
                fencing_token=fencing_token or "",
                expected_generation=expected_generation,
            )

        with self.db.transaction() as conn:
            # 事件去重
            existed = conn.execute(
                "SELECT 1 FROM consumer_event_dedupe WHERE consumer=? AND event_id=?",
                (consumer, event_id),
            ).fetchone()
            if existed:
                pos = conn.execute(
                    "SELECT position FROM consumer_watermarks WHERE consumer=? AND stream=?",
                    (consumer, stream),
                ).fetchone()
                return {
                    "ok": True,
                    "deduped": True,
                    "consumer": consumer,
                    "position": int(pos["position"]) if pos else 0,
                    "note": "replay_equivalent_to_single_success",
                }

            now = _now()
            effect_id = f"eff_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO consumer_effects(id, consumer, event_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    effect_id,
                    consumer,
                    event_id,
                    json.dumps(effect_payload, ensure_ascii=False),
                    now,
                ),
            )
            if crash_after_effect:
                # 模拟崩溃：抛错使事务回滚 → 效果与水位都不保留
                raise RuntimeError("injected_crash_after_effect_before_watermark")

            conn.execute(
                """
                INSERT INTO consumer_watermarks(consumer, stream, position, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(consumer, stream) DO UPDATE SET
                  position=excluded.position,
                  updated_at=excluded.updated_at
                """,
                (consumer, stream, int(next_position), now),
            )
            conn.execute(
                """
                INSERT INTO consumer_event_dedupe(consumer, event_id, effect_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (consumer, event_id, effect_id, now),
            )
        return {
            "ok": True,
            "deduped": False,
            "consumer": consumer,
            "position": int(next_position),
            "effect_id": effect_id,
        }

    def effect_count(self, consumer: str) -> int:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM consumer_effects WHERE consumer=?",
                (consumer,),
            ).fetchone()
        return int(row["n"])
