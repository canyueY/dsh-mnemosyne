# -*- coding: utf-8 -*-
"""时空元数据 schema v1（挂在 metadata.spatiotemporal，不另建库）。

约定：
- 缺省字段保持 unknown / 省略，禁止臆造精确时间或位置。
- occurredAt ≠ recordedAt：前者是事件/状态时间，后者是系统记录时间。
- spatial.validUntil 已结束 → 历史位置，不得在 mode=current 中当成当前位置。
- expires_at（记忆列）或 temporal.validUntil 到期 → 降权，不自动删除。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1

TEMPORAL_KINDS = frozenset(
    {"event", "state", "habit", "preference", "plan", "relationship", "knowledge", "unknown"}
)
TEMPORAL_STATUS = frozenset(
    {"active", "inactive", "completed", "superseded", "uncertain", "unknown"}
)
TIME_PRECISION = frozenset(
    {"exact", "day", "week", "month", "approximate", "unknown"}
)
SPATIAL_KINDS = frozenset(
    {
        "geographic",
        "building",
        "room",
        "area",
        "object",
        "virtual_scene",
        "social_space",
        "unknown",
    }
)
SPATIAL_PRECISION = frozenset({"exact", "approximate", "semantic", "unknown"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(raw: Any) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clamp_str(v: Any, n: int = 120) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    return s[:n]


def _enum(v: Any, allowed: frozenset[str], default: str) -> str:
    s = str(v or "").strip().lower()
    return s if s in allowed else default


def _clip01(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, x))


def normalize_temporal(
    raw: dict[str, Any] | None,
    *,
    occurred_at: str | None = None,
    recorded_at: str | None = None,
    kind_hint: str | None = None,
) -> dict[str, Any]:
    src = dict(raw) if isinstance(raw, dict) else {}
    recorded = _clamp_str(src.get("recordedAt") or recorded_at) or _now_iso()
    occurred = _clamp_str(src.get("occurredAt") or occurred_at)
    out: dict[str, Any] = {
        "kind": _enum(src.get("kind") or kind_hint, TEMPORAL_KINDS, "unknown"),
        "recordedAt": recorded,
        "status": _enum(src.get("status"), TEMPORAL_STATUS, "unknown"),
        "timePrecision": _enum(src.get("timePrecision"), TIME_PRECISION, "unknown"),
    }
    if occurred:
        out["occurredAt"] = occurred
        if out["timePrecision"] == "unknown":
            out["timePrecision"] = "approximate"
    for key, alias in (
        ("validFrom", "validFrom"),
        ("validUntil", "validUntil"),
        ("lastMentionedAt", "lastMentionedAt"),
        ("lastEvidenceAt", "lastEvidenceAt"),
        ("lastConfirmedAt", "lastConfirmedAt"),
        ("reevaluationAt", "reevaluationAt"),
    ):
        val = _clamp_str(src.get(key) or src.get(alias))
        if val:
            out[key] = val
    conf = _clip01(src.get("confidence"))
    if conf is not None:
        out["confidence"] = conf
    return out


def normalize_spatial(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    # 没有任何可识别锚点 → 不写入，避免伪造成 unknown 地点实体
    has_anchor = any(
        str(raw.get(k) or "").strip()
        for k in ("spaceId", "name", "parentSpaceId", "kind")
    )
    if not has_anchor:
        return None
    out: dict[str, Any] = {
        "kind": _enum(raw.get("kind"), SPATIAL_KINDS, "unknown"),
        "precision": _enum(raw.get("precision"), SPATIAL_PRECISION, "unknown"),
    }
    for key in ("spaceId", "name", "parentSpaceId", "source", "observedAt", "validFrom", "validUntil"):
        val = _clamp_str(raw.get(key), 200)
        if val:
            out[key] = val
    conf = _clip01(raw.get("confidence"))
    if conf is not None:
        out["confidence"] = conf
    return out


def attach_spatiotemporal(
    metadata: dict[str, Any] | None,
    *,
    occurred_at: str | None = None,
    recorded_at: str | None = None,
    kind_hint: str | None = None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    """规范化并写回 metadata['spatiotemporal']；保留其它字段。"""
    meta = dict(metadata or {})
    existing = meta.get("spatiotemporal") if isinstance(meta.get("spatiotemporal"), dict) else {}
    # 兼容扁平别名
    flat_t = meta.get("temporal") if isinstance(meta.get("temporal"), dict) else {}
    flat_s = meta.get("spatial") if isinstance(meta.get("spatial"), dict) else {}
    merged_t = {**flat_t, **(existing.get("temporal") or {})}
    merged_s = {**(existing.get("spatial") or {}), **flat_s}

    hint = kind_hint
    if not hint and source_kind in ("dialog", "user_statement"):
        hint = "event"
    elif not hint and source_kind == "video_observation":
        hint = "event"

    temporal = normalize_temporal(
        merged_t,
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        kind_hint=hint,
    )
    spatial = normalize_spatial(merged_s)
    pack: dict[str, Any] = {
        "schemaVersion": int(existing.get("schemaVersion") or SCHEMA_VERSION),
        "temporal": temporal,
    }
    if spatial:
        pack["spatial"] = spatial
    meta["spatiotemporal"] = pack
    # 清理扁平副本，避免双份漂移（可选保留只读别名给旧读者）
    meta.pop("temporal", None)
    meta.pop("spatial", None)
    return meta


def get_spatiotemporal(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    st = metadata.get("spatiotemporal")
    return dict(st) if isinstance(st, dict) else {}


def is_temporally_expired(
    metadata: dict[str, Any] | None,
    *,
    expires_at: str | None = None,
    now: datetime | None = None,
) -> bool:
    """记忆列 expires_at 或 temporal.validUntil 已过 → 视为时间过期（仍可查历史）。"""
    now = now or datetime.now(timezone.utc)
    for cand in (expires_at, (get_spatiotemporal(metadata).get("temporal") or {}).get("validUntil")):
        dt = parse_ts(cand)
        if dt is not None and dt < now:
            return True
    status = str((get_spatiotemporal(metadata).get("temporal") or {}).get("status") or "")
    if status in ("inactive", "completed", "superseded"):
        return True
    return False


def is_historical_space(
    metadata: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    """空间锚点 validUntil 已过 → 历史位置，不是当前位置。"""
    now = now or datetime.now(timezone.utc)
    spatial = get_spatiotemporal(metadata).get("spatial") or {}
    dt = parse_ts(spatial.get("validUntil"))
    return bool(dt is not None and dt < now)


def spatiotemporal_score(
    metadata: dict[str, Any] | None,
    *,
    expires_at: str | None = None,
    mode: str = "current",
    now: datetime | None = None,
) -> float:
    """检索重排附加分：1.0 正常，越小越靠后。不删除候选。"""
    mode = (mode or "current").strip().lower()
    if mode == "any":
        return 1.0
    now = now or datetime.now(timezone.utc)
    score = 1.0
    expired = is_temporally_expired(metadata, expires_at=expires_at, now=now)
    hist_space = is_historical_space(metadata, now=now)

    if mode == "current":
        if expired:
            score *= 0.25
        if hist_space:
            score *= 0.2
        # 有明确当前空间锚点略加分
        spatial = get_spatiotemporal(metadata).get("spatial") or {}
        if spatial.get("spaceId") or spatial.get("name"):
            if not hist_space:
                score *= 1.05
    elif mode == "historical":
        # 历史查询：过期/历史空间反而相关
        if expired or hist_space:
            score *= 1.15
        else:
            # 仅有当前有效状态、无历史标记时略降（仍保留）
            score *= 0.85
    return max(0.05, min(1.5, score))
