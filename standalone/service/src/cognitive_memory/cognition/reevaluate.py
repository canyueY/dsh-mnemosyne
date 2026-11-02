# -*- coding: utf-8 -*-
"""P4：重新评估调度逻辑（运行态任务，不另建个人记忆库）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.spatiotemporal import get_spatiotemporal, is_temporally_expired, parse_ts


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_reevaluation_targets(
    service: MemoryService,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """找出需要重新评估的记忆：过期、reevaluationAt 到期、历史空间仍标 active。"""
    conn = service.db.connect()
    rows = conn.execute(
        """
        SELECT id, content, status, expires_at, metadata_json, updated_at, last_confirmed_at
        FROM memories
        WHERE status IN ('active','candidate')
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (max(20, int(limit) * 3),),
    ).fetchall()
    now = _now()
    out: list[dict[str, Any]] = []
    for row in rows:
        meta = {}
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        reasons: list[str] = []
        if is_temporally_expired(meta, expires_at=row["expires_at"], now=now):
            reasons.append("expired")
        temporal = get_spatiotemporal(meta).get("temporal") or {}
        re_at = parse_ts(temporal.get("reevaluationAt"))
        if re_at is not None and re_at <= now:
            reasons.append("reevaluation_due")
        spatial = get_spatiotemporal(meta).get("spatial") or {}
        vu = parse_ts(spatial.get("validUntil"))
        if vu is not None and vu < now:
            reasons.append("historical_space_still_listed")
        # 长时间未确认（>30 天）→ 仅标记，不自动改事实
        confirmed = parse_ts(row["last_confirmed_at"] or row["updated_at"])
        if confirmed is not None and (now - confirmed).days >= 30:
            reasons.append("stale_confirmation")
        if not reasons:
            continue
        out.append(
            {
                "memory_id": row["id"],
                "status": row["status"],
                "reasons": reasons,
                "content_preview": str(row["content"] or "")[:120],
            }
        )
        if len(out) >= limit:
            break
    return out


def run_reevaluation(
    service: MemoryService,
    *,
    dry_run: bool = True,
    limit: int = 40,
    apply_l2: bool = False,
) -> dict[str, Any]:
    """执行一轮重新评估。

    L1（默认）：只列出目标。
    L2（apply_l2）：对 expired 且 status=active 的记忆，把 temporal.status 标 uncertain（不删内容）。
    L3：冲突/重要纠正仍只返回候选，由 Agent 决定是否问用户。
    """
    targets = list_reevaluation_targets(service, limit=limit)
    applied: list[dict[str, Any]] = []
    if not dry_run and apply_l2:
        from cognitive_memory.domain.models import MemoryUpdateIn
        from cognitive_memory.domain.spatiotemporal import attach_spatiotemporal

        for t in targets:
            if "expired" not in t["reasons"] and "historical_space_still_listed" not in t["reasons"]:
                continue
            try:
                mem = service.get_memory(t["memory_id"])
                meta = dict(mem.get("metadata") or {})
                meta = attach_spatiotemporal(meta)
                st = meta.setdefault("spatiotemporal", {})
                temporal = dict(st.get("temporal") or {})
                temporal["status"] = "uncertain"
                st["temporal"] = temporal
                meta["spatiotemporal"] = st
                meta["reevaluation"] = {
                    "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "reasons": t["reasons"],
                    "level": "L2",
                }
                service.update_memory(t["memory_id"], MemoryUpdateIn(metadata=meta))
                applied.append({"memory_id": t["memory_id"], "action": "mark_uncertain"})
            except Exception as exc:  # noqa: BLE001
                applied.append({"memory_id": t["memory_id"], "error": str(exc)})

    return {
        "ok": True,
        "dry_run": dry_run,
        "apply_l2": apply_l2,
        "targets": targets,
        "count": len(targets),
        "applied": applied,
    }
