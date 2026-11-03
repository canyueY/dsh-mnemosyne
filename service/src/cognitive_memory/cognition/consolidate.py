# -*- coding: utf-8 -*-
"""慢速通道：观察 → 情节候选（V1 规则版，可替换为 LLM）。

E10：会话原子 → 主题/项目派生摘要（规则版）；用途视图复用现有类型/元数据。
后台巩固默认关闭（待 E11 写保护）；可取消/暂停，不发布半成品，不改原始记录。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryType, SourceKind

# E10：后台默认关闭；E11 fencing/水位就绪后允许显式开启
BACKGROUND_CONSOLIDATION_ENABLED = False


def enable_background_consolidation(*, confirm_e11_protections: bool = False) -> bool:
    """仅在确认 E11 水位/fencing 可用后开启后台巩固。"""
    global BACKGROUND_CONSOLIDATION_ENABLED
    if not confirm_e11_protections:
        return False
    BACKGROUND_CONSOLIDATION_ENABLED = True
    return True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def consolidate_observation(
    service: MemoryService,
    observation_id: str,
    *,
    write_guard: Callable[[], None] | None = None,
) -> str | None:
    from cognitive_memory.governance.workspace import assert_workspace_authorized

    if not observation_id:
        return None
    conn = service.db.connect()
    row = conn.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
    if not row:
        return None
    # 已有以该观察为证据的 episodic → 跳过
    exists = conn.execute(
        """
        SELECT m.id FROM memories m
        JOIN memory_evidence e ON e.memory_id = m.id
        WHERE e.observation_id=? AND m.type='episodic'
        LIMIT 1
        """,
        (observation_id,),
    ).fetchone()
    if exists:
        return exists["id"]

    try:
        raw_meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        raw_meta = {}
    if not isinstance(raw_meta, dict):
        raw_meta = {}
    # E01：巩固入口执行工作区授权；授权读取仍不自动 verified。
    assert_workspace_authorized(service.db, raw_meta, action="consolidate")

    kind = row["source_kind"]
    content = row["content"]
    from cognitive_memory.memory.shallow import is_external_perception
    if is_external_perception(kind, raw_meta):
        # Capture already created the first-level working view atomically.
        # Replayed consolidation tasks must not create a long-term candidate.
        shallow = conn.execute("""SELECT m.id FROM memories m JOIN memory_evidence e ON e.memory_id=m.id
            WHERE e.observation_id=? AND m.type='working'
            AND json_valid(m.metadata_json) AND json_type(m.metadata_json,'$.shallow_memory')='object'
            LIMIT 1""", (observation_id,)).fetchone()
        return shallow[0] if shallow else None
    # 对话类已有 working；视频/系统观察生成 episodic 候选
    if kind in (SourceKind.DIALOG.value, SourceKind.USER_STATEMENT.value):
        # 从 working 提升为 episodic 候选摘要
        summary = f"对话片段：{content[:400]}"
        mem_type = MemoryType.EPISODIC
        status = MemoryStatus.CANDIDATE
    elif kind == SourceKind.VIDEO_OBSERVATION.value:
        summary = f"视频观察：{content[:500]}"
        mem_type = MemoryType.EPISODIC
        status = MemoryStatus.CANDIDATE
    else:
        summary = content[:500]
        mem_type = MemoryType.EPISODIC
        status = MemoryStatus.CANDIDATE

    meta: dict = {
        "consolidation": "rule_v1",
        "source_kind": kind,
        "epistemic": "summary",
        "original_observation_id": observation_id,
        "lossy": len(content)
        > (400 if kind in (SourceKind.DIALOG.value, SourceKind.USER_STATEMENT.value) else 500),
        "source_role": "derived_summary",
    }
    if raw_meta.get("workspace_id") or raw_meta.get("workspace"):
        meta["workspace_id"] = raw_meta.get("workspace_id") or raw_meta.get("workspace")
    st = raw_meta.get("spatiotemporal") if isinstance(raw_meta, dict) else None
    if isinstance(st, dict):
        meta["spatiotemporal"] = st

    mem = service.propose_memory(
        MemoryProposeIn(
            type=mem_type,
            status=status,
            content=summary,
            source_ids=[observation_id],
            metadata=meta,
            idempotency_key=f"consol:{observation_id}",
        ),
        write_guard=write_guard,
    )
    return mem.id


def _negation_markers(text: str) -> list[str]:
    markers = []
    for tok in ("不", "没", "无", "非", "未", "否认", "否定", "not", "no", "never"):
        if tok in (text or "").lower():
            markers.append(tok)
    return markers


def build_session_utility_view(
    atoms: list[dict[str, Any]],
    *,
    allowed_ids: set[str] | None = None,
) -> dict[str, Any]:
    """用途视图：复用现有字段，不新建七套库；摘要/图式/规则分开。"""
    selected = []
    for atom in atoms:
        aid = str(atom.get("id") or "")
        if allowed_ids is not None and aid not in allowed_ids:
            continue
        selected.append(atom)

    passed = sum(1 for a in selected if a.get("result") == "passed")
    failed = sum(1 for a in selected if a.get("result") == "failed")
    skipped = sum(1 for a in selected if a.get("result") == "skipped")
    negations = []
    source_anchors = []
    for a in selected:
        content = str(a.get("content") or "")
        negs = list(a.get("negations") or []) or _negation_markers(content)
        for n in negs:
            negations.append({"atom_id": a.get("id"), "marker": n, "content": content[:120]})
        for sid in a.get("source_ids") or []:
            source_anchors.append({"atom_id": a.get("id"), "source_id": sid})
        if a.get("source_id"):
            source_anchors.append({"atom_id": a.get("id"), "source_id": a.get("source_id")})

    # 不能把失败抹成全通过
    all_pass_forged = failed == 0 and skipped == 0 and passed == len(selected) and len(selected) > 0
    truthful_counts = {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total_selected": len(selected),
    }
    summary_rule = (
        f"会话统计：通过 {passed}，失败 {failed}，跳过 {skipped}；"
        f"否定标记 {len(negations)} 条。"
    )
    if failed > 0:
        summary_rule += "存在失败项，不得表述为全部通过。"

    schema_view = {
        "kind": "session_theme_skeleton",
        "node_count": len(selected),
        "has_failures": failed > 0,
        "has_negations": len(negations) > 0,
    }
    return {
        "summary": {
            "text": summary_rule,
            "kind": "rule_summary_not_full_semantic_merge",
            "counts": truthful_counts,
            "forged_all_pass": False,
            "all_pass_only_if": all_pass_forged and failed == 0,
        },
        "schema": schema_view,
        "rules": {
            "preserve_counts": True,
            "preserve_negations": True,
            "preserve_source_anchors": True,
            "summary_is_not_new_evidence": True,
            "do_not_delete_raw_evidence": True,
        },
        "negations": negations,
        "source_anchors": source_anchors,
        "selected_atom_ids": [str(a.get("id")) for a in selected],
    }


class SessionConsolidationJob:
    """会话巩固任务：可暂停/取消；半成品不发布；不修改原始记录。"""

    def __init__(
        self,
        *,
        session_id: str,
        atoms: list[dict[str, Any]],
        allowed_input_ids: set[str] | None = None,
        topic: str = "",
    ) -> None:
        self.job_id = f"sconsol_{uuid.uuid4().hex[:12]}"
        self.session_id = session_id
        self.atoms = list(atoms)
        self.allowed_input_ids = set(allowed_input_ids) if allowed_input_ids is not None else None
        self.topic = topic
        self.status = "pending"
        self.cancel_requested = False
        self.pause_requested = False
        self.published = False
        self.result: dict[str, Any] | None = None
        self.original_fingerprints = {
            str(a.get("id")): json.dumps(a, ensure_ascii=False, sort_keys=True)
            for a in self.atoms
        }
        self._lock = threading.Lock()

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True

    def request_pause(self) -> None:
        with self._lock:
            self.pause_requested = True

    def resume(self) -> None:
        with self._lock:
            self.pause_requested = False
            if self.status == "paused":
                self.status = "running"

    def run(self, *, foreground_preempt: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.cancel_requested:
                self.status = "cancelled"
                return self._pack(published=False, reason="cancelled_before_start")
            self.status = "running"

        if foreground_preempt or self.pause_requested:
            with self._lock:
                self.status = "paused"
                self.pause_requested = True
            return self._pack(published=False, reason="preempted_by_foreground")

        # 范围：仅实际允许创建/读取/交付
        view = build_session_utility_view(self.atoms, allowed_ids=self.allowed_input_ids)
        out_of_scope = [
            str(a.get("id"))
            for a in self.atoms
            if self.allowed_input_ids is not None
            and str(a.get("id")) not in self.allowed_input_ids
        ]

        if self.cancel_requested or self.pause_requested:
            with self._lock:
                self.status = "cancelled" if self.cancel_requested else "paused"
            # 半成品不发布
            return self._pack(
                published=False,
                reason="interrupted_before_publish",
                draft=view,
                out_of_scope=out_of_scope,
            )

        # 解析不确定时标 partial/lossy
        lossy = any(a.get("lossy") or a.get("parse_uncertain") for a in self.atoms)
        fidelity = "partial" if lossy or out_of_scope else "complete"

        derived = {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "topic": self.topic,
            "utility_view": view,
            "dependencies": {
                "atom_ids": view["selected_atom_ids"],
                "source_anchors": view["source_anchors"],
                "out_of_scope_excluded": out_of_scope,
            },
            "fidelity": fidelity,
            "lossy": bool(lossy),
            "importance_score": float(view["summary"]["counts"].get("passed") or 0),
            "source_refs": [a.get("source_id") for a in view.get("source_anchors") or []],
            "consolidation_level": "derived_summary",
            "epistemic": "derived_summary",
            "not_new_evidence": True,
            "background_enabled_default": BACKGROUND_CONSOLIDATION_ENABLED,
            "created_at": _now(),
        }
        # 原始记录指纹不得被改写
        unchanged = all(
            self.original_fingerprints.get(str(a.get("id")))
            == json.dumps(a, ensure_ascii=False, sort_keys=True)
            for a in self.atoms
        )
        with self._lock:
            self.status = "done"
            self.published = True
            self.result = derived
        return self._pack(
            published=True,
            reason="published",
            derived=derived,
            originals_unchanged=unchanged,
            out_of_scope=out_of_scope,
        )

    def _pack(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "job_id": self.job_id,
            "session_id": self.session_id,
            "status": self.status,
            "published": bool(kwargs.get("published")),
            "reason": kwargs.get("reason"),
            "derived": kwargs.get("derived"),
            "draft": kwargs.get("draft"),
            "out_of_scope": kwargs.get("out_of_scope") or [],
            "originals_unchanged": kwargs.get("originals_unchanged", True),
            "background_consolidation_enabled": BACKGROUND_CONSOLIDATION_ENABLED,
        }


def consolidate_session(
    *,
    session_id: str,
    atoms: list[dict[str, Any]],
    allowed_input_ids: set[str] | list[str] | None = None,
    topic: str = "",
    foreground_preempt: bool = False,
    job: SessionConsolidationJob | None = None,
) -> dict[str, Any]:
    """会话巩固真实入口（规则摘要，非付费模型语义整合）。"""
    allowed = set(allowed_input_ids) if allowed_input_ids is not None else None
    work = job or SessionConsolidationJob(
        session_id=session_id,
        atoms=atoms,
        allowed_input_ids=allowed,
        topic=topic,
    )
    if allowed is not None:
        work.allowed_input_ids = allowed
    return work.run(foreground_preempt=foreground_preempt)
