# -*- coding: utf-8 -*-
"""导出 Interchange JSONL（E4.7 最小面；非完整 Admin UI）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from cognitive_memory.migration.interchange import InterchangeRecord


def export_interchange_jsonl(
    records: Iterable[InterchangeRecord | dict[str, Any]],
    path: Path,
) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            if isinstance(r, InterchangeRecord):
                obj = r.to_dict()
            else:
                obj = dict(r)
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n += 1
    return {"ok": True, "path": str(path), "count": n}


def memories_to_interchange(
    service: Any,
    *,
    limit: int = 500,
    enforce_forward_policy: bool = True,
) -> list[InterchangeRecord]:
    from cognitive_memory.domain.models import SearchQuery
    from cognitive_memory.governance.policy import filter_forwardable_memories
    from cognitive_memory.migration.interchange import InterchangeRecord

    rows = service.search(SearchQuery(query="", limit=limit, banks=["personal", "shared", "knowledge", "projects"]))
    skipped: list[dict] = []
    if enforce_forward_policy:
        rows, skipped = filter_forwardable_memories(list(rows))
    out: list[InterchangeRecord] = []
    for m in rows:
        out.append(
            InterchangeRecord(
                source_system="mnemosyne",
                source_record_id=m.id,
                content={"text": m.content, "type": m.type.value, "status": m.status.value},
                provenance={"exporter": "memories_to_interchange"},
                time={"created_at": m.created_at, "updated_at": m.updated_at},
                extensions={"bank_id": m.bank_id, "metadata": m.metadata},
                tags=[],
            ).with_checksum()
        )
    # attach skip audit on service last export (caller may ignore)
    service._last_export_policy_skipped = skipped  # type: ignore[attr-defined]
    return out
