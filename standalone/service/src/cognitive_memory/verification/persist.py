# -*- coding: utf-8 -*-
"""Persistent VerificationRecord store (P2) — no silent Verified."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.storage.db import Database
from cognitive_memory.verification.models import VerificationRecord, VerificationStatus


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PersistentVerificationStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.ensure_schema()

    def insert(self, record: VerificationRecord) -> VerificationRecord:
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO verification_records(
                  id, claim_id, status, evidence_refs_json, actor,
                  verified_at, expired_at, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    record.claim_id,
                    record.status.value if hasattr(record.status, "value") else str(record.status),
                    json.dumps(list(record.evidence_refs), ensure_ascii=False),
                    record.actor,
                    record.verified_at,
                    record.valid_until,
                    json.dumps(
                        {
                            "policy_version": record.policy_version,
                            "claim_version": record.claim_version,
                            "reason_codes": list(record.reason_codes),
                            "counterevidence_refs": list(record.counterevidence_refs),
                            "security_context": dict(record.security_context or {}),
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        return record

    def latest_for_claim(self, claim_id: str) -> VerificationRecord | None:
        with self.db.exclusive() as conn:
            row = conn.execute(
                """
                SELECT * FROM verification_records
                WHERE claim_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (claim_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def mark_expired(self, record_id: str) -> VerificationRecord | None:
        rec = self.get(record_id)
        if not rec:
            return None
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE verification_records
                SET status=?, expired_at=?
                WHERE id=?
                """,
                (VerificationStatus.EXPIRED.value, now, record_id),
            )
        return self.get(record_id)

    def get(self, record_id: str) -> VerificationRecord | None:
        with self.db.exclusive() as conn:
            row = conn.execute(
                "SELECT * FROM verification_records WHERE id=?", (record_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    @staticmethod
    def new_record_id() -> str:
        return f"ver_{secrets.token_hex(8)}"

    def _row_to_record(self, row: Any) -> VerificationRecord:
        try:
            refs = json.loads(row["evidence_refs_json"] or "[]")
        except json.JSONDecodeError:
            refs = []
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        return VerificationRecord(
            record_id=row["id"],
            claim_id=row["claim_id"],
            claim_version=int(detail.get("claim_version") or 1),
            status=VerificationStatus(str(row["status"])),
            evidence_refs=list(refs),
            counterevidence_refs=list(detail.get("counterevidence_refs") or []),
            policy_version=str(detail.get("policy_version") or "verification.v1"),
            actor=row["actor"] or "",
            verified_at=row["verified_at"],
            valid_until=row["expired_at"],
            reason_codes=list(detail.get("reason_codes") or []),
            security_context=dict(detail.get("security_context") or {}),
        )
