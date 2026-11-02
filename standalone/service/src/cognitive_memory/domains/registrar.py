# -*- coding: utf-8 -*-
"""Idempotent domain pack registration via CognitiveSchema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cognitive_memory.domains.pack import DomainPack, DomainPackError, load_domain_pack


def _compat_payload(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Same id + same domain_id + compatible kind → reuse; else conflict."""
    if str(existing.get("id") or "") != str(desired.get("id") or ""):
        return False
    ex_domain = str(
        (existing.get("domain_id") or (existing.get("payload") or {}).get("domain_id") or "")
    )
    de_domain = str(desired.get("domain_id") or "")
    if ex_domain and de_domain and ex_domain != de_domain:
        return False
    ex_kind = str(existing.get("type") or existing.get("schema_type") or "").upper()
    de_kind = str(desired.get("type") or desired.get("schema_type") or "").upper()
    if ex_kind and de_kind and ex_kind != de_kind:
        return False
    return True


def register_domain_pack(
    service: Any,
    pack: DomainPack | Path | str | dict[str, Any],
    *,
    actor: str = "domain_registrar",
) -> dict[str, Any]:
    """Register Music (or other) domain ontology. Does NOT import user data.

    Returns created / reused / conflicts / schema_ids.
    Conflicts produce CognitiveChangePlan entries (dry audit) when possible.
    """
    if isinstance(pack, (str, Path)):
        pack = load_domain_pack(pack)
    elif isinstance(pack, dict):
        # allow in-memory dict via temp-like validation
        from tempfile import NamedTemporaryFile
        import json

        with NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(pack, fh, ensure_ascii=False)
            tmp = fh.name
        try:
            pack = load_domain_pack(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)
    if not isinstance(pack, DomainPack):
        raise DomainPackError("invalid pack argument", code="bad_pack")

    created: list[str] = []
    reused: list[str] = []
    conflicts: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []

    root = dict(pack.root_concept)
    root_id = str(root.get("id") or f"domain:{pack.domain_id}")
    nodes = [root] + list(pack.concepts)

    for node in nodes:
        sid = str(node.get("id") or "").strip()
        if not sid:
            continue
        kind = str(node.get("kind") or ("DOMAIN" if sid.startswith("domain:") else "CONCEPT"))
        desired = {
            "id": sid,
            "name": str(node.get("name") or sid),
            "description": str(node.get("description") or ""),
            "type": kind,
            "schema_type": kind,
            "state": "ACTIVE",
            "confidence": 1.0,
            "version": 1,
            "domain_id": pack.domain_id,
            "domain_pack_version": pack.version,
            "parent": node.get("parent") or "",
            "change_type": "DOMAIN_PACK_REGISTER",
            "reason": f"register domain pack {pack.domain_id}@{pack.version} by {actor}",
            "network_links": [
                link
                for link in pack.network_links
                if str(link.get("from") or "") == sid or str(link.get("to") or "") == sid
            ],
            "relations_declared": pack.relations if sid == root_id else [],
            "event_types": pack.event_types if sid == root_id else [],
            "policies": pack.policies if sid == root_id else {},
            "reuse_concepts": pack.reuse_concepts if sid == root_id else [],
            "pack_meta": {
                "domain_id": pack.domain_id,
                "version": pack.version,
                "registers_ontology_only": True,
            },
        }
        existing = None
        try:
            existing = service.get_cognitive_schema(sid)
        except Exception:
            existing = None
        if existing:
            if _compat_payload(existing, desired):
                # refresh pack_meta lightly without bumping conflict
                desired["version"] = int(existing.get("version") or 1)
                desired["state"] = str(existing.get("state") or "ACTIVE")
                service.store_cognitive_schema(desired)
                reused.append(sid)
                schemas.append({"id": sid, "status": "reused"})
            else:
                conflict = {
                    "schema_id": sid,
                    "reason": "incompatible_existing_schema",
                    "existing_type": existing.get("type") or existing.get("schema_type"),
                    "desired_type": kind,
                }
                conflicts.append(conflict)
                schemas.append({"id": sid, "status": "conflict", **conflict})
                _maybe_change_plan(service, pack, sid, existing, desired, actor)
            continue
        service.store_cognitive_schema(desired)
        created.append(sid)
        schemas.append({"id": sid, "status": "created"})

    # root network_links snapshot
    if root_id and not any(c.get("schema_id") == root_id for c in conflicts):
        root_existing = service.get_cognitive_schema(root_id) or {}
        payload = dict(root_existing)
        payload["network_links"] = list(pack.network_links)
        payload["relations_declared"] = list(pack.relations)
        payload["event_types"] = list(pack.event_types)
        payload["policies"] = dict(pack.policies)
        payload["id"] = root_id
        payload["domain_id"] = pack.domain_id
        try:
            service.store_cognitive_schema(payload)
        except Exception:
            pass

    return {
        "ok": not conflicts,
        "domain_id": pack.domain_id,
        "version": pack.version,
        "created": created,
        "reused": reused,
        "conflicts": conflicts,
        "schemas": schemas,
        "note": "ontology_only; user music data not imported",
    }


def _maybe_change_plan(
    service: Any,
    pack: DomainPack,
    schema_id: str,
    existing: dict[str, Any],
    desired: dict[str, Any],
    actor: str,
) -> None:
    builder = getattr(service, "create_cognitive_change_plan", None)
    if not callable(builder):
        # fall back: store a candidate schema note in versions via store with PROPOSED
        try:
            proposed = dict(desired)
            proposed["id"] = f"{schema_id}#proposed:{pack.version}"
            proposed["state"] = "PROPOSED"
            proposed["change_type"] = "DOMAIN_PACK_CONFLICT"
            proposed["reason"] = f"conflict with {schema_id}; actor={actor}"
            proposed["evidence_refs"] = [schema_id]
            service.store_cognitive_schema(proposed)
        except Exception:
            return
        return
    try:
        builder(
            {
                "operation": "DOMAIN_PACK_CONFLICT",
                "target_schema_ids": [schema_id],
                "evidence_refs": [],
                "expected_impact": ["domain_pack_register_blocked"],
                "risk_level": "MEDIUM",
                "approval_required": True,
                "reason": f"pack {pack.domain_id}@{pack.version} conflicts on {schema_id}",
                "payload": {"existing": existing, "desired": desired, "actor": actor},
            }
        )
    except Exception:
        return


def queue_pending_domain_pack(data_dir: Path | str, pack_path: Path | str) -> Path:
    """When Node service is offline at install time, queue pack for later registration."""
    root = Path(data_dir).resolve() / "components" / "pending_domain_packs"
    root.mkdir(parents=True, exist_ok=True)
    src = Path(pack_path).resolve()
    dest = root / src.name
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def drain_pending_domain_packs(service: Any, data_dir: Path | str) -> list[dict[str, Any]]:
    root = Path(data_dir).resolve() / "components" / "pending_domain_packs"
    if not root.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            results.append(register_domain_pack(service, path, actor="pending_drain"))
            path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "path": str(path), "error": str(exc)})
    return results
