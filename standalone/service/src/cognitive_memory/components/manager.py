# -*- coding: utf-8 -*-
"""Component Manager v0 — 本地源安装/启用/健康/禁用/回滚。

安装成功 ≠ 已授权（ENGINEERING_CONSTRAINTS）。
不做社区市场；Git 仅支持「已检出目录」作为本地路径。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cognitive_memory.components.contract import ComponentRegistry
from cognitive_memory.components.manifest import (
    FullComponentManifest,
    ManifestError,
    find_manifest_file,
    load_manifest,
)
from cognitive_memory.components.resolver import resolve_dependencies

STATES = (
    "discovered",
    "installed",
    "configured",
    "enabled",
    "disabled",
    "failed",
    "removed",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def components_root(data_dir: Path | str) -> Path:
    root = Path(data_dir).resolve() / "components"
    root.mkdir(parents=True, exist_ok=True)
    (root / "installed").mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class ComponentRecord:
    component_id: str
    state: str = "installed"
    version: str = "0.1.0"
    source: str = ""
    install_path: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    authorized: bool = False
    last_health: dict[str, Any] = field(default_factory=dict)
    installed_at: str = ""
    updated_at: str = ""
    error: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "state": self.state,
            "version": self.version,
            "source": self.source,
            "install_path": self.install_path,
            "config": dict(self.config),
            "authorized": self.authorized,
            "last_health": dict(self.last_health),
            "installed_at": self.installed_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "manifest": dict(self.manifest),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComponentRecord":
        return cls(
            component_id=str(data.get("component_id") or ""),
            state=str(data.get("state") or "installed"),
            version=str(data.get("version") or "0.1.0"),
            source=str(data.get("source") or ""),
            install_path=str(data.get("install_path") or ""),
            config=dict(data.get("config") or {}),
            authorized=bool(data.get("authorized")),
            last_health=dict(data.get("last_health") or {}),
            installed_at=str(data.get("installed_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            error=str(data.get("error") or ""),
            manifest=dict(data.get("manifest") or {}),
        )


class ComponentManager:
    def __init__(
        self,
        data_dir: Path | str,
        *,
        registry: ComponentRegistry | None = None,
        builtin_provides: list[str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.root = components_root(self.data_dir)
        self.state_path = self.root / "state.json"
        self.registry = registry or ComponentRegistry()
        self.builtin_provides = list(
            builtin_provides
            or [
                "memory.read",
                "memory.write",
                "observation.ingest",
                "node.health",
            ]
        )
        self._records: dict[str, ComponentRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.state_path.is_file():
            self._records = {}
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._records = {}
            return
        items = raw.get("components") if isinstance(raw, dict) else {}
        self._records = {
            str(k): ComponentRecord.from_dict(v)
            for k, v in (items or {}).items()
            if isinstance(v, dict)
        }

    def _save(self) -> None:
        payload = {
            "updated_at": _now(),
            "components": {cid: rec.to_dict() for cid, rec in sorted(self._records.items())},
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def list(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in sorted(self._records.values(), key=lambda x: x.component_id)]

    def get(self, component_id: str) -> dict[str, Any] | None:
        rec = self._records.get(component_id)
        return rec.to_dict() if rec else None

    def inspect(self, source: Path | str) -> dict[str, Any]:
        try:
            m = load_manifest(source)
        except ManifestError as exc:
            return {"ok": False, "error": exc.code, "detail": str(exc)}
        return {
            "ok": True,
            "manifest": m.to_dict(),
            "permissions_declarative": True,
            "note": "inspect ≠ install ≠ authorize",
        }

    def _installed_manifests(self) -> list[FullComponentManifest]:
        out: list[FullComponentManifest] = []
        for rec in self._records.values():
            if rec.state in ("removed", "failed"):
                continue
            if rec.manifest:
                try:
                    from cognitive_memory.components.manifest import parse_manifest

                    out.append(parse_manifest(rec.manifest))
                except ManifestError:
                    continue
            elif rec.install_path:
                try:
                    out.append(load_manifest(rec.install_path))
                except ManifestError:
                    continue
        return out

    def resolve(self, source: Path | str) -> dict[str, Any]:
        try:
            m = load_manifest(source)
        except ManifestError as exc:
            return {"ok": False, "error": exc.code, "detail": str(exc)}
        result = resolve_dependencies(
            m,
            installed=self._installed_manifests(),
            builtin_provides=self.builtin_provides,
        )
        return {"ok": result.ok, "manifest": m.to_dict(), "resolve": result.to_dict()}

    def install(
        self,
        source: Path | str,
        *,
        force: bool = False,
        skip_resolve: bool = False,
        authorize: bool = False,
    ) -> dict[str, Any]:
        src = Path(source).resolve()
        try:
            m = load_manifest(src)
        except ManifestError as exc:
            return {"ok": False, "error": exc.code, "detail": str(exc)}
        if not skip_resolve:
            rr = resolve_dependencies(
                m,
                installed=self._installed_manifests(),
                builtin_provides=self.builtin_provides,
            )
            if not rr.ok:
                return {
                    "ok": False,
                    "error": "unresolved_requires",
                    "resolve": rr.to_dict(),
                    "manifest": m.to_dict(),
                }

        existing = self._records.get(m.component_id)
        if existing and existing.state not in ("removed",) and not force:
            return {
                "ok": False,
                "error": "already_installed",
                "component": existing.to_dict(),
                "hint": "pass force=True to replace",
            }

        dest = self.root / "installed" / m.component_id.replace("/", "_").replace("\\", "_")
        backup: Path | None = None
        if dest.exists():
            backup = dest.with_name(dest.name + ".bak-" + _now().replace(":", ""))
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            dest.rename(backup)

        try:
            dest.mkdir(parents=True, exist_ok=True)
            src_root = src if src.is_dir() else src.parent
            # copy tree (shallow package)
            for item in src_root.iterdir():
                target = dest / item.name
                if item.is_dir():
                    if item.name in (".git", "__pycache__", ".venv", "node_modules"):
                        continue
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
            # ensure manifest present under standard name
            if not find_manifest_file(dest):
                (dest / "mnemosyne.component.json").write_text(
                    json.dumps(m.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            # verify
            load_manifest(dest)
        except Exception as exc:  # noqa: BLE001
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            if backup and backup.exists():
                backup.rename(dest)
            return {"ok": False, "error": "install_failed", "detail": str(exc)}

        if backup and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

        now = _now()
        rec = ComponentRecord(
            component_id=m.component_id,
            state="installed",
            version=m.version,
            source=str(src),
            install_path=str(dest),
            config={},
            authorized=False,
            installed_at=(existing.installed_at if existing and existing.installed_at else now),
            updated_at=now,
            manifest=m.to_dict(),
        )
        self._records[m.component_id] = rec
        # register into process registry (still unauthorized)
        self.registry.register(m.to_contract_manifest())
        auth_info: dict[str, Any] | None = None
        if authorize:
            auth_info = self.authorize(m.component_id)
        domain_pack_result = self._queue_or_note_domain_packs(dest, m)
        self._save()
        return {
            "ok": True,
            "component": rec.to_dict(),
            "authorized": bool(authorize),
            "authorization": auth_info,
            "domain_packs": domain_pack_result,
            "note": "install ≠ authorize" if not authorize else "authorized_on_install",
        }

    def configure(self, component_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        rec = self._records.get(component_id)
        if not rec or rec.state == "removed":
            return {"ok": False, "error": "not_installed"}
        rec.config = dict(config or {})
        if rec.state in ("installed", "disabled"):
            rec.state = "configured"
        rec.updated_at = _now()
        self._save()
        return {"ok": True, "component": rec.to_dict()}

    def enable(self, component_id: str, *, require_auth: bool = False) -> dict[str, Any]:
        rec = self._records.get(component_id)
        if not rec or rec.state == "removed":
            return {"ok": False, "error": "not_installed"}
        if require_auth and not rec.authorized and not self.registry.is_authorized(component_id):
            return {
                "ok": False,
                "error": "not_authorized",
                "detail": "enable with require_auth=True needs prior authorize()",
            }
        rec.state = "enabled"
        rec.error = ""
        rec.updated_at = _now()
        self._save()
        health = self.health(component_id)
        return {"ok": True, "component": rec.to_dict(), "health": health}

    def disable(self, component_id: str) -> dict[str, Any]:
        rec = self._records.get(component_id)
        if not rec or rec.state == "removed":
            return {"ok": False, "error": "not_installed"}
        rec.state = "disabled"
        rec.updated_at = _now()
        self._save()
        return {"ok": True, "component": rec.to_dict()}

    def authorize(self, component_id: str) -> dict[str, Any]:
        rec = self._records.get(component_id)
        if not rec or rec.state == "removed":
            return {"ok": False, "error": "not_installed"}
        loaded: FullComponentManifest | None = None
        try:
            if rec.install_path:
                loaded = load_manifest(rec.install_path)
        except ManifestError:
            loaded = None
        if loaded is None and rec.manifest:
            from cognitive_memory.components.manifest import parse_manifest

            try:
                loaded = parse_manifest(rec.manifest)
            except ManifestError:
                loaded = None
        if loaded is None:
            return {"ok": False, "error": "manifest_missing"}
        self.registry.register(loaded.to_contract_manifest())
        info = self.registry.authorize(
            component_id,
            permissions=list(loaded.permissions),
            banks=list(loaded.banks or ["personal"]),
        )
        rec.authorized = True
        rec.updated_at = _now()
        self._save()
        return {"ok": True, "authorization": info, "component": rec.to_dict()}

    def health(self, component_id: str) -> dict[str, Any]:
        rec = self._records.get(component_id)
        if not rec or rec.state == "removed":
            return {"ok": False, "error": "not_installed"}
        report: dict[str, Any] = {
            "ok": True,
            "component_id": component_id,
            "state": rec.state,
            "checks": [],
        }
        path = Path(rec.install_path) if rec.install_path else None
        if not path or not path.is_dir():
            report["ok"] = False
            report["checks"].append({"id": "install_path", "ok": False})
            rec.state = "failed"
            rec.error = "install_path_missing"
            rec.last_health = report
            rec.updated_at = _now()
            self._save()
            return report
        report["checks"].append({"id": "install_path", "ok": True, "path": str(path)})
        try:
            m = load_manifest(path)
            report["checks"].append(
                {"id": "manifest", "ok": True, "version": m.version, "type": m.type}
            )
        except ManifestError as exc:
            report["ok"] = False
            report["checks"].append({"id": "manifest", "ok": False, "error": str(exc)})
        want_health = True
        if rec.manifest.get("lifecycle"):
            want_health = bool(rec.manifest["lifecycle"].get("health_check", True))
        if want_health and rec.state == "enabled":
            report["checks"].append({"id": "enabled", "ok": True})
        rec.last_health = report
        rec.updated_at = _now()
        if not report["ok"]:
            rec.state = "failed"
            rec.error = "health_failed"
        self._save()
        return report

    def _domain_pack_paths(self, install_dir: Path, manifest: FullComponentManifest) -> list[Path]:
        raw = dict(manifest.raw or {})
        meta = dict(manifest.metadata or {})
        ext = meta.get("extensions") if isinstance(meta.get("extensions"), dict) else {}
        listed = raw.get("domain_packs") or meta.get("domain_packs") or ext.get("domain_packs") or []
        if isinstance(listed, str):
            listed = [listed]
        out: list[Path] = []
        for item in listed:
            rel = str(item or "").strip()
            if not rel:
                continue
            cand = (install_dir / rel).resolve()
            if cand.is_relative_to(install_dir.resolve()) and cand.is_file():
                out.append(cand)
        return out

    def _queue_or_note_domain_packs(
        self, install_dir: Path, manifest: FullComponentManifest
    ) -> dict[str, Any]:
        """Install-time: queue domain packs for MemoryService registration (ontology only)."""
        paths = self._domain_pack_paths(install_dir, manifest)
        if not paths:
            return {"queued": [], "note": "no_domain_packs"}
        queued: list[str] = []
        try:
            from cognitive_memory.domains.registrar import queue_pending_domain_pack
        except Exception as exc:  # noqa: BLE001
            return {"queued": [], "error": f"registrar_unavailable:{exc}"}
        for p in paths:
            try:
                dest = queue_pending_domain_pack(self.data_dir, p)
                queued.append(str(dest))
            except Exception as exc:  # noqa: BLE001
                return {"queued": queued, "error": str(exc), "failed": str(p)}
        return {
            "queued": queued,
            "note": "pending_until_domains_register; ontology_only_no_user_data",
        }

    def apply_pending_domain_packs(self, service: Any) -> list[dict[str, Any]]:
        from cognitive_memory.domains.registrar import drain_pending_domain_packs

        return drain_pending_domain_packs(service, self.data_dir)

    def remove(self, component_id: str, *, purge: bool = True) -> dict[str, Any]:
        rec = self._records.get(component_id)
        if not rec:
            return {"ok": False, "error": "not_found"}
        path = Path(rec.install_path) if rec.install_path else None
        if purge and path and path.exists():
            shutil.rmtree(path, ignore_errors=True)
        rec.state = "removed"
        rec.authorized = False
        rec.updated_at = _now()
        self._save()
        return {"ok": True, "component": rec.to_dict(), "purged": bool(purge)}

    def rollback_failed_install(self, component_id: str) -> dict[str, Any]:
        """失败态清理：删目录并标 removed。"""
        rec = self._records.get(component_id)
        if not rec:
            return {"ok": False, "error": "not_found"}
        path = Path(rec.install_path) if rec.install_path else None
        if path and path.exists():
            shutil.rmtree(path, ignore_errors=True)
        rec.state = "removed"
        rec.error = "rolled_back"
        rec.updated_at = _now()
        self._save()
        return {"ok": True, "component": rec.to_dict()}
