# -*- coding: utf-8 -*-
"""UTF-8 书籍/长文 TXT 首域（E14）。

chapter / paragraph / revision / hash 定位；未知页码 page=null。
作者观点保留为 author_claim，不自动成自我信念或 verified。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

_CHAPTER_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百千0-9]+章|Chapter\s+\d+|CHAPTER\s+\d+)[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)


def content_hash(text: str) -> str:
    norm = unicodedata.normalize("NFC", text or "")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def parse_text_book(
    text: str,
    *,
    book_id: str,
    revision: str = "rev1",
    title: str = "",
) -> dict[str, Any]:
    raw = text if isinstance(text, str) else str(text or "")
    # 仅 UTF-8 文本域；不做 OCR/PDF
    chapters = []
    matches = list(_CHAPTER_RE.finditer(raw))
    if not matches:
        chapters.append(
            {
                "chapter_index": 1,
                "title": title or "全文",
                "start": 0,
                "end": len(raw),
                "text": raw,
            }
        )
    else:
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            chapters.append(
                {
                    "chapter_index": i + 1,
                    "title": m.group(0).strip(),
                    "start": start,
                    "end": end,
                    "text": raw[start:end],
                }
            )

    paragraphs: list[dict[str, Any]] = []
    for ch in chapters:
        parts = [p.strip() for p in re.split(r"\n\s*\n", ch["text"]) if p.strip()]
        for pi, para in enumerate(parts, start=1):
            paragraphs.append(
                {
                    "book_id": book_id,
                    "chapter_index": ch["chapter_index"],
                    "chapter_title": ch["title"],
                    "paragraph_index": pi,
                    "revision": revision,
                    "content_hash": content_hash(para),
                    "page": None,  # 无可靠页码
                    "text": para,
                    "anchor": {
                        "book_id": book_id,
                        "chapter": ch["chapter_index"],
                        "paragraph": pi,
                        "revision": revision,
                        "hash": content_hash(para),
                        "page": None,
                    },
                }
            )
    return {
        "book_id": book_id,
        "title": title or book_id,
        "revision": revision,
        "encoding": "utf-8",
        "chapter_count": len(chapters),
        "paragraph_count": len(paragraphs),
        "chapters": [
            {"chapter_index": c["chapter_index"], "title": c["title"]} for c in chapters
        ],
        "paragraphs": paragraphs,
    }


def author_claim_from_paragraph(
    para: dict[str, Any],
    *,
    author: str = "author",
) -> dict[str, Any]:
    """作者未核验观点 → author_claim，非自我信念、非 verified。"""
    return {
        "kind": "author_claim",
        "author": author,
        "text": para.get("text"),
        "anchor": para.get("anchor"),
        "epistemic": "author_claim",
        "verified": False,
        "self_belief": False,
        "coverage": {
            "book_id": para.get("book_id"),
            "chapter": para.get("chapter_index"),
            "paragraph": para.get("paragraph_index"),
            "revision": para.get("revision"),
        },
        "note": "reading_ne_adopting_author_views",
    }
