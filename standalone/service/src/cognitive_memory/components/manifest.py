# -*- coding: utf-8 -*-
"""Component Manifest v0 — 对齐 docs/MNEMOSYNE_COMPONENT_SPEC.md。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cognitive_memory.components.contract import ComponentManifest

MANIFEST_FILENAMES = (
    "mnemosyne.component.json",
    "component.json",
    "manifest.json",
)

VALID_TYPES = frozenset({"provider", "adapter", "extension", "consumer"})


class ManifestError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_manifest"):
        super().__init__(message)
        self.code = code


@dataclass
class FullComponentManifest:
    component_id: str
    name: str = ""
    version: str = "0.1.0"
    type: str = "extension"
    provider: str = "local"
    interfaces_provides: list[str] = field(default_factory=list)
    interfaces_requires: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    banks: list[str] = field(default_factory=list)
    compatibility: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "provider": self.provider,
            "interfaces": {
                "provides": list(self.interfaces_provides),
                "requires": list(self.interfaces_requires),
            },
            "protocols": list(self.protocols),
            "permissions": list(self.permissions),
            "banks": list(self.banks),
            "compatibility": dict(self.compatibility),
            "lifecycle": dict(self.lifecycle),
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }

    def to_contract_manifest(self) -> ComponentManifest:
        return ComponentManifest(
            component_id=self.component_id,
            name=self.name or self.component_id,
            version=self.version,
            permissions=list(self.permissions),
            banks=list(self.banks),
            expires_at=self.expires_at,
            metadata={
                **dict(self.metadata),
                "type": self.type,
                "provider": self.provider,
                "interfaces": {
                    "provides": list(self.interfaces_provides),
                    "requires": list(self.interfaces_requires),
                },
            },
        )


def parse_manifest(data: dict[str, Any]) -> FullComponentManifest:
    if not isinstance(data, dict):
        raise ManifestError("manifest must be an object", code="not_object")
    cid = str(data.get("component_id") or "").strip()
    if not cid:
        raise ManifestError("component_id required", code="missing_id")
    ctype = str(data.get("type") or "extension").strip().lower()
    if ctype not in VALID_TYPES:
        raise ManifestError(f"invalid type: {ctype}", code="invalid_type")
    interfaces = data.get("interfaces") if isinstance(data.get("interfaces"), dict) else {}
    provides = [str(x) for x in (interfaces.get("provides") or []) if str(x).strip()]
    requires = [str(x) for x in (interfaces.get("requires") or []) if str(x).strip()]
    protocols = [str(x) for x in (data.get("protocols") or ["mnemosyne-component-v1"]) if str(x).strip()]
    permissions = [str(x) for x in (data.get("permissions") or []) if str(x).strip()]
    banks = [str(x) for x in (data.get("banks") or []) if str(x).strip()]
    compatibility = data.get("compatibility") if isinstance(data.get("compatibility"), dict) else {}
    lifecycle = data.get("lifecycle") if isinstance(data.get("lifecycle"), dict) else {}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    # unknown top-level keys preserved in raw / extensions-like metadata
    known = {
        "component_id",
        "name",
        "version",
        "type",
        "provider",
        "interfaces",
        "protocols",
        "permissions",
        "banks",
        "compatibility",
        "lifecycle",
        "expires_at",
        "metadata",
    }
    extras = {k: v for k, v in data.items() if k not in known}
    if extras:
        metadata = {**metadata, "extensions": extras}
    return FullComponentManifest(
        component_id=cid,
        name=str(data.get("name") or cid).strip(),
        version=str(data.get("version") or "0.1.0").strip() or "0.1.0",
        type=ctype,
        provider=str(data.get("provider") or "local").strip() or "local",
        interfaces_provides=provides,
        interfaces_requires=requires,
        protocols=protocols,
        permissions=permissions,
        banks=banks,
        compatibility=dict(compatibility),
        lifecycle=dict(lifecycle),
        expires_at=(str(data["expires_at"]) if data.get("expires_at") else None),
        metadata=dict(metadata),
        raw=dict(data),
    )


def load_manifest(path: Path | str) -> FullComponentManifest:
    p = Path(path)
    if p.is_dir():
        for name in MANIFEST_FILENAMES:
            cand = p / name
            if cand.is_file():
                p = cand
                break
        else:
            raise ManifestError(f"no manifest in directory: {path}", code="manifest_not_found")
    if not p.is_file():
        raise ManifestError(f"manifest not found: {p}", code="manifest_not_found")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}", code="manifest_unreadable") from exc
    return parse_manifest(data)


def find_manifest_file(root: Path) -> Path | None:
    for name in MANIFEST_FILENAMES:
        cand = root / name
        if cand.is_file():
            return cand
    return None
