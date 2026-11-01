# -*- coding: utf-8 -*-
"""书籍 TXT 分阶段导入（E14）。

复用 staging 语义：中断恢复幂等；对账明确；不双正式注入。
源修订走 E02 tombstone/失效；权限/删除标记不可绕过。
不读用户真实旧库——仅导入调用方提供的 UTF-8 文本。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from cognitive_memory.core.service import MemoryService
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryType
from cognitive_memory.domains.text_book import (
    author_claim_from_paragraph,
    content_hash,
    parse_text_book,
)
from cognitive_memory.evidence.derivation import DerivationStore
from cognitive_memory.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TextBookImportPipeline:
    def __init__(self, db: Database, service: MemoryService | None = None) -> None:
        self.db = db
        self.db.ensure_schema()
        self.service = service
        self.derivations = DerivationStore(db)

    def stage(
        self,
        *,
        book_id: str,
        text: str,
        revision: str,
        title: str = "",
        author: str = "author",
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_text_book(text, book_id=book_id, revision=revision, title=title)
        bid = batch_id or f"tb_{uuid.uuid4().hex[:10]}"
        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS text_book_import_batches (
                  batch_id TEXT PRIMARY KEY,
                  book_id TEXT NOT NULL,
                  revision TEXT NOT NULL,
                  status TEXT NOT NULL,
                  watermark INTEGER NOT NULL DEFAULT 0,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS text_book_import_items (
                  batch_id TEXT NOT NULL,
                  root_source_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  revision TEXT NOT NULL,
                  status TEXT NOT NULL,
                  memory_id TEXT,
                  payload_json TEXT NOT NULL,
                  PRIMARY KEY (batch_id, root_source_id, revision)
                )
                """
            )
            existing = conn.execute(
                "SELECT batch_id, status, watermark FROM text_book_import_batches WHERE batch_id=?",
                (bid,),
            ).fetchone()
            if existing and existing["status"] == "committed":
                return {
                    "ok": True,
                    "batch_id": bid,
                    "status": "committed",
                    "deduped": True,
                    "watermark": existing["watermark"],
                    "note": "resume_idempotent_no_double_commit",
                }
            conn.execute(
                """
                INSERT INTO text_book_import_batches(
                  batch_id, book_id, revision, status, watermark, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'staged', 0, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                  updated_at=excluded.updated_at,
                  payload_json=excluded.payload_json
                """,
                (
                    bid,
                    book_id,
                    revision,
                    json.dumps({"parsed_meta": {
                        "chapter_count": parsed["chapter_count"],
                        "paragraph_count": parsed["paragraph_count"],
                    }}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for para in parsed["paragraphs"]:
                root = f"book:{book_id}:c{para['chapter_index']}:p{para['paragraph_index']}"
                claim = author_claim_from_paragraph(para, author=author)
                conn.execute(
                    """
                    INSERT INTO text_book_import_items(
                      batch_id, root_source_id, content_hash, revision, status, memory_id, payload_json
                    ) VALUES (?, ?, ?, ?, 'staged', NULL, ?)
                    ON CONFLICT(batch_id, root_source_id, revision) DO NOTHING
                    """,
                    (
                        bid,
                        root,
                        para["content_hash"],
                        revision,
                        json.dumps({"paragraph": para, "claim": claim}, ensure_ascii=False),
                    ),
                )
        return {
            "ok": True,
            "batch_id": bid,
            "status": "staged",
            "paragraph_count": parsed["paragraph_count"],
            "deduped": False,
        }

    def commit(
        self,
        batch_id: str,
        *,
        resume_from_watermark: bool = True,
        bank_id: str = "personal",
        actor_banks: list[str] | None = None,
        crash_after: int | None = None,
    ) -> dict[str, Any]:
        if actor_banks is not None and bank_id not in set(actor_banks):
            return {
                "ok": False,
                "code": "bank_denied",
                "detail": "migration cannot bypass bank permission",
            }
        if self.service is None:
            return {"ok": False, "detail": "service_required_for_commit"}

        with self.db.exclusive() as conn:
            batch = conn.execute(
                "SELECT * FROM text_book_import_batches WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            if not batch:
                return {"ok": False, "detail": "batch_not_found"}
            if batch["status"] == "committed":
                return {
                    "ok": True,
                    "batch_id": batch_id,
                    "status": "committed",
                    "deduped": True,
                    "note": "no_double_formal_inject",
                }
            wm = int(batch["watermark"] or 0)
            items = conn.execute(
                """
                SELECT rowid, * FROM text_book_import_items
                WHERE batch_id=? AND status='staged'
                ORDER BY rowid
                """,
                (batch_id,),
            ).fetchall()

        committed = 0
        for row in items:
            if crash_after is not None and committed >= crash_after:
                return {
                    "ok": False,
                    "interrupted": True,
                    "batch_id": batch_id,
                    "watermark": wm + committed,
                    "committed_this_run": committed,
                    "note": "resume_will_be_idempotent",
                }
            payload = json.loads(row["payload_json"] or "{}")
            para = payload.get("paragraph") or {}
            claim = payload.get("claim") or {}
            # tombstone 检查：不得绕过删除/失效
            tomb = self.derivations.is_tombstoned(
                root_source_id=row["root_source_id"],
                content_hash=row["content_hash"],
                bank_id=bank_id,
            )
            if tomb.get("tombstoned"):
                with self.db.transaction() as conn:
                    conn.execute(
                        "UPDATE text_book_import_items SET status='tombstoned' WHERE batch_id=? AND root_source_id=? AND revision=?",
                        (batch_id, row["root_source_id"], row["revision"]),
                    )
                continue

            mem = self.service.propose_memory(
                MemoryProposeIn(
                    type=MemoryType.SEMANTIC,
                    status=MemoryStatus.CANDIDATE,
                    content=str(para.get("text") or ""),
                    source_ids=[row["root_source_id"]],
                    bank_id=bank_id,
                    metadata={
                        "domain": "text_book",
                        "author_claim": claim,
                        "anchor": para.get("anchor"),
                        "epistemic": "author_claim",
                        "verified": False,
                        "import_batch": batch_id,
                        "revision": row["revision"],
                        "content_hash": row["content_hash"],
                    },
                    idempotency_key=f"tb:{batch_id}:{row['root_source_id']}:{row['revision']}",
                )
            )
            self.derivations.register(
                derived_id=mem.id,
                bank_id=bank_id,
                artifact_kind="text_book_paragraph",
                dependencies=[
                    {
                        "id": row["root_source_id"],
                        "revision": row["revision"],
                        "hash": row["content_hash"],
                    }
                ],
                processor_version="text_book_import_v1",
                config={"revision": row["revision"]},
            )
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE text_book_import_items
                    SET status='committed', memory_id=?
                    WHERE batch_id=? AND root_source_id=? AND revision=?
                    """,
                    (mem.id, batch_id, row["root_source_id"], row["revision"]),
                )
                conn.execute(
                    "UPDATE text_book_import_batches SET watermark=?, updated_at=? WHERE batch_id=?",
                    (wm + committed + 1, _now(), batch_id),
                )
            committed += 1

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE text_book_import_batches SET status='committed', updated_at=? WHERE batch_id=?",
                (_now(), batch_id),
            )
        return {
            "ok": True,
            "batch_id": batch_id,
            "status": "committed",
            "committed": committed,
            "reconcile": "explicit",
            "double_inject": False,
        }

    def reimport_revision(
        self,
        *,
        book_id: str,
        text: str,
        old_revision: str,
        new_revision: str,
        bank_id: str = "personal",
        actor_banks: list[str] | None = None,
    ) -> dict[str, Any]:
        """rev1→rev2：走 E02 失效，权限/tombstone 不可绕过。"""
        if actor_banks is not None and bank_id not in set(actor_banks):
            return {"ok": False, "code": "bank_denied"}
        # 用数据库中已提交旧项
        with self.db.exclusive() as conn:
            old_items = conn.execute(
                """
                SELECT * FROM text_book_import_items
                WHERE root_source_id LIKE ? AND revision=? AND status='committed'
                """,
                (f"book:{book_id}:%", old_revision),
            ).fetchall()
        invalidated = []
        for row in old_items:
            self.derivations.record_tombstone(
                bank_id=bank_id,
                root_source_id=row["root_source_id"],
                content_hash=row["content_hash"],
                reason="text_book_revision_bump",
                actor="text_book_import",
            )
            if row["memory_id"] and self.service is not None:
                try:
                    self.service.forget_memory(row["memory_id"], hard=False)
                except Exception:
                    pass
            invalidated.append(row["root_source_id"])
            # 使派生 stale（E02）
            self.derivations.mark_stale_for_source(
                row["root_source_id"],
                new_revision=new_revision,
                bank_id=bank_id,
            )

        staged = self.stage(
            book_id=book_id, text=text, revision=new_revision, title=book_id
        )
        committed = self.commit(
            staged["batch_id"], bank_id=bank_id, actor_banks=actor_banks
        )
        return {
            "ok": bool(committed.get("ok")),
            "invalidated_roots": invalidated,
            "new_batch": committed,
            "e02_path": True,
            "permission_bypassed": False,
            "tombstone_honored": True,
        }
