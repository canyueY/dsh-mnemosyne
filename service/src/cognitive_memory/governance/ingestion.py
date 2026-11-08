# -*- coding: utf-8 -*-
"""Mnemosyne 2.0 录入治理门禁。

本模块只决定入站内容能否进入 Observation 链路，以及是否需要人工复核；
它不会把 Observation 直接提升为 Claim/Knowledge，也不会覆盖既有 TMS。
所有规则和决策落在 Mnemosyne 服务自己的 SQLite 中，便于审计和回放。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class IngestionGovernanceError(PermissionError):
    """录入被隔离或拒绝；调用方不得绕过此异常直接写长期记忆。"""

    def __init__(self, decision: dict[str, Any]):
        self.decision = decision
        super().__init__(decision.get("reason") or "ingestion denied")


class IngestionSimulationIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    source_kind: str = "user_statement"
    producer: str = "dsh"
    profile_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: str | None = None


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prompt_injection", re.compile(r"(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|system|developer)\s+instructions?", re.I)),
    ("prompt_injection_cn", re.compile(r"(?:忽略|无视|覆盖|绕过).{0,12}(?:系统|开发者|之前|原有).{0,12}(?:指令|规则|提示词)", re.S)),
    ("persistent_injection", re.compile(r"(?:always|永久|以后都|从现在起).{0,30}(?:do|执行|记住|指令|规则)", re.I | re.S)),
    ("exfiltration", re.compile(r"(?:reveal|show|send|泄露|导出|显示).{0,30}(?:secret|token|password|系统提示|密钥|凭据)", re.I | re.S)),
    ("tool_execution", re.compile(r"(?:run|execute|调用|执行).{0,20}(?:tool|shell|powershell|cmd|工具|命令)", re.I | re.S)),
    ("hidden_markup", re.compile(r"<!--.*?(?:ignore|system|指令|提示词).*?-->|<script[^>]*>.*?</script>", re.I | re.S)),
)
_EMOTION = re.compile(r"(?:我|用户).{0,8}(?:很|太|非常|有点|感到|觉得).{0,12}(?:生气|愤怒|焦虑|难过|开心|沮丧|讨厌|喜欢)", re.S)
_FACT = re.compile(r"(?:我叫|我的名字是|我是|位于|在|项目|版本|使用)", re.S)
_PREFERENCE = re.compile(r"(?:我喜欢|我偏好|我讨厌|我不喜欢|偏好|喜欢)", re.S)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ensure_ingestion_tables(db: Any) -> None:
    """兼容旧数据库：治理表可独立创建，迁移脚本也会正式声明它们。"""
    with db.transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingestion_profiles (
              profile_id TEXT PRIMARY KEY,
              version INTEGER NOT NULL DEFAULT 1,
              name TEXT NOT NULL,
              mode TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              config_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ingestion_rules (
              rule_id TEXT PRIMARY KEY,
              profile_id TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1,
              priority INTEGER NOT NULL,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              config_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ingestion_decisions (
              decision_id TEXT PRIMARY KEY,
              source_kind TEXT NOT NULL,
              producer TEXT NOT NULL,
              profile_id TEXT NOT NULL,
              profile_version INTEGER NOT NULL,
              action TEXT NOT NULL,
              content_type TEXT NOT NULL,
              threat_level TEXT NOT NULL,
              threat_codes_json TEXT NOT NULL DEFAULT '[]',
              matched_rules_json TEXT NOT NULL DEFAULT '[]',
              security_json TEXT NOT NULL DEFAULT '{}',
              reason TEXT NOT NULL DEFAULT '',
              review_required INTEGER NOT NULL DEFAULT 0,
              observation_id TEXT,
              result_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ingestion_decisions_created
              ON ingestion_decisions(created_at);
            CREATE TABLE IF NOT EXISTS ingestion_quarantine (
              quarantine_id TEXT PRIMARY KEY,
              decision_id TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              reviewed_by TEXT,
              review_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ingestion_profile_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              profile_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              snapshot_json TEXT NOT NULL,
              changed_by TEXT NOT NULL,
              change_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(profile_id, version)
            );
            """
        )
        now = _now()
        defaults = (
            ("general", "通用记忆", "tiered", {"emotion": "review", "agent_inference": "candidate"}),
            ("agent_development", "Agent 开发", "strict", {"emotion": "deny", "agent_inference": "candidate"}),
            ("roleplay", "角色扮演", "tiered", {"emotion": "candidate", "agent_inference": "candidate"}),
        )
        for pid, name, mode, config in defaults:
            conn.execute(
                """INSERT OR IGNORE INTO ingestion_profiles
                (profile_id,version,name,mode,enabled,config_json,created_at,updated_at)
                VALUES (?,?,?,?,1,?,?,?)""",
                (pid, 1, name, mode, _json(config), now, now),
            )


