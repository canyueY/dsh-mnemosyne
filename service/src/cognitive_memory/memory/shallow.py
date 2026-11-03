"""External perception: provisional candidates, periodic extraction, selection.

Age is measured from capture (not an untrusted upstream event clock). Original
observations remain evidence; extracted words are lossy, unverified retrieval cues.
"""
from __future__ import annotations
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cognitive_memory.domain.models import Memory, MemoryStatus, MemoryType, MemoryUpdateIn
from cognitive_memory.storage.ids import new_id


def policy():
    path = Path(os.environ.get("MNEMOSYNE_SHALLOW_POLICY") or Path(__file__).resolve().parents[1] / "resources/shallow-memory.json")
    p = json.loads(path.read_text(encoding="utf-8-sig"))
    a, b, c = (p[k] for k in ("temporary_days", "prompt_until_days", "impression_until_days"))
    if not 0 < a < b < c or not 0 < p["extraction_interval_hours"] <= 24 * c or not 1 <= p["keyword_limit"] <= 100:
        raise ValueError("invalid shallow memory policy")
    return p


def clock(value=None):
    t = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if t is None:
        t = datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def stage_at(created, now=None, p=None):
    p = p or policy()
    age = max(0, (clock(now) - clock(created)).total_seconds() / 86400)
    return "temporary" if age < p["temporary_days"] else "prompt" if age < p["prompt_until_days"] else "impression" if age <= p["impression_until_days"] else "archived"


def extract(text, limit):
    # Preserve negation-bearing phrases; impression words never assert a trait.
    phrases = [x.strip()[:80] for x in re.split(r"[，。！？；\n.!?;]+", text) if x.strip()]
    words = re.findall(r"[a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return [w for w, _ in Counter(words).most_common(limit)], phrases[:min(limit, 6)]


def is_external_perception(kind, metadata, p=None):
    p = p or policy()
    if metadata.get("external_perception") is True:
        return True
    if kind not in p["external_source_kinds"]:
        return False
    # SYSTEM also carries migrations and operations, which are not perception.
    if kind == "system":
        return metadata.get("domain") in {"music", "video", "game", "minecraft", "screen"} or metadata.get("scene") in {"music", "video", "game", "minecraft", "screen"}
    return True


def create_shallow(service, conn, obs, now):
    p = policy()
    if not is_external_perception(obs.source_kind.value, obs.metadata, p):
        return None
    from cognitive_memory.governance.policy import assert_allow_store
    assert_allow_store(dict(obs.metadata))
    words, impressions = extract(obs.content, p["keyword_limit"])
    state = {"stage": "temporary", "captured_at": now, "last_extracted_at": now,
             "extraction_count": 1, "keywords": words, "impression_words": impressions,
             "initial_cognition": {"kind": "unverified_source_summary", "text": " / ".join(impressions[:3])},
             "extractor": "local_phrase_frequency_v1", "selected": False,
             "long_term_class": "provisional_deletable", "formal_long_term": False}
    meta = {**obs.metadata, "shallow_memory": state, "epistemic": "inferred_signal"}
    mid = new_id("mem")
    bank = str(meta.get("bank_id") or "personal")
    from cognitive_memory.memory.banks import normalize_bank_id
    bank = normalize_bank_id(bank)
    meta["bank_id"] = bank
    mem = Memory(id=mid, type=MemoryType.EPISODIC, status=MemoryStatus.CANDIDATE,
                 content=obs.content, source_ids=[obs.id], created_at=now, updated_at=now,
                 version=1, metadata=meta, bank_id=bank,
                 expires_at=(clock(now) + timedelta(days=p["impression_until_days"])).isoformat())
    service._insert_memory(conn, mem)
    service._emit(conn, "memory.shallow_created", {"memory_id": mid, "observation_id": obs.id})
    return mid


def maintain(service, now=None, limit=100):
    p = policy(); now = clock(now); count = 0
    with service.db.transaction() as conn:
        # Earliest extraction first prevents starvation in large Banks.
        rows = conn.execute("""SELECT m.* FROM memories m WHERE m.status='candidate' AND json_valid(m.metadata_json)
          AND json_type(m.metadata_json,'$.shallow_memory')='object'
          AND COALESCE(json_extract(m.metadata_json,'$.shallow_memory.formal_long_term'),0)=0
          ORDER BY json_extract(m.metadata_json,'$.shallow_memory.last_extracted_at'), m.id LIMIT ?""", (limit,)).fetchall()
        for row in rows:
            mem = service._row_memory(row); meta = dict(mem.metadata); state = dict(meta["shallow_memory"])
            stage = stage_at(state["captured_at"], now, p)
            if stage == state["stage"] and (now - clock(state["last_extracted_at"])).total_seconds() < p["extraction_interval_hours"] * 3600:
                continue
            source = conn.execute("SELECT content FROM observations WHERE id=?", (mem.source_ids[0],)).fetchone()
            if not source:
                continue
            words, impressions = extract(source[0], p["keyword_limit"])
            state.update(stage=stage, last_extracted_at=now.isoformat(), keywords=words,
                         impression_words=impressions, extraction_count=state["extraction_count"] + 1)
            meta["shallow_memory"] = state
            content = source[0] if stage == "temporary" else "未核实提示词：" + " / ".join(words) if stage == "prompt" else "未核实印象线索：" + " / ".join(impressions[:3])
            service.update_memory(mem.id, MemoryUpdateIn(content=content, metadata=meta,
                expected_version=mem.version, status=MemoryStatus.SUPERSEDED if stage == "archived" else mem.status,
                expires_at=(clock(state["captured_at"]) + timedelta(days=p["impression_until_days"])).isoformat()))
            if stage in {"prompt", "impression"}:
                from cognitive_memory.cognition.promote import maybe_promote
                maybe_promote(service, mem.id)
            count += 1
    return {"updated": count, "checked": len(rows)}


def select(service, memory_id, *, reason, actor):
    """Record explicit review, then use the existing evidence promotion gate."""
    if not reason.strip():
        raise ValueError("selection_reason_required")
    with service.db.transaction() as conn:
        data = service.get_memory(memory_id)
        if not data.get("metadata", {}).get("shallow_memory") or data["status"] == "invalidated":
            raise ValueError("not_selectable_shallow_memory")
        if stage_at(data["metadata"]["shallow_memory"]["captured_at"]) not in {"prompt", "impression"}:
            raise ValueError("formal_selection_requires_age_80_to_200_days")
        oid = data["source_ids"][0]
        obs = conn.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone()
        if not obs:
            raise ValueError("source_missing")
        meta = dict(data["metadata"])
        meta.update(selected_from_shallow=memory_id, selection={"reason": reason, "actor": actor})
        if meta.get("selection") != data["metadata"].get("selection"):
            service.update_memory(memory_id, MemoryUpdateIn(metadata=meta, expected_version=data["version"]))
        from cognitive_memory.cognition.promote import maybe_promote
        maybe_promote(service, memory_id)
        return service.get_memory(memory_id)
