# -*- coding: utf-8 -*-
"""P2：变化检测与状态演化候选（默认 dry-run，不直接覆盖事实）。"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.spatiotemporal import get_spatiotemporal

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2}|[A-Za-z0-9_]{3,}")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _loads_meta(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def find_related_memories(
    conn: sqlite3.Connection,
    *,
    content: str,
    limit: int = 8,
) -> list[sqlite3.Row]:
    terms = list(_tokens(content))[:8]
    if not terms:
        return []
    likes = " OR ".join(["content LIKE ?"] * len(terms))
    rows = conn.execute(
        f"""
        SELECT * FROM memories
        WHERE status IN ('active','candidate')
          AND ({likes})
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (*[f"%{t}%" for t in terms], int(limit)),
    ).fetchall()
    return list(rows)


def classify_change(
    new_content: str,
    old_content: str,
    *,
    new_meta: dict[str, Any] | None = None,
    old_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """比较新旧内容，产出变化候选（证据不足 → uncertain）。"""
    nt, ot = _tokens(new_content), _tokens(old_content)
    sim = _jaccard(nt, ot)
    new_st = get_spatiotemporal(new_meta)
    old_st = get_spatiotemporal(old_meta)
    new_space = (new_st.get("spatial") or {}).get("spaceId") or (new_st.get("spatial") or {}).get(
        "name"
    )
    old_space = (old_st.get("spatial") or {}).get("spaceId") or (old_st.get("spatial") or {}).get(
        "name"
    )

    if new_space and old_space and new_space != old_space and sim >= 0.15:
        return {
            "change_type": "evolves",
            "reason": "spatial_moved",
            "similarity": round(sim, 3),
            "suggested_relation": "supersedes",
            "confidence": min(0.85, 0.45 + sim),
        }

    if sim >= 0.72:
        return {
            "change_type": "continues",
            "reason": "high_overlap",
            "similarity": round(sim, 3),
            "suggested_relation": "continues",
            "confidence": sim,
        }
    if sim >= 0.35:
        neg = any(
            k in (new_content or "")
            for k in ("不是", "不再", "取消", "错了", "其实没有", "已经结束", "完成了")
        )
        if neg:
            return {
                "change_type": "corrects",
                "reason": "negation_with_overlap",
                "similarity": round(sim, 3),
                "suggested_relation": "contradicts",
                "confidence": 0.55,
            }
        return {
            "change_type": "evolves",
            "reason": "partial_overlap",
            "similarity": round(sim, 3),
            "suggested_relation": "supersedes",
            "confidence": 0.4 + sim * 0.3,
        }
    if sim <= 0.08:
        return {
            "change_type": "new",
            "reason": "low_overlap",
            "similarity": round(sim, 3),
            "suggested_relation": None,
            "confidence": 0.5,
        }
    return {
        "change_type": "uncertain",
        "reason": "ambiguous_overlap",
        "similarity": round(sim, 3),
        "suggested_relation": None,
        "confidence": 0.3,
    }


def detect_changes_for_observation(
    service: MemoryService,
    observation_id: str,
    *,
    dry_run: bool = True,
    apply_l2: bool = False,
) -> dict[str, Any]:
    """对单条观察做相关记忆变化检测。

    dry_run=True：只返回候选。
    apply_l2=True 且 confidence≥0.7：写 supersedes 关系；corrects/supersedes 可标旧记忆 superseded。
    """
    conn = service.db.connect()
    row = conn.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
    if not row:
        return {"ok": False, "detail": "observation_not_found", "candidates": []}

    content = str(row["content"] or "")
    meta = _loads_meta(row["metadata_json"])
    related = find_related_memories(conn, content=content, limit=8)
    candidates: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    new_mem = conn.execute(
        """
        SELECT m.id FROM memories m
        JOIN memory_evidence e ON e.memory_id=m.id
        WHERE e.observation_id=?
        ORDER BY m.created_at DESC LIMIT 1
        """,
        (observation_id,),
    ).fetchone()
    new_id = str(new_mem["id"]) if new_mem else None

    for mem in related:
        mid = str(mem["id"])
        if new_id and mid == new_id:
            continue
        old_meta = _loads_meta(mem["metadata_json"])
        verdict = classify_change(content, str(mem["content"] or ""), new_meta=meta, old_meta=old_meta)
        item = {
            "observation_id": observation_id,
            "memory_id": mid,
            "new_memory_id": new_id,
            "memory_content_preview": str(mem["content"] or "")[:160],
            **verdict,
        }
        candidates.append(item)

        if dry_run or not apply_l2 or not new_id:
            continue
        if float(verdict.get("confidence") or 0) < 0.7:
            continue
        if verdict["change_type"] not in ("supersedes", "corrects", "evolves"):
            continue
        rel = verdict.get("suggested_relation") or "supersedes"
        if rel not in ("supersedes", "contradicts", "continues"):
            continue
        try:
            service.link_memories(
                from_id=new_id,
                to_id=mid,
                relation=rel if rel != "continues" else "supersedes",
                note=f"auto_l2:{verdict['change_type']}",
            )
            if verdict["change_type"] in ("supersedes", "corrects"):
                from cognitive_memory.domain.models import MemoryStatus, MemoryUpdateIn

                service.update_memory(mid, MemoryUpdateIn(status=MemoryStatus.SUPERSEDED))
            applied.append({"old": mid, "new": new_id, "relation": rel, "change_type": verdict["change_type"]})
        except Exception as exc:  # noqa: BLE001
            applied.append({"error": str(exc), "memory_id": mid})

    if not candidates:
        candidates.append(
            {
                "observation_id": observation_id,
                "memory_id": None,
                "change_type": "new",
                "reason": "no_related",
                "similarity": 0.0,
                "confidence": 0.6,
            }
        )

    return {
        "ok": True,
        "observation_id": observation_id,
        "dry_run": dry_run,
        "apply_l2": apply_l2,
        "candidates": candidates,
        "applied": applied,
    }