def _row_to_profile(row: Any) -> dict[str, Any]:
    try:
        config = json.loads(row["config_json"] or "{}")
    except json.JSONDecodeError:
        config = {}
    return {
        "profile_id": row["profile_id"],
        "version": int(row["version"] or 1),
        "name": row["name"] if "name" in row.keys() else row["profile_id"],
        "mode": row["mode"],
        "enabled": bool(row["enabled"]) if "enabled" in row.keys() else True,
        "config": config if isinstance(config, dict) else {},
        "updated_at": row["updated_at"] if "updated_at" in row.keys() else None,
    }


def _load_profile(db: Any, profile_id: str, *, enabled_only: bool = False) -> dict[str, Any] | None:
    ensure_ingestion_tables(db)
    pid = (profile_id or "").strip()
    if not pid:
        return None
    sql = "SELECT * FROM ingestion_profiles WHERE profile_id=?"
    params: tuple[Any, ...] = (pid,)
    if enabled_only:
        sql += " AND enabled=1"
    with db.exclusive() as conn:
        row = conn.execute(sql, params).fetchone()
    return _row_to_profile(row) if row else None


def _profile(db: Any, profile_id: str | None) -> dict[str, Any]:
    wanted = (profile_id or "general").strip() or "general"
    row = _load_profile(db, wanted, enabled_only=True) or _load_profile(db, "general", enabled_only=True)
    if not row:
        raise RuntimeError("ingestion_profiles_not_initialized")
    return row


