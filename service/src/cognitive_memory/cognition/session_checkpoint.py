# -*- coding: utf-8 -*-
"""会话账本 / ContextManifest / 任务 checkpoint（E06）。

宿主交付 ≠ 模型理解；助手口述完成 ≠ 执行验收成功。
外部效果未知 → reconcile_required，禁止盲重试。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database

EXEC_PHASES = (
    "prepared",
    "requested",
    "acknowledged",
    "effect_confirmed",
    "unknown",
    "reconcile_required",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def stable_message_key(
    *,
    session_id: str,
    role: str,
    content: str,
    message_id: str | None = None,
) -> str:
    if message_id:
        return f"{session_id}:{message_id}"
    digest = hashlib.sha256(
        f"{session_id}|{role}|{content}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{session_id}:h:{digest}"


class SessionLedger:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    # ---- message idempotency / delivery ledger ----

    def record_message(
        self,
        *,
        session_id: str,
        bank_id: str,
        role: str,
        content: str,
        message_id: str | None = None,
        producer: str = "host",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = stable_message_key(
            session_id=session_id, role=role, content=content, message_id=message_id
        )
        now = _now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM session_messages WHERE stable_key=?", (key,)
            ).fetchone()
            if row:
                return {
                    "ok": True,
                    "deduped": True,
                    "stable_key": key,
                    "message_id": row["id"],
                    "exposure_count": int(row["exposure_count"] or 1),
                    "note": "replay_does_not_increase_fact_support",
                }
            mid = _new_id("smsg")
            conn.execute(
                """
                INSERT INTO session_messages(
                  id, session_id, bank_id, stable_key, role, content,
                  producer, exposure_count, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    mid,
                    session_id,
                    bank_id,
                    key,
                    role,
                    content,
                    producer,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "ok": True,
            "deduped": False,
            "stable_key": key,
            "message_id": mid,
            "exposure_count": 1,
        }

    def record_delivery(
        self,
        *,
        session_id: str,
        bank_id: str,
        card_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """交付账本：宿主交付去重；交付≠理解。"""
        key = f"{session_id}:card:{card_id}"
        now = _now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM session_deliveries WHERE stable_key=?", (key,)
            ).fetchone()
            if row:
                return {
                    "ok": True,
                    "deduped": True,
                    "delivery_id": row["id"],
                    "note": "delivered_only_proves_host_delivery_not_understanding",
                }
            did = _new_id("sdel")
            conn.execute(
                """
                INSERT INTO session_deliveries(
                  id, session_id, bank_id, stable_key, card_id,
                  payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    did,
                    session_id,
                    bank_id,
                    key,
                    card_id,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "ok": True,
            "deduped": False,
            "delivery_id": did,
            "note": "delivered_only_proves_host_delivery_not_understanding",
        }

    # ---- checkpoint / context manifest ----

    def save_checkpoint(
        self,
        *,
        session_id: str,
        bank_id: str,
        task_id: str | None = None,
        goal: str = "",
        constraints: list[str] | None = None,
        verified_progress: list[dict[str, Any]] | None = None,
        pending_questions: list[str] | None = None,
        pending_external_operations: list[dict[str, Any]] | None = None,
        delivered_cards: list[str] | None = None,
        evidence_revisions: list[dict[str, Any]] | None = None,
        assistant_claims: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
        principal_banks: list[str] | None = None,
    ) -> dict[str, Any]:
        cid = checkpoint_id or _new_id("sckpt")
        now = _now()
        ops = []
        for op in pending_external_operations or []:
            if not isinstance(op, dict):
                continue
            phase = str(op.get("phase") or "prepared")
            if phase not in EXEC_PHASES:
                phase = "unknown"
            item = dict(op)
            item["phase"] = phase
            # 超时/不明不得推断成功
            if phase in {"unknown", "requested"} and op.get("timeout"):
                item["phase"] = "reconcile_required"
                item["blind_retry_forbidden"] = True
            ops.append(item)

        # 助手自报完成不得写入 verified_progress
        verified = []
        for item in verified_progress or []:
            if not isinstance(item, dict):
                continue
            if item.get("source") in {"assistant_self_report", "model_said_done"}:
                continue
            verified.append(item)

        manifest = {
            "session_id": session_id,
            "task_id": task_id,
            "bank_id": bank_id,
            "goal": goal,
            "constraints": list(constraints or []),
            "verified_progress": verified,
            "pending_questions": list(pending_questions or []),
            "pending_external_operations": ops,
            "delivered_cards": list(delivered_cards or []),
            "evidence_revisions": list(evidence_revisions or []),
            "assistant_claims": list(assistant_claims or []),
            "notes": {
                "host_delivery_ne_understanding": True,
                "assistant_said_done_ne_verified": True,
            },
            "metadata": dict(metadata or {}),
            "updated_at": now,
        }
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT id, session_id, bank_id, task_id FROM session_checkpoints WHERE id=?",
                (cid,),
            ).fetchone()
            if existing is not None:
                existing_bank = str(existing["bank_id"] or "personal")
                existing_session = str(existing["session_id"] or "")
                if principal_banks is not None and existing_bank not in set(principal_banks):
                    return {
                        "ok": False,
                        "detail": "bank_denied",
                        "code": "cross_bank_checkpoint_denied",
                        "hint": "cannot overwrite checkpoint owned by an unauthorized bank",
                        "existing_bank_id": existing_bank,
                        "requested_bank_id": bank_id,
                    }
                if existing_bank != str(bank_id):
                    return {
                        "ok": False,
                        "detail": "bank_mismatch",
                        "code": "cross_bank_checkpoint_denied",
                        "hint": "checkpoint bank_id is immutable; refuse cross-bank overwrite",
                        "existing_bank_id": existing_bank,
                        "requested_bank_id": bank_id,
                    }
                if existing_session and existing_session != str(session_id):
                    return {
                        "ok": False,
                        "detail": "session_mismatch",
                        "code": "session_isolation",
                        "hint": "checkpoint session_id is immutable; refuse cross-session overwrite",
                        "existing_session_id": existing_session,
                        "requested_session_id": session_id,
                    }
                conn.execute(
                    """
                    UPDATE session_checkpoints
                    SET task_id=?, manifest_json=?, updated_at=?
                    WHERE id=? AND bank_id=? AND session_id=?
                    """,
                    (
                        task_id,
                        json.dumps(manifest, ensure_ascii=False),
                        now,
                        cid,
                        existing_bank,
                        existing_session,
                    ),
                )
            else:
                if principal_banks is not None and str(bank_id) not in set(principal_banks):
                    return {
                        "ok": False,
                        "detail": "bank_denied",
                        "code": "session_isolation",
                        "hint": "cannot create checkpoint in unauthorized bank",
                    }
                conn.execute(
                    """
                    INSERT INTO session_checkpoints(
                      id, session_id, bank_id, task_id, manifest_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        session_id,
                        bank_id,
                        task_id,
                        json.dumps(manifest, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        return {"ok": True, "checkpoint_id": cid, "manifest": manifest}

    def get_checkpoint(
        self,
        checkpoint_id: str,
        *,
        principal_banks: list[str] | None = None,
        reauthorize: bool = True,
    ) -> dict[str, Any]:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT * FROM session_checkpoints WHERE id=?", (checkpoint_id,)
            ).fetchone()
        if not row:
            return {"ok": False, "detail": "not_found"}
        bank_id = str(row["bank_id"] or "personal")
        if reauthorize and principal_banks is not None:
            if bank_id not in set(principal_banks):
                return {
                    "ok": False,
                    "detail": "bank_denied",
                    "code": "session_isolation",
                    "note": "cannot read or inherit commitments across unauthorized banks/sessions",
                }
        try:
            manifest = json.loads(row["manifest_json"] or "{}")
        except json.JSONDecodeError:
            manifest = {}
        # 恢复时核对外部操作：unknown/超时 → reconcile_required
        ops = []
        for op in manifest.get("pending_external_operations") or []:
            if not isinstance(op, dict):
                continue
            item = dict(op)
            phase = str(item.get("phase") or "")
            if phase in {"unknown", "requested"} and (
                item.get("timeout") or item.get("effect_unknown")
            ):
                item["phase"] = "reconcile_required"
                item["blind_retry_forbidden"] = True
            if item.get("phase") == "reconcile_required":
                item["blind_retry_forbidden"] = True
            ops.append(item)
        manifest["pending_external_operations"] = ops
        return {
            "ok": True,
            "checkpoint_id": row["id"],
            "session_id": row["session_id"],
            "bank_id": bank_id,
            "task_id": row["task_id"],
            "manifest": manifest,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "recovery": {
                "reauthorized": bool(reauthorize),
                "evidence_revisions": list(manifest.get("evidence_revisions") or []),
                "assistant_self_report_not_verified": True,
            },
        }

    def _authority_source_revisions(
        self, evidence_revisions: list[Any], *, bank_id: str
    ) -> dict[str, str]:
        """主动查询权威来源当前修订（不只回显存盘字段）。"""
        current: dict[str, str] = {}
        for item in evidence_revisions or []:
            if not isinstance(item, dict):
                continue
            sid = str(
                item.get("source_id")
                or item.get("id")
                or item.get("evidence_id")
                or ""
            ).strip()
            if not sid:
                continue
            with self.db.exclusive() as conn:
                row = conn.execute(
                    "SELECT id, metadata_json, root_source_id FROM evidences WHERE id=? OR root_source_id=?",
                    (sid, sid),
                ).fetchone()
            if not row:
                continue
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            src = meta.get("source") if isinstance(meta.get("source"), dict) else {}
            source_ref = (
                meta.get("source_ref") if isinstance(meta.get("source_ref"), dict) else {}
            )
            # 只取权威侧修订，绝不回退到 checkpoint 存盘值（否则无法发现漂移）
            rev = str(
                src.get("revision")
                or source_ref.get("revision")
                or meta.get("revision")
                or "0"
            )
            current[sid] = rev
            current[str(row["id"])] = rev
            if row["root_source_id"]:
                current[str(row["root_source_id"])] = rev
        return current

    def resume_checkpoint(
        self,
        checkpoint_id: str,
        *,
        principal_banks: list[str] | None = None,
        confirm_blind_retry: bool = False,
        current_source_revisions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        pack = self.get_checkpoint(
            checkpoint_id, principal_banks=principal_banks, reauthorize=True
        )
        if not pack.get("ok"):
            return pack
        ops = pack["manifest"].get("pending_external_operations") or []
        blocked = [
            op
            for op in ops
            if isinstance(op, dict) and op.get("phase") == "reconcile_required"
        ]
        if blocked and not confirm_blind_retry:
            return {
                "ok": True,
                "resumed": False,
                "status": "reconcile_required",
                "checkpoint": pack,
                "blocked_operations": blocked,
                "reason_codes": ["reconcile_required", "blind_retry_forbidden"],
                "note": "external effect unknown; do not auto-retry; assistant said-done is not verification",
            }

        expected = list(pack["manifest"].get("evidence_revisions") or [])
        bank_id = str(pack.get("bank_id") or "personal")
        authority = dict(current_source_revisions or {})
        if expected and not authority:
            authority = self._authority_source_revisions(expected, bank_id=bank_id)
        revision_recheck = None
        if expected:
            from cognitive_memory.evidence.derivation import DerivationStore

            expected_norm = [
                {
                    "id": str(x.get("source_id") or x.get("id") or ""),
                    "revision": str(x.get("revision") or "0"),
                }
                for x in expected
                if isinstance(x, dict) and (x.get("source_id") or x.get("id"))
            ]
            revision_recheck = DerivationStore(self.db).recheck_revisions(
                expected_norm,
                current_revisions=authority,
            )
            # 权威侧明确给出不同修订 → 拒绝恢复；仅存盘字段不足以放行
            drifted = [
                e
                for e in expected_norm
                if str(authority.get(e["id"]) or "")
                and str(authority.get(e["id"])) != e["revision"]
            ]
            if drifted or not revision_recheck.get("chosen_allowed"):
                return {
                    "ok": True,
                    "resumed": False,
                    "status": "source_stale",
                    "checkpoint": pack,
                    "reason_codes": list(
                        dict.fromkeys(
                            list(revision_recheck.get("reason_codes") or [])
                            + (["source_revised_during_check"] if drifted else [])
                        )
                    )
                    or ["source_revised_during_check"],
                    "revision_recheck": {**revision_recheck, "drifted": drifted},
                    "authority_revisions": authority,
                    "note": "stored evidence_revisions diverge from authority; refuse blind resume",
                }

        constraints = list(pack["manifest"].get("constraints") or [])
        return {
            "ok": True,
            "resumed": True,
            "status": "ready",
            "checkpoint": pack,
            "constraints": constraints,
            "verified_progress": list(pack["manifest"].get("verified_progress") or []),
            "pending_questions": list(pack["manifest"].get("pending_questions") or []),
            "reason_codes": ["restored_with_reauth"],
            "revision_recheck": revision_recheck,
            "authority_revisions": authority,
        }
