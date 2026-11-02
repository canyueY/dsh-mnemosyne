# -*- coding: utf-8 -*-
"""装配 Database / EventBus / MemoryService / Dispatcher / FastAPI。"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognitive_memory.api.app import create_app
from cognitive_memory.core.service import MemoryService
from cognitive_memory.events.bus import EventBus
from cognitive_memory.jobs.dispatcher import BackgroundRuntime
from cognitive_memory.storage.db import Database
from cognitive_memory.storage.paths import ensure_layout


@dataclass
class AppContext:
    paths: dict[str, Path]
    db: Database
    bus: EventBus
    service: MemoryService
    runtime: BackgroundRuntime
    app: Any
    access_token: str
    host: str
    port: int


def _resolve_token() -> str:
    env_name = os.environ.get("DSH_MEMORY_TOKEN_ENV", "DSH_MEMORY_TOKEN")
    token = os.environ.get(env_name, "").strip()
    if token:
        return token
    # 开发态：生成一次性 token，写入 runtime（不进日志）
    return secrets.token_urlsafe(24)


def build_context(
    *,
    data_dir: str | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> AppContext:
    root = Path(data_dir).resolve() if data_dir else None
    paths = ensure_layout(root)
    db = Database(paths["db"])
    db.ensure_schema()
    bus = EventBus()
    # host 经 DSH_MEMORY_SPATIOTEMPORAL=0|1 控制；默认开启
    st_env = str(os.environ.get("DSH_MEMORY_SPATIOTEMPORAL", "1")).strip().lower()
    spatiotemporal_enable = st_env not in ("0", "false", "off", "no")
    # Mnemosyne：DSH_MEMORY_EMOTION=1 才开提取任务；默认关（shadow 也需显式开）
    emo_env = str(os.environ.get("DSH_MEMORY_EMOTION", "0")).strip().lower()
    emotion_enable = emo_env in ("1", "true", "yes", "on")
    emo_write = str(os.environ.get("DSH_MEMORY_EMOTION_WRITE", "0")).strip().lower()
    emotion_write_enabled = emo_write in ("1", "true", "yes", "on")
    emo_shadow = str(os.environ.get("DSH_MEMORY_EMOTION_SHADOW", "1")).strip().lower()
    # WRITE 打开时若未显式设 SHADOW=0，仍保持 shadow（安全默认）
    if emo_shadow in ("0", "false", "off", "no"):
        emotion_shadow_only = False
    else:
        emotion_shadow_only = True
    emo_retr = str(os.environ.get("DSH_MEMORY_EMOTION_RETRIEVAL", "0")).strip().lower()
    emotion_retrieval_enabled = emo_retr in ("1", "true", "yes", "on")
    service = MemoryService(
        db,
        bus,
        spatiotemporal_enable=spatiotemporal_enable,
        emotion_enable=emotion_enable,
        emotion_write_enabled=emotion_write_enabled,
        emotion_shadow_only=emotion_shadow_only,
        emotion_retrieval_enabled=emotion_retrieval_enabled,
    )
    # Capability Gate：默认 LOCKED 行写入权限表
    from cognitive_memory.governance.authority import ensure_authority_row
    from cognitive_memory.governance.capability_gate import ensure_default_capabilities
    from cognitive_memory.identity import ensure_default_principals
    from cognitive_memory.memory.banks import ensure_default_banks

    ensure_default_capabilities(db)
    ensure_default_principals(db)
    ensure_default_banks(db)
    ensure_authority_row(db)
    runtime = BackgroundRuntime(db, bus, service)
    access_token = token or _resolve_token()
    app = create_app(service, access_token=access_token)
    return AppContext(
        paths=paths,
        db=db,
        bus=bus,
        service=service,
        runtime=runtime,
        app=app,
        access_token=access_token,
        host=host,
        port=port,
    )


def write_service_json(ctx: AppContext, bound_port: int) -> Path:
    payload = {
        "instance_id": secrets.token_hex(8),
        "host": ctx.host,
        "port": bound_port,
        "api_prefix": "/api/v1",
        "api_version": "v1",
        "pid": os.getpid(),
        "token_env": "DSH_MEMORY_TOKEN",
    }
    path = ctx.paths["service_json"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # token 单独写受保护文件，不进 service.json 明文展示给随意读取者时可选
    token_path = ctx.paths["runtime"] / "token"
    token_path.write_text(ctx.access_token, encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    return path
