# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class UnauthorizedComponentError(PermissionError):
    def __init__(self, message: str, *, code: str = "component_unauthorized"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ComponentManifest:
    component_id: str
    name: str = ""
    version: str = "0.1.0"
    permissions: list[str] = field(default_factory=list)
    banks: list[str] = field(default_factory=list)
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "name": self.name,
            "version": self.version,
            "permissions": list(self.permissions),
            "banks": list(self.banks),
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }


class ComponentRegistry:
    """进程内注册表：register 仅登记；authorize 才授予权限。"""

    def __init__(self) -> None:
        self._registered: dict[str, ComponentManifest] = {}
        self._authorized: dict[str, set[str]] = {}
        self._authorized_banks: dict[str, set[str]] = {}

    def register(self, manifest: ComponentManifest) -> dict[str, Any]:
        self._registered[manifest.component_id] = manifest
        # 注册成功 ≠ 已授权
        return {
            "registered": True,
            "authorized": False,
            "component_id": manifest.component_id,
            "registered_at": _now(),
        }

    def authorize(
        self,
        component_id: str,
        *,
        permissions: list[str] | None = None,
        banks: list[str] | None = None,
    ) -> dict[str, Any]:
        if component_id not in self._registered:
            raise UnauthorizedComponentError(
                f"component not registered: {component_id}",
                code="not_registered",
            )
        m = self._registered[component_id]
        perms = set(permissions if permissions is not None else m.permissions)
        bks = set(banks if banks is not None else m.banks or ["personal"])
        self._authorized[component_id] = perms
        self._authorized_banks[component_id] = bks
        return {
            "authorized": True,
            "component_id": component_id,
            "permissions": sorted(perms),
            "banks": sorted(bks),
            "authorized_at": _now(),
        }

    def require(
        self,
        component_id: str,
        permission: str,
        *,
        bank: str | None = None,
    ) -> None:
        if component_id not in self._registered:
            raise UnauthorizedComponentError(
                f"component not registered: {component_id}",
                code="not_registered",
            )
        granted = self._authorized.get(component_id) or set()
        if permission not in granted:
            raise UnauthorizedComponentError(
                f"permission denied: {permission} for {component_id}",
                code="permission_denied",
            )
        if bank:
            banks = self._authorized_banks.get(component_id) or set()
            if bank not in banks:
                raise UnauthorizedComponentError(
                    f"bank access denied: {bank} for {component_id}",
                    code="bank_denied",
                )

    def is_authorized(self, component_id: str) -> bool:
        return bool(self._authorized.get(component_id))

    def snapshot(self) -> dict[str, Any]:
        return {
            "registered": [m.to_dict() for m in self._registered.values()],
            "authorized": {
                cid: {
                    "permissions": sorted(perms),
                    "banks": sorted(self._authorized_banks.get(cid) or []),
                }
                for cid, perms in self._authorized.items()
            },
        }


def ensure_component_tables(db: Any) -> None:
    """可选持久化表（迁移未强制；供后续安装器用）。"""
    with db.transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS component_registry (
              component_id TEXT PRIMARY KEY,
              manifest_json TEXT NOT NULL,
              authorized INTEGER NOT NULL DEFAULT 0,
              permissions_json TEXT NOT NULL DEFAULT '[]',
              banks_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )


def persist_register(db: Any, manifest: ComponentManifest) -> None:
    ensure_component_tables(db)
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO component_registry(
              component_id, manifest_json, authorized, permissions_json, banks_json, created_at, updated_at
            ) VALUES (?, ?, 0, '[]', '[]', ?, ?)
            ON CONFLICT(component_id) DO UPDATE SET
              manifest_json=excluded.manifest_json,
              updated_at=excluded.updated_at
            """,
            (
                manifest.component_id,
                json.dumps(manifest.to_dict(), ensure_ascii=False),
                now,
                now,
            ),
        )
