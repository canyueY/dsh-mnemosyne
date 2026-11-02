# -*- coding: utf-8 -*-
"""Staging + Approval + Checksum（E4.2）：未批准不得 Commit；支持 dedupe 与分层回滚。"""

from __future__ import annotations

import hashlib
import json
import uuid
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from cognitive_memory.migration.engine import MemoryMigrationEngine, MigrationConfig
from cognitive_memory.migration.interchange import InterchangeRecord, parse_interchange
from cognitive_memory.migration.models import SourceRecord
from cognitive_memory.migration.sink import RecordingSink


class StagingState(str, Enum):
    PREVIEW = "preview"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


# soft：forget(hard=False) 标 invalidated；hard：forget(hard=True) 清主表
ROLLBACK_LAYERS = ("soft", "hard")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_text(rec: dict[str, Any]) -> str:
    content = rec.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("body") or "").strip()
    if content is None:
        return ""
    return str(content).strip()


def _source_key(rec: dict[str, Any]) -> str:
    sys = str(rec.get("source_system") or "").strip().lower()
    rid = str(rec.get("source_record_id") or "").strip()
    return f"{sys}::{rid}"


def _content_key(rec: dict[str, Any]) -> str:
    text = _record_text(rec).lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def dedupe_records(
    records: Iterable[dict[str, Any]],
    *,
    strategy: str = "source_then_content",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """精细去重：默认先按 source_system+source_record_id，再按正文 hash。

    strategy:
      - source: 仅 source key
      - content: 仅正文 hash
      - source_then_content: 先 source，再 content（默认）
      - none: 不去重
    """
    items = [dict(r) for r in records]
    if strategy in ("", "none", "off", "false"):
        return items, {
            "strategy": "none",
            "input": len(items),
            "output": len(items),
            "dropped": 0,
            "dropped_by": {},
        }

    kept: list[dict[str, Any]] = []
    seen_source: set[str] = set()
    source_contents: dict[str, str] = {}
    seen_content: set[str] = set()
    dropped_by = {"source": 0, "content": 0}

    use_source = strategy in ("source", "source_then_content")
    use_content = strategy in ("content", "source_then_content")

    for rec in items:
        sk = _source_key(rec) if use_source else ""
        if use_source and sk.endswith("::"):
            sk = ""  # 无 id 时不按空 source 去重
        if use_source and sk and sk in seen_source:
            if strategy=="source" and source_contents[sk] != json.dumps(rec,sort_keys=True,ensure_ascii=False):
                raise ValueError("conflicting_source_revisions_require_review")
            dropped_by["source"] += 1
            continue
        ck = _content_key(rec) if use_content else ""
        if use_content and ck and ck in seen_content:
            dropped_by["content"] += 1
            continue
        if sk:
            seen_source.add(sk)
            source_contents[sk] = json.dumps(rec,sort_keys=True,ensure_ascii=False)
        if ck:
            seen_content.add(ck)
        kept.append(rec)

    report = {
        "strategy": strategy,
        "input": len(items),
        "output": len(kept),
        "dropped": len(items) - len(kept),
        "dropped_by": dropped_by,
    }
    return kept, report


@dataclass
class StagingBatch:
    batch_id: str
    state: str = StagingState.PREVIEW.value
    created_at: str = ""
    updated_at: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)
    checksum: str = ""
    approved_by: str | None = None
    approved_at: str | None = None
    approved_checksum: str | None = None
    commit_report: dict[str, Any] | None = None
    dedupe_report: dict[str, Any] | None = None
    rollback_report: dict[str, Any] | None = None
    reason: str = ""

    def recompute_checksum(self) -> str:
        blob = json.dumps(self.records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.checksum = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return self.checksum

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StagingBatch":
        return cls(
            batch_id=str(data.get("batch_id") or ""),
            state=str(data.get("state") or StagingState.PREVIEW.value),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            records=list(data.get("records") or []),
            checksum=str(data.get("checksum") or ""),
            approved_by=data.get("approved_by"),
            approved_at=data.get("approved_at"),
            approved_checksum=data.get("approved_checksum"),
            commit_report=data.get("commit_report"),
            dedupe_report=data.get("dedupe_report"),
            rollback_report=data.get("rollback_report"),
            reason=str(data.get("reason") or ""),
        )


class StagingStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, batch_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}",batch_id):
            raise ValueError("invalid_batch_id")
        return self.root / f"{batch_id}.json"

    def save(self, batch: StagingBatch) -> Path:
        batch.updated_at = _now()
        if not batch.created_at:
            batch.created_at = batch.updated_at
        batch.recompute_checksum()
        path = self._path(batch.batch_id)
        tmp=path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with tmp.open("w",encoding="utf-8") as handle:
            handle.write(json.dumps(batch.to_dict(),ensure_ascii=False,indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        return path

    def load(self, batch_id: str) -> StagingBatch | None:
        path = self._path(batch_id)
        if not path.is_file():
            return None
        return StagingBatch.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def preview_batch(
    records: Iterable[InterchangeRecord | SourceRecord | dict[str, Any]],
    *,
    store: StagingStore | None = None,
    reason: str = "",
    dedupe: bool | str = True,
) -> StagingBatch:
    items: list[dict[str, Any]] = []
    for r in records:
        if isinstance(r, InterchangeRecord):
            items.append(r.to_dict())
        elif isinstance(r, SourceRecord):
            from cognitive_memory.migration.interchange import source_to_interchange

            items.append(source_to_interchange(r).to_dict())
        elif isinstance(r, dict):
            items.append(parse_interchange(r).to_dict())
        else:
            raise TypeError(f"unsupported record type: {type(r)}")
    strategy = "source"
    if dedupe is False:
        strategy = "none"
    elif isinstance(dedupe, str) and dedupe.strip():
        strategy = dedupe.strip()
    items, dedupe_report = dedupe_records(items, strategy=strategy)
    batch = StagingBatch(
        batch_id=uuid.uuid4().hex[:12],
        state=StagingState.PENDING_APPROVAL.value,
        records=items,
        reason=reason,
        dedupe_report=dedupe_report,
    )
    batch.recompute_checksum()
    if store:
        store.save(batch)
    return batch


def approve_batch(
    batch: StagingBatch,
    *,
    actor: str = "operator",
    expected_checksum: str | None = None,
    store: StagingStore | None = None,
) -> StagingBatch:
    if batch.state in (
        StagingState.COMMITTED.value,
        StagingState.REJECTED.value,
        StagingState.ROLLED_BACK.value,
    ):
        raise ValueError(f"cannot approve from state={batch.state}")
    current = batch.recompute_checksum()
    if expected_checksum and expected_checksum != current:
        raise ValueError("checksum_mismatch")
    batch.state = StagingState.APPROVED.value
    batch.approved_by = actor
    batch.approved_at = _now()
    batch.approved_checksum = current
    if store:
        store.save(batch)
    return batch


def reject_batch(
    batch: StagingBatch,
    *,
    reason: str = "",
    store: StagingStore | None = None,
) -> StagingBatch:
    batch.state = StagingState.REJECTED.value
    batch.reason = reason or batch.reason
    if store:
        store.save(batch)
    return batch


def commit_batch(
    batch: StagingBatch,
    *,
    service: Any | None = None,
    dry_run: bool = False,
    store: StagingStore | None = None,
) -> StagingBatch:
    """未 APPROVED 禁止 Commit。"""
    if batch.state == StagingState.COMMITTED.value:
        return batch
    if batch.state != StagingState.APPROVED.value:
        raise PermissionError(
            f"commit denied: state={batch.state} (approval required)"
        )
    if not batch.approved_checksum or batch.recompute_checksum() != batch.approved_checksum:
        raise PermissionError("approved_payload_changed")
    sources = [parse_interchange(r).to_source_record() for r in batch.records]
    cfg = MigrationConfig(dry_run=dry_run, resume=False, producer=f"migrate.staging:{batch.batch_id}")
    if service is None or dry_run:
        engine = MemoryMigrationEngine(RecordingSink(), config=cfg)
    else:
        engine = MemoryMigrationEngine.from_service(service, config=cfg)
    report = engine.run(sources)
    previous = {row["memory_id"] for row in (batch.commit_report or {}).get("written", [])
                if row.get("created") is True}
    batch.commit_report = report.to_dict()
    for row in batch.commit_report["written"]:
        if row.get("memory_id") in previous:
            row["created"] = True
    batch.state = ("partial" if report.imported else "failed") if report.failed else StagingState.COMMITTED.value
    if store:
        store.save(batch)
    return batch


def rollback_batch(
    batch: StagingBatch,
    *,
    service: Any,
    layer: str = "soft",
    store: StagingStore | None = None,
    actor: str = "operator",
) -> StagingBatch:
    """对已 Commit 批次分层回滚。

    soft：记忆标 invalidated（可审计保留）
    hard：硬删除主表（仍保留版本审计语义由 service 决定）
    仅处理 commit_report.written 中的 memory_id；无 id 的条目记入 skipped。
    """
    layer_norm = str(layer or "soft").strip().lower()
    if layer_norm not in ROLLBACK_LAYERS:
        raise ValueError(f"invalid rollback layer={layer!r}; expected {ROLLBACK_LAYERS}")
    if batch.state not in (StagingState.COMMITTED.value, "partial"):
        raise PermissionError(
            f"rollback denied: state={batch.state} (committed required)"
        )
    if not hasattr(service, "forget_memory"):
        raise TypeError("service must provide forget_memory for rollback")

    written = list((batch.commit_report or {}).get("written") or [])
    items: list[dict[str, Any]] = []
    skipped = 0
    hard = layer_norm == "hard"
    for row in written:
        if row.get("created") is not True or row.get("dry_run"):
            skipped += 1
            items.append({"ok":True,"skipped":True,"reason":"not_created_by_batch"})
            continue
        mid = str((row or {}).get("memory_id") or "").strip()
        if not mid:
            skipped += 1
            items.append({"ok": False, "skipped": True, "reason": "no_memory_id"})
            continue
        try:
            out = service.forget_memory(mid, hard=hard)
            items.append(
                {
                    "ok": True,
                    "memory_id": mid,
                    "layer": layer_norm,
                    "result": out if isinstance(out, dict) else {"raw": str(out)},
                }
            )
        except Exception as exc:  # noqa: BLE001 — 逐条收集，不中断整批
            items.append(
                {
                    "ok": False,
                    "memory_id": mid,
                    "layer": layer_norm,
                    "error": str(exc),
                }
            )

    batch.state = StagingState.ROLLED_BACK.value if all(i.get("ok") or i.get("skipped") for i in items) else "partial"
    batch.rollback_report = {
        "layer": layer_norm,
        "actor": actor,
        "at": _now(),
        "attempted": len(written),
        "skipped": skipped,
        "ok_count": sum(1 for i in items if i.get("ok")),
        "items": items,
    }
    if store:
        store.save(batch)
    return batch
