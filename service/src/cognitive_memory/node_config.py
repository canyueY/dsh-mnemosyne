# -*- coding: utf-8 -*-
"""E1.3 Node 独立配置边界（不依赖 DSH）。

优先级（高 → 低）：
  CLI 显式参数 > 环境变量 > 配置文件 > 内置默认
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "node": {
        "id": "local-node",
        "name": "My Mnemosyne",
        "mode": "standalone",
    },
    "runtime": {
        "host": "127.0.0.1",
        "port": 8765,
        "log_level": "INFO",
    },
    "storage": {
        "provider": "sqlite",
        "data_dir": "",
        "path": "",
    },
    "security": {
        "authentication": "required",
        "remote_access": "disabled",
        "token_env": "DSH_MEMORY_TOKEN",
    },
    "components": {
        "profiles": ["core-default"],
    },
    "context_budget": {
        "model_window": 8192,
        "conservative_reserve": 256,
        "max_candidates": 32,
        "timeout_ms": 5000,
        "default_token_budget": 1200,
        "measurement_default": "estimated",
    },
    "call_budget": {
        "max_external_calls": 8,
        "max_retrieval_rounds": 4,
        "max_duration_ms": 30000,
    },
}


@dataclass
class NodeConfig:
    raw: dict[str, Any] = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG))
    source_path: str | None = None

    @property
    def node_id(self) -> str:
        return str((self.raw.get("node") or {}).get("id") or "local-node")

    @property
    def host(self) -> str:
        return str((self.raw.get("runtime") or {}).get("host") or "127.0.0.1")

    @property
    def port(self) -> int:
        try:
            return int((self.raw.get("runtime") or {}).get("port") or 8765)
        except (TypeError, ValueError):
            return 8765

    @property
    def data_dir(self) -> str:
        st = self.raw.get("storage") or {}
        return str(st.get("data_dir") or st.get("path") or "").strip()

    @property
    def token_env(self) -> str:
        return str((self.raw.get("security") or {}).get("token_env") or "DSH_MEMORY_TOKEN")

    @property
    def remote_access(self) -> str:
        return str((self.raw.get("security") or {}).get("remote_access") or "disabled")

    @property
    def context_budget(self) -> dict[str, Any]:
        raw = self.raw.get("context_budget")
        return dict(raw) if isinstance(raw, dict) else dict(DEFAULT_CONFIG["context_budget"])

    @property
    def call_budget(self) -> dict[str, Any]:
        raw = self.raw.get("call_budget")
        return dict(raw) if isinstance(raw, dict) else dict(DEFAULT_CONFIG["call_budget"])

    def to_public_dict(self) -> dict[str, Any]:
        """不包含密钥；仅结构与来源。"""
        return {
            "source_path": self.source_path,
            "config": deepcopy(self.raw),
        }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def _load_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "YAML config requires PyYAML; use .json or pip install pyyaml"
            ) from exc
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"config root must be object: {path}")
    return data


def validate_config(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    runtime = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else {}
    host = str(runtime.get("host") or "127.0.0.1")
    remote = str((raw.get("security") or {}).get("remote_access") or "disabled").lower()
    if remote in {"disabled", "false", "0", "off"}:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            errors.append(
                f"security.remote_access=disabled but runtime.host={host!r} "
                "(deployment must not widen by default)"
            )
    storage = raw.get("storage") if isinstance(raw.get("storage"), dict) else {}
    provider = str(storage.get("provider") or "sqlite").lower()
    if provider != "sqlite":
        errors.append(f"storage.provider={provider!r} not supported in Phase 1 (sqlite only)")
    mode = str((raw.get("node") or {}).get("mode") or "standalone").lower()
    if mode not in {"standalone", "dsh-profile", "custom"}:
        errors.append(f"node.mode={mode!r} invalid")
    try:
        port = int(runtime.get("port") or 8765)
        if port < 0 or port > 65535:
            errors.append(f"runtime.port={port} out of range")
    except (TypeError, ValueError):
        errors.append("runtime.port must be int")
    return errors


def apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(raw)
    out.setdefault("runtime", {})
    out.setdefault("storage", {})
    out.setdefault("security", {})
    if os.environ.get("DSH_MEMORY_HOST"):
        out["runtime"]["host"] = os.environ["DSH_MEMORY_HOST"].strip()
    if os.environ.get("DSH_MEMORY_PORT"):
        try:
            out["runtime"]["port"] = int(os.environ["DSH_MEMORY_PORT"])
        except ValueError:
            pass
    if os.environ.get("DSH_MEMORY_DATA_DIR"):
        out["storage"]["data_dir"] = os.environ["DSH_MEMORY_DATA_DIR"].strip()
    if os.environ.get("DSH_MEMORY_TOKEN_ENV"):
        out["security"]["token_env"] = os.environ["DSH_MEMORY_TOKEN_ENV"].strip()
    if os.environ.get("DSH_MEMORY_LOG_LEVEL"):
        out["runtime"]["log_level"] = os.environ["DSH_MEMORY_LOG_LEVEL"].strip()
    return out


def load_node_config(
    *,
    config_path: str | Path | None = None,
    data_dir_hint: str | None = None,
) -> NodeConfig:
    """加载配置；若未指定文件，尝试 data_dir/node.config.json。"""
    raw = deepcopy(DEFAULT_CONFIG)
    source: str | None = None
    path: Path | None = None
    if config_path:
        path = Path(config_path).expanduser().resolve()
    elif data_dir_hint:
        cand = Path(data_dir_hint).expanduser().resolve() / "node.config.json"
        if cand.is_file():
            path = cand
    if path and path.is_file():
        raw = _deep_merge(raw, _load_file(path))
        source = str(path)
    raw = apply_env_overrides(raw)
    # CLI data_dir_hint 覆盖 storage.data_dir（显式目录优先于文件内空值）
    if data_dir_hint and str(data_dir_hint).strip():
        raw.setdefault("storage", {})["data_dir"] = str(Path(data_dir_hint).expanduser().resolve())
    errors = validate_config(raw)
    if errors:
        raise ValueError("invalid node config:\n- " + "\n- ".join(errors))
    return NodeConfig(raw=raw, source_path=source)


def write_default_config(path: Path, *, data_dir: str = "") -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(DEFAULT_CONFIG)
    if data_dir:
        payload["storage"]["data_dir"] = str(Path(data_dir).expanduser().resolve())
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PyYAML required for yaml output") from exc
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_token(cfg: NodeConfig, cli_token: str = "") -> str:
    if cli_token.strip():
        return cli_token.strip()
    env_name = cfg.token_env or "DSH_MEMORY_TOKEN"
    return os.environ.get(env_name, "").strip()
