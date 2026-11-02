# -*- coding: utf-8 -*-
"""Dependency resolver for Component Manifest interfaces.requires."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from cognitive_memory.components.manifest import FullComponentManifest


@dataclass
class ResolveResult:
    ok: bool
    missing: list[str] = field(default_factory=list)
    satisfied_by: dict[str, str] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing": list(self.missing),
            "satisfied_by": dict(self.satisfied_by),
            "detail": self.detail,
        }


def collect_provides(manifests: Iterable[FullComponentManifest]) -> dict[str, str]:
    """iface -> component_id (first wins)."""
    out: dict[str, str] = {}
    for m in manifests:
        for iface in m.interfaces_provides:
            out.setdefault(iface, m.component_id)
    return out


def resolve_dependencies(
    target: FullComponentManifest,
    *,
    installed: Iterable[FullComponentManifest] | None = None,
    builtin_provides: Iterable[str] | None = None,
) -> ResolveResult:
    available = collect_provides(installed or [])
    for iface in builtin_provides or ():
        available.setdefault(str(iface), "builtin")
    missing: list[str] = []
    satisfied: dict[str, str] = {}
    for req in target.interfaces_requires:
        provider = available.get(req)
        if not provider:
            missing.append(req)
        else:
            satisfied[req] = provider
    if missing:
        return ResolveResult(
            ok=False,
            missing=missing,
            satisfied_by=satisfied,
            detail="unresolved_requires",
        )
    return ResolveResult(ok=True, missing=[], satisfied_by=satisfied, detail="ok")
