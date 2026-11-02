# -*- coding: utf-8 -*-
"""Domain pack load / validate (declarative JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class DomainPackError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_domain_pack"):
        super().__init__(message)
        self.code = code


@dataclass
class DomainPack:
    domain_id: str
    version: str
    name: str
    description: str = ""
    root_concept: dict[str, Any] = field(default_factory=dict)
    concepts: list[dict[str, Any]] = field(default_factory=list)
    reuse_concepts: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    policies: dict[str, Any] = field(default_factory=dict)
    network_links: list[dict[str, Any]] = field(default_factory=list)
    compatibility: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def schema_ids(self) -> list[str]:
        ids = [str(self.root_concept.get("id") or f"domain:{self.domain_id}")]
        for c in self.concepts:
            cid = str(c.get("id") or "").strip()
            if cid:
                ids.append(cid)
        return ids


def load_domain_pack(path: Path | str) -> DomainPack:
    p = Path(path)
    if not p.is_file():
        raise DomainPackError(f"pack not found: {p}", code="pack_not_found")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainPackError(f"cannot read pack: {exc}", code="pack_unreadable") from exc
    if not isinstance(data, dict):
        raise DomainPackError("pack must be an object", code="not_object")
    domain_id = str(data.get("domain_id") or "").strip()
    if not domain_id:
        raise DomainPackError("domain_id required", code="missing_domain_id")
    root = data.get("root_concept") if isinstance(data.get("root_concept"), dict) else {}
    if not root.get("id"):
        root = {
            "id": f"domain:{domain_id}",
            "name": str(data.get("name") or domain_id),
            "kind": "DOMAIN",
            "description": str(data.get("description") or ""),
        }
    concepts = [c for c in (data.get("concepts") or []) if isinstance(c, dict) and c.get("id")]
    return DomainPack(
        domain_id=domain_id,
        version=str(data.get("version") or "1.0.0"),
        name=str(data.get("name") or domain_id),
        description=str(data.get("description") or ""),
        root_concept=dict(root),
        concepts=concepts,
        reuse_concepts=[c for c in (data.get("reuse_concepts") or []) if isinstance(c, dict)],
        relations=[r for r in (data.get("relations") or []) if isinstance(r, dict)],
        event_types=[str(x) for x in (data.get("event_types") or []) if str(x).strip()],
        policies=dict(data.get("policies") or {}) if isinstance(data.get("policies"), dict) else {},
        network_links=[
            x for x in (data.get("network_links") or []) if isinstance(x, dict)
        ],
        compatibility=dict(data.get("compatibility") or {})
        if isinstance(data.get("compatibility"), dict)
        else {},
        raw=dict(data),
    )
