# -*- coding: utf-8 -*-
"""Interchange Model v0（E4.1）：只描述外部数据；未知字段保留。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from cognitive_memory.migration.models import SourceRecord

INTERCHANGE_SCHEMA_VERSION = "1.0"

KNOWN_TOP_LEVEL = frozenset(
    {
        "source_system",
        "source_record_id",
        "record_type",
        "schema_version",
        "content",
        "provenance",
        "time",
        "extensions",
        "raw_payload",
        "tags",
        "checksum",
    }
)


@dataclass
class InterchangeRecord:
    source_system: str
    source_record_id: str
    record_type: str = "memory"
    schema_version: str = INTERCHANGE_SCHEMA_VERSION
    content: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    time: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    checksum: str = ""

    def compute_checksum(self) -> str:
        body = {
            "source_system": self.source_system,
            "source_record_id": self.source_record_id,
            "record_type": self.record_type,
            "content": self.content,
            "tags": self.tags,
        }
        blob = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_checksum(self) -> "InterchangeRecord":
        self.checksum = self.compute_checksum()
        return self

    def to_dict(self) -> dict[str, Any]:
        if not self.checksum:
            self.with_checksum()
        return asdict(self)

    def to_source_record(self) -> SourceRecord:
        text = ""
        if isinstance(self.content, dict):
            text = str(
                self.content.get("text")
                or self.content.get("content")
                or self.content.get("body")
                or ""
            )
        elif self.content:
            text = str(self.content)
        meta = {
            "interchange": {
                "source_system": self.source_system,
                "schema_version": self.schema_version,
                "record_type": self.record_type,
                "checksum": self.checksum or self.compute_checksum(),
                "extensions": self.extensions,
            },
            "provenance": self.provenance,
            **{k: v for k, v in (self.raw_payload or {}).items() if k not in ("content", "text")},
        }
        raw=self.raw_payload or {}
        containers=[raw,self.extensions,raw.get("metadata") or {},
                    self.extensions.get("metadata") or {},self.extensions.get("mem0_metadata") or {},
                    self.extensions.get("provider_metadata") or {}]
        policies=[c.get("policy",c) for c in containers if isinstance(c,dict)]
        policy={}
        for flag,default in (("allow_store",True),("allow_forward",False),("allow_train",False)):
            specified=[p[flag] for p in policies if flag in p]
            policy[flag]=all(v is True for v in specified) if specified else default
        limits=[p["retain_until"] for p in policies if p.get("retain_until")]
        if limits:
            from cognitive_memory.governance.policy import _parse_ts
            dates=[_parse_ts(t) for t in limits]
            if any(t is None for t in dates):
                policy["allow_store"]=False
            else:
                policy["retain_until"]=min(dates).isoformat()
        if any(c.get("deleted") is True or c.get("tombstone") is True for c in containers if isinstance(c,dict)):
            policy["allow_store"]=False
        meta["policy"]=policy
        bank=self.extensions.get("bank_id") or raw.get("bank_id") or "personal"
        from cognitive_memory.memory.banks import BANK_CATALOG
        if bank not in BANK_CATALOG:
            raise ValueError("source_bank_requires_explicit_mapping")
        meta["target_bank"]=bank
        occurred = None
        if isinstance(self.time, dict):
            occurred = self.time.get("occurred_at") or self.time.get("created_at")
        return SourceRecord(
            source_id=f"{self.source_system}:{self.source_record_id}",
            content=text,
            tags=list(self.tags or []),
            metadata=meta,
            occurred_at=str(occurred) if occurred else None,
        )


def parse_interchange(obj: dict[str, Any]) -> InterchangeRecord:
    """解析外部记录；未知顶层字段进入 extensions（保留）。"""
    if not isinstance(obj, dict):
        raise ValueError("interchange record must be object")
    unknown = {k: v for k, v in obj.items() if k not in KNOWN_TOP_LEVEL}
    extensions = dict(obj.get("extensions") or {})
    extensions.update(unknown)
    content = obj.get("content")
    if not isinstance(content, dict):
        content = {"text": str(content or obj.get("text") or "")}
    rec = InterchangeRecord(
        source_system=str(obj.get("source_system") or "unknown"),
        source_record_id=str(
            obj.get("source_record_id") or obj.get("id") or obj.get("source_id") or ""
        ),
        record_type=str(obj.get("record_type") or "memory"),
        schema_version=str(obj.get("schema_version") or INTERCHANGE_SCHEMA_VERSION),
        content=content,
        provenance=dict(obj.get("provenance") or {}),
        time=dict(obj.get("time") or {}),
        extensions=extensions,
        raw_payload=dict(obj),
        tags=[str(t) for t in (obj.get("tags") or [])],
        checksum=str(obj.get("checksum") or ""),
    )
    if not rec.source_record_id:
        rec.source_record_id = rec.compute_checksum()[:16]
    if not rec.checksum:
        rec.with_checksum()
    return rec


def source_to_interchange(record: SourceRecord, *, source_system: str = "jsonl") -> InterchangeRecord:
    return InterchangeRecord(
        source_system=source_system,
        source_record_id=record.source_id,
        content={"text": record.content},
        provenance={"mapper": "source_to_interchange"},
        time={"occurred_at": record.occurred_at} if record.occurred_at else {},
        extensions={},
        raw_payload={"metadata": record.metadata},
        tags=list(record.tags or []),
    ).with_checksum()