def resolve_effective_profile(
    db: Any,
    *,
    profile_id: str | None = None,
    bank_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Global → Bank/Domain → Task. Later layer overrides; conflicts are retained."""
    ensure_ingestion_tables(db)
    chain: list[dict[str, Any]] = []
    global_p = _load_profile(db, "general")
    if global_p:
        chain.append({**global_p, "layer": "global", "scope": "global"})
    wanted = (profile_id or "").strip()
    bank = (bank_id or "").strip()
    domain_id = wanted if wanted and wanted != "general" else (f"bank_{bank}" if bank else "")
    if domain_id:
        domain_p = _load_profile(db, domain_id)
        if domain_p:
            chain.append({**domain_p, "layer": "bank_or_domain", "scope": domain_id})
        elif wanted and wanted != "general":
            chain.append(
                {
                    "layer": "bank_or_domain",
                    "profile_id": wanted,
                    "version": None,
                    "enabled": False,
                    "config": {},
                    "scope": wanted,
                    "missing": True,
                }
            )
    task = (task_id or "").strip()
    if task:
        task_p = _load_profile(db, f"task_{task}") or _load_profile(db, task)
        if task_p:
            chain.append({**task_p, "layer": "task", "scope": task})
    effective: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    for layer in chain:
        if layer.get("missing") or layer.get("enabled") is False:
            continue
        for key, value in (layer.get("config") or {}).items():
            prev = effective.get(key)
            if prev is not None and prev != value:
                conflicts.append(
                    {
                        "key": key,
                        "from_layer": (sources.get(key) or {}).get("layer"),
                        "from_value": prev,
                        "to_layer": layer.get("layer"),
                        "to_value": value,
                        "winner": layer.get("layer"),
                    }
                )
            effective[key] = value
            sources[key] = {
                "layer": layer.get("layer"),
                "profile_id": layer.get("profile_id"),
                "version": layer.get("version"),
            }
    return {
        "ok": True,
        "effective": effective,
        "sources": sources,
        "chain": [
            {
                "layer": c.get("layer"),
                "profile_id": c.get("profile_id"),
                "version": c.get("version"),
                "enabled": c.get("enabled"),
                "scope": c.get("scope"),
                "missing": bool(c.get("missing")),
                "config": c.get("config") or {},
            }
            for c in chain
        ],
        "conflicts": conflicts,
        "scope": {
            "profile_id": wanted or "general",
            "bank_id": bank or None,
            "task_id": task or None,
        },
        "save_is_not_activate": True,
        "computed_on": "server",
    }


def _threat_scan(content: str, *, history: list[str] | None = None, modality: str = "text") -> dict[str, Any]:
    history_text = "\n".join(str(x) for x in (history or [])[-12:])
    scan_text = f"{history_text}\n{content}" if history_text else content
    codes = [name for name, pattern in _INJECTION_PATTERNS if pattern.search(scan_text)]
    hidden = sum(1 for ch in scan_text if ord(ch) in {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})
    if hidden:
        codes.append("hidden_unicode")
    # Only classify a plausible base64 token; never decode or execute its contents.
    for token in re.findall(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/])", scan_text):
        try:
            raw = base64.b64decode(token, validate=True)
        except (binascii.Error, ValueError):
            continue
        if any(marker in raw.lower() for marker in (b"ignore ", "忽略".encode(), b"system prompt", b"powershell")):
            codes.append("encoded_instruction")
            break
    if history_text and any(name in codes for name in ("prompt_injection", "prompt_injection_cn", "persistent_injection", "exfiltration", "tool_execution")):
        codes.append("multi_turn_injection")
    if str(modality).lower() in {"ocr", "asr", "audio", "image"} and codes:
        codes.append(f"{str(modality).lower()}_injection")
    level = "high" if any(c in codes for c in ("exfiltration", "tool_execution", "encoded_instruction")) else ("medium" if codes else "none")
    return {"level": level, "codes": sorted(set(codes)), "hidden_unicode_count": hidden, "modality": str(modality or "text")}


def _source_trust(source_kind: str, metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("source_trust") or "").strip().lower()
    if explicit in {"trusted", "verified", "untrusted", "unknown"}:
        return explicit
    return {"user_statement": "trusted", "dialog": "trusted", "feedback": "trusted", "system": "verified", "agent_inference": "untrusted", "video_observation": "unknown"}.get(str(source_kind), "unknown")


def _content_type(content: str, source_kind: str) -> str:
    sk = (source_kind or "").strip().lower()
    if sk in {"agent_inference", "system"}:
        return "agent_inference"
    if _EMOTION.search(content):
        return "emotion"
    if _PREFERENCE.search(content):
        return "preference"
    if _FACT.search(content):
        return "fact"
    if sk in {"video_observation", "dialog", "feedback"}:
        return "observation"
    return "temporary"


def evaluate_ingestion(db: Any, *, content: str, source_kind: str, producer: str,
                       profile_id: str | None = None, metadata: dict[str, Any] | None = None,
                       mode: str | None = None) -> dict[str, Any]:
    profile = _profile(db, profile_id)
    meta = dict(metadata or {})
    resolved = resolve_effective_profile(
        db,
        profile_id=profile_id,
        bank_id=str(meta.get("bank_id") or "") or None,
        task_id=str(meta.get("task_id") or "") or None,
    )
    history = meta.get("conversation_history") if isinstance(meta.get("conversation_history"), list) else []
    threat = _threat_scan(content, history=history, modality=str(meta.get("modality") or meta.get("source_modality") or "text"))
    threat["source_trust"] = _source_trust(source_kind, meta)
    ctype = _content_type(content, source_kind)
    config = resolved.get("effective") or profile.get("config") or {}
    action = config.get(ctype, "allow")
    matched = [f"profile:{profile['profile_id']}:v{profile['version']}", "security:threat-scan:v1", f"content:{ctype}:v1", "inherit:global_bank_task"]
    reason = "matched profile and content policy"
    review = False
    if threat["level"] == "high":
        action, review, reason = "quarantine", True, "high-risk instruction or exfiltration pattern"
    elif threat["level"] == "medium":
        action, review, reason = "review", True, "suspicious input requires user review"
    elif action == "deny":
        reason = "profile denies this content type"
    elif action == "review":
        review = True
        reason = "profile requires user review"
    elif action not in {"allow", "candidate", "temporary", "deny", "review"}:
        action, review, reason = "review", True, "unknown policy action defaults to review"
    return {
        "decision_id": _new_id("igd"),
        "source_kind": source_kind,
        "producer": producer,
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "action": action,
        "content_type": ctype,
        "threat_level": threat["level"],
        "threat_codes": threat["codes"],
        "security": threat,
        "matched_rules": matched,
        "review_required": review,
        "reason": reason,
        "mode": mode or profile.get("mode"),
        "metadata": dict(metadata or {}),
        "inheritance": {
            "chain": resolved.get("chain") or [],
            "conflicts": resolved.get("conflicts") or [],
            "sources": resolved.get("sources") or {},
        },
    }


def authorize_ingestion(db: Any, *, content: str, source_kind: str, producer: str,
                        profile_id: str | None = None, metadata: dict[str, Any] | None = None,
                        mode: str | None = None) -> dict[str, Any]:
    decision = evaluate_ingestion(db, content=content, source_kind=source_kind, producer=producer,
                                  profile_id=profile_id, metadata=metadata, mode=mode)
    now = _now()
    ensure_ingestion_tables(db)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO ingestion_decisions
            (decision_id,source_kind,producer,profile_id,profile_version,action,content_type,threat_level,
             threat_codes_json,matched_rules_json,security_json,reason,review_required,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision["decision_id"], decision["source_kind"], decision["producer"], decision["profile_id"],
             decision["profile_version"], decision["action"], decision["content_type"], decision["threat_level"],
             _json(decision["threat_codes"]), _json(decision["matched_rules"]), _json(decision["security"]),
             decision["reason"], 1 if decision["review_required"] else 0, now),
        )
        if decision["action"] == "quarantine":
            conn.execute(
                "INSERT INTO ingestion_quarantine(quarantine_id,decision_id,payload_json,created_at) VALUES(?,?,?,?)",
                (_new_id("qtn"), decision["decision_id"], _json({"content": content, "source_kind": source_kind, "producer": producer, "metadata": metadata or {}}), now),
            )
    if decision["action"] == "quarantine":
        raise IngestionGovernanceError(decision)
    if decision["action"] == "deny":
        raise IngestionGovernanceError(decision)
    return decision


def attach_result(db: Any, decision_id: str, *, observation_id: str | None = None, result: dict[str, Any] | None = None) -> None:
    ensure_ingestion_tables(db)
    with db.transaction() as conn:
        conn.execute("UPDATE ingestion_decisions SET observation_id=?, result_json=? WHERE decision_id=?",
                     (observation_id, _json(result or {}), decision_id))


def list_profiles(db: Any) -> list[dict[str, Any]]:
    ensure_ingestion_tables(db)
    with db.exclusive() as conn:
        rows = conn.execute("SELECT * FROM ingestion_profiles ORDER BY profile_id").fetchall()
    out = []
    for row in rows:
        try: config = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError: config = {}
        out.append({"profile_id": row["profile_id"], "version": row["version"], "name": row["name"], "mode": row["mode"], "enabled": bool(row["enabled"]), "config": config, "updated_at": row["updated_at"]})
    return out


def upsert_profile(db: Any, *, profile_id: str, name: str, mode: str, config: dict[str, Any], actor: str, note: str = "") -> dict[str, Any]:
    """用户/节点管理员修改策略；每次修改产生不可变版本快照。"""
    ensure_ingestion_tables(db)
    pid = str(profile_id or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", pid):
        raise ValueError("invalid_profile_id")
    if not isinstance(config, dict):
        raise ValueError("profile_config_must_be_object")
    allowed_actions = {"allow", "candidate", "temporary", "review", "deny"}
    if any(str(v) not in allowed_actions for v in config.values()):
        raise ValueError("profile_config_has_invalid_action")
    now = _now()
    with db.transaction() as conn:
        row = conn.execute("SELECT version FROM ingestion_profiles WHERE profile_id=?", (pid,)).fetchone()
        version = int(row["version"] or 0) + 1 if row else 1
        conn.execute(
            """INSERT INTO ingestion_profiles(profile_id,version,name,mode,enabled,config_json,created_at,updated_at)
            VALUES(?,?,?,?,1,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET version=excluded.version,name=excluded.name,mode=excluded.mode,
            enabled=1,config_json=excluded.config_json,updated_at=excluded.updated_at""",
            (pid, version, str(name or pid)[:120], str(mode or "tiered")[:32], _json(config), now, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO ingestion_profile_versions(profile_id,version,snapshot_json,changed_by,change_note,created_at) VALUES(?,?,?,?,?,?)",
            (pid, version, _json({"profile_id": pid, "version": version, "name": str(name or pid), "mode": str(mode or "tiered"), "config": config}), actor, note, now),
        )
    return next((p for p in list_profiles(db) if p["profile_id"] == pid), {"profile_id": pid, "version": version})


def list_profile_versions(db: Any, profile_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    ensure_ingestion_tables(db)
    with db.exclusive() as conn:
        rows = conn.execute("SELECT * FROM ingestion_profile_versions WHERE profile_id=? ORDER BY version DESC LIMIT ?", (profile_id, max(1, min(int(limit), 200)))).fetchall()
    out=[]
    for row in rows:
        try: snapshot=json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError: snapshot={}
        out.append({"profile_id": row["profile_id"], "version": row["version"], "snapshot": snapshot, "changed_by": row["changed_by"], "change_note": row["change_note"], "created_at": row["created_at"]})
    return out


def list_decisions(db: Any, *, limit: int = 100, quarantine_only: bool = False) -> list[dict[str, Any]]:
    ensure_ingestion_tables(db)
    table = "ingestion_quarantine q JOIN ingestion_decisions d ON d.decision_id=q.decision_id" if quarantine_only else "ingestion_decisions d"
    where = " WHERE q.status='pending'" if quarantine_only else ""
    with db.exclusive() as conn:
        select = "d.*, q.quarantine_id, q.status AS quarantine_status, q.reviewed_by, q.review_note" if quarantine_only else "d.*"
        rows = conn.execute(f"SELECT {select} FROM {table}{where} ORDER BY d.created_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        for key in ("threat_codes_json", "matched_rules_json", "security_json", "result_json"):
            target = key[:-5] if key.endswith("_json") else key
            try: item[target] = json.loads(item.pop(key) or "{}")
            except json.JSONDecodeError: item[target] = [] if key.endswith("codes_json") or key.endswith("rules_json") else {}
        item["review_required"] = bool(item["review_required"])
        out.append(item)
    return out


def review_quarantine(db: Any, quarantine_id: str, *, decision: str, actor: str, note: str = "") -> dict[str, Any]:
    """记录人工复核结果；批准只解除隔离状态，不直接写 Observation。"""
    ensure_ingestion_tables(db)
    normalized = str(decision or "").strip().lower()
    if normalized not in {"approve", "reject"}:
        raise ValueError("review_decision_must_be_approve_or_reject")
    now = _now()
    status = "approved" if normalized == "approve" else "rejected"
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM ingestion_quarantine WHERE quarantine_id=?", (quarantine_id,)).fetchone()
        if not row:
            raise KeyError("quarantine_not_found")
        if row["status"] != "pending":
            return {"quarantine_id": quarantine_id, "status": row["status"], "idempotent": True}
        conn.execute(
            "UPDATE ingestion_quarantine SET status=?, reviewed_by=?, review_note=?, reviewed_at=? WHERE quarantine_id=?",
            (status, actor, note, now, quarantine_id),
        )
        conn.execute(
            "UPDATE ingestion_decisions SET result_json=? WHERE decision_id=?",
            (_json({"review": status, "reviewed_by": actor, "review_note": note}), row["decision_id"]),
        )
    return {"quarantine_id": quarantine_id, "decision_id": row["decision_id"], "status": status, "reviewed_by": actor, "review_note": note}


def filter_retrieval_items(items: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """检索阶段的二次安全过滤。

    旧数据没有治理标记时保持兼容；只有明确标记为可疑内容的对象才会被
    排除。这样不会把“来源可信”误当作“内容拥有指令权限”。
    """
    kept: list[Any] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        meta = item.get("metadata", {}) if isinstance(item, dict) else getattr(item, "metadata", {})
        gov = meta.get("ingestion_governance") if isinstance(meta, dict) else None
        level = str((gov or {}).get("threat_level") or "none").lower()
        if level in {"medium", "high", "critical"}:
            skipped.append({"id": str(item.get("id") if isinstance(item, dict) else getattr(item, "id", "")), "reason": "ingestion_security_filter", "threat_level": level})
            continue
        kept.append(item)
    return kept, skipped
