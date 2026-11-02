# -*- coding: utf-8 -*-
"""P3 live RO eval: semantic assist + Evidence materialize (shadow only).

Never writes to source memory.db. Never Verified. Never prod Claim/Evidence backfill.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cognitive_memory.claims.shadow_replay import _open_source_readonly, sample_memory_rows
from cognitive_memory.evidence.object_store import EvidenceObjectStore
from cognitive_memory.retrieval.semantic_sidepath import evaluate_assist, pool_suggest
from cognitive_memory.storage.db import Database

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2}|[A-Za-z0-9_]{3,}")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def _fts_like_ids(query: str, corpus: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    qt = _tokens(query)
    scored: list[tuple[float, str]] = []
    qlow = (query or "").strip().lower()
    for doc in corpus:
        text = str(doc.get("content") or "")
        ct = _tokens(text)
        if qt and ct:
            score = len(qt & ct) / max(1, len(qt))
        elif qlow and qlow in text.lower():
            score = 1.0
        else:
            score = 0.0
        if score > 0:
            scored.append((score, str(doc.get("id"))))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [i for _, i in scored[:limit]]


@dataclass
class LiveEvalReport:
    ok: bool
    source_db: str
    shadow_db: str
    sample_count: int = 0
    query_count: int = 0
    fts_recall_avg: float = 0.0
    assist_novel_total: int = 0
    assist_novel_rate: float = 0.0
    evidence_links_scanned: int = 0
    evidence_objects_created: int = 0
    evidence_unknown: int = 0
    evidence_partial: int = 0
    provenance_complete_forbidden: int = 0
    errors: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sample_evidence_links(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT memory_id, observation_id, relation, created_at
            FROM memory_evidence
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "memory_id": r["memory_id"],
            "observation_id": r["observation_id"] or "",
            "relation": r["relation"] or "supports",
        }
        for r in rows
    ]


def run_live_eval(
    source_db: Path,
    shadow_db: Path,
    *,
    memory_limit: int = 80,
    query_limit: int = 20,
    evidence_limit: int = 40,
) -> LiveEvalReport:
    source_db = Path(source_db)
    shadow_db = Path(shadow_db)
    report = LiveEvalReport(ok=True, source_db=str(source_db), shadow_db=str(shadow_db))
    if not source_db.is_file():
        report.ok = False
        report.errors.append("source_db_not_found")
        return report

    src = _open_source_readonly(source_db)
    try:
        memories = sample_memory_rows(src, limit=memory_limit)
        evidence_links = _sample_evidence_links(src, limit=evidence_limit)
    except sqlite3.Error as exc:
        report.ok = False
        report.errors.append(f"sample_failed:{exc}")
        return report
    finally:
        src.close()

    report.sample_count = len(memories)
    corpus = [{"id": m["id"], "content": m.get("content") or ""} for m in memories]

    def _paraphrase(text: str) -> str | None:
        """Build a non-identical query so FTS may miss while semantics can still hit."""
        t = (text or "").strip()
        # music observed lines often embed 「title」(id)
        m = re.search(r"[「『《]([^」』》]{1,40})[」』》]", t)
        if m:
            return f"喜欢听{m.group(1)}"
        m2 = re.search(r"偏好|喜欢|收藏", t)
        if m2 and len(t) >= 6:
            # strip bracket tags
            body = re.sub(r"^\[[^\]]+\]\s*", "", t)
            body = re.sub(r"\(.*?\)", "", body).strip()
            if 2 <= len(body) <= 24:
                return body
        if 8 <= len(t) <= 36 and not t.startswith("["):
            # drop first 2 chars as weak paraphrase
            return t[2:]
        return None

    queries = []
    for m in memories:
        text = str(m.get("content") or "").strip()
        pq = _paraphrase(text)
        if pq:
            queries.append({"id": m["id"], "query": pq, "surface": text[:80]})
        if len(queries) >= query_limit:
            break

    recall_sum = 0.0
    novel_total = 0
    for q in queries:
        query = q["query"]
        relevant = [q["id"]]
        fts_ids = _fts_like_ids(query, corpus, limit=8)
        ev = evaluate_assist(
            query,
            fts_ids=fts_ids,
            relevant_ids=relevant,
            pool=corpus,
            limit=8,
            min_score=0.2,
        )
        recall_sum += float(ev.get("fts_recall") or 0)
        novel = int(ev.get("assist_novel_count") or 0)
        novel_total += novel
        # Also record top semantic suggestion (assistive)
        sug = pool_suggest(query, corpus, limit=3, min_score=0.2)
        report.samples.append(
            {
                "memory_id": q["id"],
                "surface": q.get("surface"),
                "query": query[:80],
                "fts_ids": fts_ids[:5],
                "fts_recall": ev.get("fts_recall"),
                "assist_novel_hits": ev.get("assist_novel_hits"),
                "semantic_top": [
                    {"id": x.get("id"), "semantic": x.get("semantic")}
                    for x in (sug.get("items") or [])[:3]
                ],
            }
        )
    report.query_count = len(queries)
    if queries:
        report.fts_recall_avg = round(recall_sum / len(queries), 4)
        report.assist_novel_total = novel_total
        report.assist_novel_rate = round(novel_total / len(queries), 4)

    # Evidence materialize into shadow only
    shadow_db.parent.mkdir(parents=True, exist_ok=True)
    shadow = Database(shadow_db)
    estore = EvidenceObjectStore(shadow)
    try:
        for link in evidence_links:
            report.evidence_links_scanned += 1
            try:
                created = estore.from_legacy_memory_link(
                    memory_id=str(link.get("memory_id") or ""),
                    observation_id=str(link.get("observation_id") or ""),
                    relation=str(link.get("relation") or "supports"),
                )
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"evidence:{link.get('memory_id')}:{exc}")
                continue
            if not created.get("ok"):
                continue
            ev = created.get("evidence") or {}
            if created.get("deduped"):
                continue
            report.evidence_objects_created += 1
            st = str(ev.get("provenance_status") or "")
            if st == "unknown":
                report.evidence_unknown += 1
            elif st == "partial":
                report.evidence_partial += 1
            elif st == "complete":
                # Should never auto-complete
                report.provenance_complete_forbidden += 1
                report.errors.append(f"unexpected_complete:{ev.get('id')}")
    finally:
        shadow.close()

    out = shadow_db.with_suffix(".live_eval.json")
    out.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="P3 RO live semantic/evidence eval")
    p.add_argument("--source-db", required=True)
    p.add_argument("--shadow-db", required=True)
    p.add_argument("--memory-limit", type=int, default=80)
    p.add_argument("--query-limit", type=int, default=20)
    p.add_argument("--evidence-limit", type=int, default=40)
    args = p.parse_args(argv)
    report = run_live_eval(
        Path(args.source_db),
        Path(args.shadow_db),
        memory_limit=int(args.memory_limit),
        query_limit=int(args.query_limit),
        evidence_limit=int(args.evidence_limit),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
