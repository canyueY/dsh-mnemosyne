# -*- coding: utf-8 -*-
"""Compatibility Gateway（E4.6）：只读 / 单向；双向默认关闭。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class CompatMode(str, Enum):
    DISABLED = "disabled"
    READ_ONLY = "read_only"  # 旧库权威，Mnemosyne 只读窥视
    ONE_WAY = "one_way"  # 旧 → Mnemosyne 单向
    DUAL = "dual"  # 显式启用才允许


@dataclass
class CompatGateway:
    mode: CompatMode = CompatMode.READ_ONLY
    dual_enabled: bool = False  # 硬默认关
    service: Any = None
    legacy_reader: Any = None

    def add(self, content: str, *, bank_id="personal", metadata=None, idempotency_key=None):
        if not self.service:
            raise RuntimeError("service_not_bound")
        from cognitive_memory.domain.models import ObservationIn, SourceKind, MemoryProposeIn
        with self.service.db.transaction():
            obs=self.service.capture_observation(ObservationIn(
                content=content,source_kind=SourceKind.SYSTEM,producer="compat.mem0",
                metadata={"origin":"external_memory","original_metadata":metadata or {}},
                idempotency_key=f"compat:{bank_id}:obs:{idempotency_key}" if idempotency_key else None,
            ))
            mem=self.service.propose_memory(MemoryProposeIn(
                content=content,bank_id=bank_id,source_ids=[obs.id],metadata=metadata or {},
                idempotency_key=f"compat:{bank_id}:mem:{idempotency_key}" if idempotency_key else None,
            ))
        return {"id":mem.id,"memory":mem.content,"status":mem.status.value,"metadata":mem.metadata}

    def search(self, query: str, *, bank_id="personal", limit=10):
        from cognitive_memory.domain.models import SearchQuery
        rows=self.service.search(SearchQuery(query=query,banks=[bank_id],limit=limit))
        result=[{"id":m.id,"memory":m.content,"metadata":m.metadata,"status":m.status.value} for m in rows]
        if self.legacy_reader and self.allow_read_legacy():
            # Legacy content stays external; reading never promotes it to a personal fact.
            result.extend({"external":True,"source":"legacy","record":r}
                          for r in self.legacy_reader.search(query,bank_id=bank_id,limit=limit))
        return {"results":result,"compatibility":"mem0-record-subset.v1"}

    def get(self, memory_id: str):
        pack=self.service.get_memory(memory_id)
        mem=pack.get("memory",pack) if isinstance(pack,dict) else pack.model_dump(mode="json")
        return {"id":mem.get("id"),"memory":mem.get("content"),"metadata":mem.get("metadata",{})}

    def delete(self, memory_id: str, *, hard=False):
        if hard:
            raise NotImplementedError("hard_delete_requires_native_governance_api")
        self.service.forget_memory(memory_id,hard=False)
        return {"id":memory_id,"status":"invalidated","physical_erasure":False}

    def allow_read_legacy(self) -> bool:
        return self.mode in (
            CompatMode.READ_ONLY,
            CompatMode.ONE_WAY,
            CompatMode.DUAL,
        )

    def allow_write_mnemosyne_from_legacy(self) -> bool:
        return self.mode in (CompatMode.ONE_WAY, CompatMode.DUAL)

    def allow_write_legacy_from_mnemosyne(self) -> bool:
        return self.mode == CompatMode.DUAL and self.dual_enabled

    def set_mode(self, mode: str | CompatMode, *, allow_dual: bool = False) -> dict[str, Any]:
        m = mode if isinstance(mode, CompatMode) else CompatMode(str(mode).strip().lower())
        if m == CompatMode.DUAL and not allow_dual:
            raise PermissionError("dual mode requires allow_dual=true")
        self.mode = m
        if m == CompatMode.DUAL:
            self.dual_enabled = True
        elif m != CompatMode.DUAL:
            self.dual_enabled = False
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "dual_enabled": self.dual_enabled,
            "allow_read_legacy": self.allow_read_legacy(),
            "allow_write_mnemosyne_from_legacy": self.allow_write_mnemosyne_from_legacy(),
            "allow_write_legacy_from_mnemosyne": self.allow_write_legacy_from_mnemosyne(),
            "dual_default_forbidden": True,
        }


# 进程级默认网关（单测可重置）
GATEWAY = CompatGateway()
