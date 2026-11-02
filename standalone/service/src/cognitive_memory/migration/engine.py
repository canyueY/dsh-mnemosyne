# -*- coding: utf-8 -*-
"""记忆迁移引擎：导源 → 映射 → 校验 → 断点续跑 → 写入 → 报告。"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cognitive_memory.migration.checkpoint import CheckpointStore
from cognitive_memory.migration.mapping import map_source_record
from cognitive_memory.migration.models import MappedRecord, SourceRecord, WriteResult
from cognitive_memory.migration.sink import InProcessSink, MigrationSink


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MigrationConfig:
    dry_run: bool = False
    resume: bool = True
    limit: int | None = None
    checkpoint_path: Path | None = None
    producer: str = "migrate.amem"
    min_content_len: int = 2
    stop_on_error: bool = False
    run_id: str = ""


@dataclass
class MigrationReport:
    run_id: str
    started_at: str
    finished_at: str = ""
    dry_run: bool = False
    total: int = 0
    imported: int = 0
    skipped: int = 0
    resumed_skipped: int = 0
    failed: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    written: list[dict[str, Any]] = field(default_factory=list)
    checkpoint_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryMigrationEngine:
    """A-MEM（或 JSONL）→ Mnemosyne / cognitive-memory 迁移编排器。"""

    def __init__(
        self,
        sink: MigrationSink,
        *,
        config: MigrationConfig | None = None,
    ) -> None:
        self.sink = sink
        self.config = config or MigrationConfig()

    @classmethod
    def from_service(
        cls,
        service: Any,
        *,
        config: MigrationConfig | None = None,
    ) -> MemoryMigrationEngine:
        cfg = config or MigrationConfig()
        return cls(InProcessSink(service, producer=cfg.producer), config=cfg)

    def run(self, records: Iterable[SourceRecord]) -> MigrationReport:
        cfg = self.config
        run_id = cfg.run_id or uuid.uuid4().hex[:12]
        report = MigrationReport(run_id=run_id, started_at=_now(), dry_run=cfg.dry_run)

        checkpoint: CheckpointStore | None = None
        if cfg.checkpoint_path:
            checkpoint = CheckpointStore.load(cfg.checkpoint_path)
            if not checkpoint.run_id:
                checkpoint.run_id = run_id
            checkpoint.meta.update(
                {
                    "producer": cfg.producer,
                    "dry_run": cfg.dry_run,
                }
            )
            report.checkpoint_path = str(cfg.checkpoint_path)

        processed = 0
        for record in records:
            if cfg.limit is not None and processed >= cfg.limit:
                break
            processed += 1
            report.total += 1

            mapped = map_source_record(
                record,
                producer=cfg.producer,
                min_content_len=cfg.min_content_len,
            )
            if checkpoint and cfg.resume and checkpoint.is_done(mapped.source_id):
                report.resumed_skipped += 1
                continue

            result = self._write_one(mapped)
            self._accumulate(report, result, checkpoint)

            if checkpoint and cfg.checkpoint_path and report.total % 25 == 0:
                checkpoint.save()

            if (not result.ok) and cfg.stop_on_error:
                break

        if checkpoint and cfg.checkpoint_path:
            checkpoint.meta["last_report"] = {
                "imported": report.imported,
                "skipped": report.skipped,
                "failed": report.failed,
                "resumed_skipped": report.resumed_skipped,
            }
            checkpoint.save()

        report.finished_at = _now()
        return report

    def _write_one(self, mapped: MappedRecord) -> WriteResult:
        return self.sink.write(mapped, dry_run=self.config.dry_run)

    def _accumulate(
        self,
        report: MigrationReport,
        result: WriteResult,
        checkpoint: CheckpointStore | None,
    ) -> None:
        if result.skipped:
            report.skipped += 1
            if checkpoint and not self.config.dry_run:
                checkpoint.mark_done(result.source_id, skipped=True)
            return
        if result.ok:
            report.imported += 1
            report.written.append(
                {
                    "source_id": result.source_id,
                    "observation_id": result.observation_id,
                    "memory_id": result.memory_id,
                    "dry_run": result.dry_run,
                    "created": result.created,
                }
            )
            if checkpoint and not self.config.dry_run:
                checkpoint.mark_done(
                    result.source_id,
                    observation_id=result.observation_id,
                    memory_id=result.memory_id,
                )
            return
        report.failed += 1
        report.errors.append({"source_id": result.source_id, "error": result.error or "unknown"})
        if checkpoint and not self.config.dry_run:
            checkpoint.mark_failed(result.source_id, result.error or "unknown")
