# -*- coding: utf-8 -*-
"""DSH / 大肥鱼 Host Adapter（薄封装）— 仅经 HTTP/SDK 访问 Node。

场景 A 验收锚点：宿主负责编排；记忆权威在 Mnemosyne Node。
禁止：直连 SQLite / 导入 storage.db.Database / 直写 memory.db。
"""

from __future__ import annotations

from typing import Any

from cognitive_memory.sdk import MnemosyneClient

# 静态检查与契约测试共用的禁导名单
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlite3",
    "cognitive_memory.storage.db",
    "cognitive_memory.storage.migrations",
    "cognitive_memory.core.service",
)


class DshHttpAdapter:
    """把 DSH 风格调用映射到 MnemosyneClient（无本地库）。"""

    def __init__(self, client: MnemosyneClient):
        self._client = client

    @classmethod
    def connect(cls, base_url: str, token: str, *, session: Any = None) -> "DshHttpAdapter":
        return cls(MnemosyneClient(base_url, token, session=session))

    def health(self) -> dict[str, Any]:
        return self._client.health()

    def contract(self) -> dict[str, Any]:
        return self._client.contract()

    def observe_dialog(
        self,
        content: str,
        *,
        producer: str = "dsh-host",
        agent_id: str = "agent:dsh",
        source_kind: str = "dialog",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._client.observe(
            {
                "source_kind": source_kind,
                "content": content,
                "producer": producer,
                "agent_id": agent_id,
                "metadata": dict(metadata or {}),
            }
        )

    def search(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        return self._client.search(query, limit=limit)

    def select_answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        """显式答案选择；故障返回 unavailable 信封（不退回 Top1 事实）。"""
        try:
            raw = self._client.select_answer(payload)
        except Exception as exc:  # noqa: BLE001 — thin host maps transport errors
            status = getattr(exc, "status_code", None)
            return {
                "ok": False,
                "availability": "unavailable",
                "status": "unavailable",
                "selected": [],
                "alternatives": [],
                "evidence_refs": [],
                "reason_codes": ["node_unavailable"],
                "wrote_verified": False,
                "checks_complete": False,
                "error": {"http_status": status, "message": str(exc)},
                "boundary": {
                    "not_top1_fact": True,
                    "not_evidence_absence": True,
                    "message": "Node unavailable — not missing counterevidence; do not fall back to unlabeled Top1",
                },
            }
        status = str(raw.get("status") or "unknown")
        delivery = {
            "ok": True,
            "availability": "available",
            "status": status,
            "decision_id": raw.get("decision_id"),
            "selected": list(raw.get("selected") or []),
            "alternatives": list(raw.get("alternatives") or []),
            "evidence_refs": list(raw.get("evidence_refs") or []),
            "counterevidence_refs": list(raw.get("counterevidence_refs") or []),
            "applicability": raw.get("applicability") or {},
            "reason_codes": list(raw.get("reason_codes") or []),
            "checks_complete": bool(raw.get("checks_complete")),
            "wrote_verified": False,
            "budget_usage": raw.get("budget_usage") or {},
            "trace_id": raw.get("trace_id"),
            "raw": raw,
        }
        if status in {"partial", "unknown"}:
            delivery["boundary"] = {
                "not_top1_fact": True,
                "labeled": True,
                "message": "labeled non-definitive selection; search Top1 is not authoritative",
            }
        return delivery

    def propose_from_host(
        self,
        content: str,
        *,
        source_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._client.propose(
            {
                "type": "semantic",
                "content": content,
                "status": "candidate",
                "source_ids": list(source_ids or []),
                "metadata": dict(metadata or {"host": "dsh"}),
            }
        )


def assert_no_forbidden_imports(module_globals: dict[str, Any]) -> None:
    """运行时粗检：模块 globals 不应持有禁导对象名。"""
    for name in ("sqlite3", "Database", "MemoryService"):
        if name in module_globals and module_globals[name] is not None:
            # allow typing-only None placeholders
            obj = module_globals[name]
            mod = getattr(obj, "__module__", "") or ""
            if name == "sqlite3" or mod.startswith("cognitive_memory.storage") or mod.startswith(
                "cognitive_memory.core"
            ):
                raise RuntimeError(f"DSH adapter must not bind {name} from {mod}")
