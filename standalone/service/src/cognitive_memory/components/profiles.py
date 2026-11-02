# -*- coding: utf-8 -*-
"""Composition Profiles — 模板起点，不锁定后续选择。"""

from __future__ import annotations

from typing import Any

PROFILES: dict[str, dict[str, Any]] = {
    "standalone": {
        "id": "standalone",
        "name": "Standalone Node",
        "description": "独立 Node：无 DSH 依赖；仅本地组件源",
        "sources": ["local"],
        "default_components": [],
        "notes": "模板不等于已安装组件",
    },
    "dsh": {
        "id": "dsh",
        "name": "DSH Profile",
        "description": "宿主为 DSH/桌宠；DSH 插件 ≠ Mnemosyne Component",
        "sources": ["local"],
        "default_components": [],
        "notes": "Adapter 须单独安装并授权；禁止因装了 DSH 插件跳过 Node 授权",
    },
    "personal-agent": {
        "id": "personal-agent",
        "name": "Personal Agent Profile",
        "description": "个人 Agent 接入同一 Node",
        "sources": ["local"],
        "default_components": [],
        "notes": "多 Agent 身份/权限/审计仍须显式配置",
    },
    "custom": {
        "id": "custom",
        "name": "Custom",
        "description": "空模板，由用户自选组件",
        "sources": ["local"],
        "default_components": [],
        "notes": "",
    },
}


def list_profiles() -> list[dict[str, Any]]:
    return [dict(v) for v in PROFILES.values()]


def get_profile(profile_id: str) -> dict[str, Any] | None:
    p = PROFILES.get(str(profile_id or "").strip().lower())
    return dict(p) if p else None
