# -*- coding: utf-8 -*-
"""记忆策略执法：字段 ≠ 默认可信（ENGINEERING_CONSTRAINTS 原则二）。

metadata.policy（或顶层兼容键）:
  allow_store / allow_forward / allow_train / retain_until

默认：store=True, forward=False, train=False。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PolicyDeniedError(PermissionError):
    def __init__(self, code: str, message: str, *, policy: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.policy = dict(policy or {})


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_policy(metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = dict(metadata or {})
    nested = meta.get("policy") if isinstance(meta.get("policy"), dict) else {}
    # 顶层兼容键可覆盖 nested
    out = {
        "allow_store": nested.get("allow_store", meta.get("allow_store", True)),
        "allow_forward": nested.get("allow_forward", meta.get("allow_forward", False)),
        "allow_train": nested.get("allow_train", meta.get("allow_train", False)),
        "retain_until": nested.get("retain_until", meta.get("retain_until")),
    }
    out["allow_store"] = bool(out["allow_store"])
    out["allow_forward"] = bool(out["allow_forward"])
    out["allow_train"] = bool(out["allow_train"])
    return out


def retention_expired(policy: dict[str, Any], *, now: datetime | None = None) -> bool:
    until = _parse_ts(policy.get("retain_until"))
    if until is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now >= until


def assert_allow_store(metadata: dict[str, Any] | None) -> dict[str, Any]:
    policy = extract_policy(metadata)
    if not policy["allow_store"]:
        raise PolicyDeniedError(
            "allow_store_denied",
            "metadata.policy.allow_store=false; refuse persist",
            policy=policy,
        )
    if retention_expired(policy):
        raise PolicyDeniedError(
            "retention_expired",
            "retain_until passed; refuse new store under expired policy",
            policy=policy,
        )
    return policy


def assert_allow_forward(metadata: dict[str, Any] | None) -> dict[str, Any]:
    policy = extract_policy(metadata)
    if retention_expired(policy):
        raise PolicyDeniedError(
            "retention_expired",
            "retain_until passed; refuse forward/export",
            policy=policy,
        )
    if not policy["allow_forward"]:
        raise PolicyDeniedError(
            "allow_forward_denied",
            "metadata.policy.allow_forward=false; refuse export/forward",
            policy=policy,
        )
    return policy


def assert_allow_train(metadata: dict[str, Any] | None) -> dict[str, Any]:
    policy = extract_policy(metadata)
    if retention_expired(policy):
        raise PolicyDeniedError(
            "retention_expired",
            "retain_until passed; refuse train",
            policy=policy,
        )
    if not policy["allow_train"]:
        raise PolicyDeniedError(
            "allow_train_denied",
            "metadata.policy.allow_train=false; refuse training use",
            policy=policy,
        )
    return policy


def is_forwardable(metadata: dict[str, Any] | None) -> bool:
    try:
        assert_allow_forward(metadata)
        return True
    except PolicyDeniedError:
        return False


def filter_forwardable_memories(items: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """返回 (可导出列表, 跳过说明)。items 需有 .metadata 或 dict metadata。"""
    kept: list[Any] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
            mid = str(item.get("id") or "")
        else:
            meta = getattr(item, "metadata", None) or {}
            mid = str(getattr(item, "id", "") or "")
        try:
            assert_allow_forward(meta if isinstance(meta, dict) else {})
            kept.append(item)
        except PolicyDeniedError as exc:
            skipped.append({"id": mid, "code": exc.code, "message": str(exc)})
    return kept, skipped
