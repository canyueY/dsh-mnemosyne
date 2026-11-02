# -*- coding: utf-8 -*-
"""Mnemosyne Node 管理入口（不依赖 DSH / 桌宠）。

用法：
  mnemosyne doctor|status|health|start|stop
  mnemosyne config show|init|validate
  mnemosyne backup|restore
  mnemosyne component list|inspect|install|enable|disable|health|authorize|remove|profiles
  mnemosyne authority show|transition|cutover-preview|cutover
  mnemosyne adopt export
  mnemosyne memory-bank status|init|migrate-legacy
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _ensure_src_path() -> None:
    here = Path(__file__).resolve().parent
    src_root = here.parent
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


def _paths(data_dir: str) -> dict[str, Path]:
    _ensure_src_path()
    from cognitive_memory.storage.paths import ensure_layout

    return ensure_layout(Path(data_dir).resolve() if data_dir else None)


def _load_cfg(args: argparse.Namespace):
    _ensure_src_path()
    from cognitive_memory.node_config import load_node_config

    cfg_path = getattr(args, "config", "") or ""
    return load_node_config(config_path=cfg_path or None, data_dir_hint=args.data_dir or None)


def _effective_data_dir(args: argparse.Namespace) -> str:
    try:
        cfg = _load_cfg(args)
        return cfg.data_dir or args.data_dir or ""
    except Exception:
        return args.data_dir or ""


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_doctor(args: argparse.Namespace) -> int:
    _ensure_src_path()
    report: dict[str, Any] = {"ok": True, "checks": [], "host_dependency": "none_required"}
    data_dir = _effective_data_dir(args)
    try:
        cfg = _load_cfg(args)
        report["checks"].append(
            {
                "id": "config",
                "ok": True,
                "source_path": cfg.source_path,
                "node_id": cfg.node_id,
                "remote_access": cfg.remote_access,
            }
        )
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["checks"].append({"id": "config", "ok": False, "error": str(exc)})
    try:
        paths = _paths(data_dir)
        report["data_dir"] = str(paths["root"])
        report["checks"].append({"id": "data_layout", "ok": True, "paths": {k: str(v) for k, v in paths.items()}})
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["checks"].append({"id": "data_layout", "ok": False, "error": str(exc)})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    # 导入核心包（不 import DSH / Murasame）
    try:
        import cognitive_memory  # noqa: F401
        from cognitive_memory import bootstrap, cli, main as cm_main  # noqa: F401

        report["checks"].append(
            {
                "id": "imports",
                "ok": True,
                "modules": ["cognitive_memory", "bootstrap", "cli", "main"],
                "dsh_imported": "Murasame" in sys.modules or "dsh" in sys.modules,
            }
        )
        if report["checks"][-1]["dsh_imported"]:
            # 不应因 doctor 本身加载 DSH；若已在进程中则仅告警
            report["checks"].append({"id": "dsh_absent", "ok": True, "note": "preloaded_in_process"})
        else:
            report["checks"].append({"id": "dsh_absent", "ok": True})
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["checks"].append({"id": "imports", "ok": False, "error": str(exc)})

    # 打开库 + health（不启动 HTTP）
    try:
        from cognitive_memory.bootstrap import build_context

        ctx = build_context(data_dir=data_dir or None, token=args.token or "node-doctor")
        try:
            health = ctx.service.health()
            report["checks"].append({"id": "local_health", "ok": bool(health.get("ok")), "health": health})
            if not health.get("ok"):
                report["ok"] = False
        finally:
            ctx.db.close()
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["checks"].append({"id": "local_health", "ok": False, "error": str(exc)})

    db = paths["db"]
    report["checks"].append(
        {
            "id": "sqlite_file",
            "ok": True,
            "exists": db.is_file(),
            "path": str(db),
            "bytes": db.stat().st_size if db.is_file() else 0,
        }
    )

    try:
        from cognitive_memory.storage.paths import MEMORY_BANK_NAME, migration_status

        mig = migration_status(target=paths["root"])
        report["memory_bank"] = MEMORY_BANK_NAME
        report["checks"].append(
            {
                "id": "memory_bank_isolation",
                "ok": True,
                "bank_name": MEMORY_BANK_NAME,
                "data_dir": str(paths["root"]),
                "legacy_dsh_path": mig.get("legacy"),
                "recommend_migrate": bool(mig.get("recommend_migrate")),
                "migration": mig,
                "note": "代码仓 mnemosyne/ 与 Memory Bank 目录分离；默认不再使用 DSH\\cognitive-memory",
            }
        )
        if mig.get("recommend_migrate"):
            report["checks"].append(
                {
                    "id": "legacy_migrate_hint",
                    "ok": True,
                    "warning": True,
                    "hint": "mnemosyne memory-bank migrate-legacy",
                }
            )
    except Exception as exc:  # noqa: BLE001
        report["checks"].append({"id": "memory_bank_isolation", "ok": False, "error": str(exc)})

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_memory_bank(args: argparse.Namespace) -> int:
    """Memory Bank 状态 / 从旧 DSH 路径迁出。"""
    _ensure_src_path()
    from cognitive_memory.storage.paths import (
        MEMORY_BANK_NAME,
        copy_memory_bank,
        default_data_root,
        ensure_layout,
        legacy_dsh_data_root,
        migration_status,
    )

    action = str(getattr(args, "bank_action", "") or "")
    target = Path(args.to).expanduser().resolve() if getattr(args, "to", "") else default_data_root()
    source = (
        Path(args.source).expanduser().resolve()
        if getattr(args, "source", "")
        else legacy_dsh_data_root()
    )
    if action == "status":
        out = migration_status(target=target, legacy=source)
        out["ok"] = True
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if action == "init":
        paths = ensure_layout(target)
        print(
            json.dumps(
                {
                    "ok": True,
                    "bank_name": MEMORY_BANK_NAME,
                    "data_dir": str(paths["root"]),
                    "db": str(paths["db"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if action == "migrate-legacy":
        result = copy_memory_bank(source, target, overwrite=bool(getattr(args, "overwrite", False)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    print(json.dumps({"ok": False, "error": f"unknown_action:{action}"}, ensure_ascii=False))
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    paths = _paths(_effective_data_dir(args))
    meta = _read_json(paths["service_json"]) or {}
    pid = int(meta.get("pid") or 0)
    running = _pid_alive(pid) if pid else False
    payload = {
        "ok": True,
        "data_dir": str(paths["root"]),
        "service_json": str(paths["service_json"]),
        "running": running,
        "pid": pid or None,
        "listen": {"host": meta.get("host"), "port": meta.get("port")} if meta else None,
        "api_prefix": meta.get("api_prefix"),
    }
    if running and meta.get("host") and meta.get("port"):
        payload["hint"] = f"http://{meta['host']}:{meta['port']}{meta.get('api_prefix') or '/api/v1'}/health"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from cognitive_memory.cli import cmd_health as cli_health

    ns = argparse.Namespace(data_dir=_effective_data_dir(args) or "", token=args.token or "node-health")
    return int(cli_health(ns))


def cmd_config(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from cognitive_memory.node_config import load_node_config, write_default_config

    action = args.config_action
    if action == "init":
        data_dir = args.data_dir or os.environ.get("DSH_MEMORY_DATA_DIR", "")
        if not data_dir:
            print(json.dumps({"ok": False, "error": "data_dir_required"}, ensure_ascii=False))
            return 2
        dest = Path(args.output) if args.output else Path(data_dir).resolve() / "node.config.json"
        path = write_default_config(dest, data_dir=data_dir)
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False, indent=2))
        return 0
    if action == "validate":
        try:
            cfg = load_node_config(config_path=args.config or None, data_dir_hint=args.data_dir or None)
            print(json.dumps({"ok": True, **cfg.to_public_dict()}, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
    # show
    try:
        cfg = load_node_config(config_path=args.config or None, data_dir_hint=args.data_dir or None)
        print(json.dumps({"ok": True, **cfg.to_public_dict()}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


def cmd_backup(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from cognitive_memory.cli import cmd_backup as cli_backup

    ns = argparse.Namespace(data_dir=_effective_data_dir(args) or "", output=args.output or "")
    return int(cli_backup(ns))


def cmd_restore(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from cognitive_memory.cli import cmd_restore as cli_restore

    ns = argparse.Namespace(
        backup=args.backup,
        data_dir=_effective_data_dir(args) or "",
        force=bool(args.force),
        force_running=bool(getattr(args, "force_running", False)),
    )
    return int(cli_restore(ns))


def cmd_start(args: argparse.Namespace) -> int:
    """前台启动，或 --background 拉起独立进程（仍不依赖 DSH）。"""
    _ensure_src_path()
    from cognitive_memory.node_config import resolve_token

    cfg = _load_cfg(args)
    data_dir = args.data_dir or cfg.data_dir or os.environ.get("DSH_MEMORY_DATA_DIR", "")
    host = args.host or cfg.host or os.environ.get("DSH_MEMORY_HOST", "127.0.0.1")
    port = int(
        args.port
        if args.port is not None
        else (cfg.port if cfg.port else int(os.environ.get("DSH_MEMORY_PORT", "8765")))
    )
    token = resolve_token(cfg, args.token or "")

    if args.background:
        paths = _paths(data_dir)
        meta = _read_json(paths["service_json"]) or {}
        old_pid = int(meta.get("pid") or 0)
        if old_pid and _pid_alive(old_pid):
            print(json.dumps({"ok": False, "error": "already_running", "pid": old_pid}, ensure_ascii=False))
            return 1
        env = os.environ.copy()
        if data_dir:
            env["DSH_MEMORY_DATA_DIR"] = str(Path(data_dir).resolve())
        if token:
            env["DSH_MEMORY_TOKEN"] = token
        env["DSH_MEMORY_HOST"] = host
        env["DSH_MEMORY_PORT"] = str(port)
        cmd = [
            sys.executable,
            "-m",
            "cognitive_memory.main",
            "--host",
            host,
            "--port",
            str(port),
        ]
        if data_dir:
            cmd.extend(["--data-dir", str(Path(data_dir).resolve())])
        if token:
            cmd.extend(["--token", token])
        # 新控制台分离（Windows）/ 后台
        popen_kwargs: dict[str, Any] = {"env": env, "cwd": str(Path.cwd())}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            popen_kwargs["close_fds"] = True
        else:
            popen_kwargs["start_new_session"] = True
        log_path = paths["logs"] / "node-stdout.log"
        log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT, **popen_kwargs)
        # 等待 service.json
        deadline = time.time() + 20
        bound = None
        while time.time() < deadline:
            meta = _read_json(paths["service_json"])
            if meta and int(meta.get("pid") or 0) == proc.pid:
                bound = meta
                break
            if meta and _pid_alive(int(meta.get("pid") or 0)):
                bound = meta
                break
            time.sleep(0.25)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "background",
                    "pid": proc.pid,
                    "log": str(log_path),
                    "service": bound,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    from cognitive_memory.main import main as run_main

    argv = ["--host", host, "--port", str(port)]
    if data_dir:
        argv.extend(["--data-dir", str(Path(data_dir).resolve())])
    if token:
        argv.extend(["--token", token])
    return int(run_main(argv))


def cmd_stop(args: argparse.Namespace) -> int:
    paths = _paths(_effective_data_dir(args))
    meta = _read_json(paths["service_json"]) or {}
    pid = int(meta.get("pid") or 0)
    if not pid:
        print(json.dumps({"ok": False, "error": "no_pid_in_service_json"}, ensure_ascii=False))
        return 1
    if not _pid_alive(pid):
        print(json.dumps({"ok": True, "stopped": False, "note": "process_not_running", "pid": pid}, ensure_ascii=False))
        return 0
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "pid": pid}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "stopped": True, "pid": pid}, ensure_ascii=False))
    return 0


def cmd_authority(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from cognitive_memory.bootstrap import build_context
    from cognitive_memory.governance.authority import AuthorityTransitionError
    from cognitive_memory.governance.cutover import execute_cutover, preview_cutover

    data_dir = _effective_data_dir(args)
    ctx = build_context(data_dir=data_dir or None, token=args.token or "cli-authority")
    action = str(getattr(args, "authority_action", "") or "")
    try:
        if action == "show":
            out = ctx.service.get_authority()
            out = {"ok": True, "authority": out}
        elif action == "transition":
            try:
                auth = ctx.service.transition_authority(
                    args.state,
                    reason=str(getattr(args, "reason", "") or ""),
                    allow_dual=bool(getattr(args, "allow_dual", False)),
                    actor="node_cli",
                )
                out = {"ok": True, "authority": auth}
            except AuthorityTransitionError as exc:
                out = {"ok": False, "error": exc.code, "detail": str(exc)}
        elif action == "cutover-preview":
            out = preview_cutover(ctx.service)
        elif action == "cutover":
            out = execute_cutover(
                ctx.service,
                target=getattr(args, "target", None) or "mnemosyne_primary",
                confirm=bool(getattr(args, "confirm", False)),
                allow_dual=bool(getattr(args, "allow_dual", False)),
                reason=str(getattr(args, "reason", "") or ""),
                actor="node_cli",
            )
        else:
            out = {"ok": False, "error": f"unknown_action:{action}"}
    finally:
        ctx.db.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", True) is not False else 1


def cmd_adopt(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from pathlib import Path as _P

    from cognitive_memory.bootstrap import build_context
    from cognitive_memory.migration.export_interchange import (
        export_interchange_jsonl,
        memories_to_interchange,
    )

    data_dir = _effective_data_dir(args)
    ctx = build_context(data_dir=data_dir or None, token=args.token or "cli-adopt")
    action = str(getattr(args, "adopt_action", "") or "")
    try:
        if action == "export":
            limit = int(getattr(args, "limit", 500) or 500)
            out_path = str(getattr(args, "output", "") or "")
            if not out_path:
                root = _P(ctx.paths["root"])
                out_path = str(root / "exports" / "interchange-export.jsonl")
            records = memories_to_interchange(ctx.service, limit=limit)
            banks = getattr(args, "banks", "") or ""
            if banks.strip():
                allow = {b.strip() for b in banks.split(",") if b.strip()}
                records = [
                    r
                    for r in records
                    if str((r.extensions or {}).get("bank_id") or "personal") in allow
                ]
            out = export_interchange_jsonl(records, _P(out_path))
        else:
            out = {"ok": False, "error": f"unknown_action:{action}"}
    finally:
        ctx.db.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", True) is not False else 1


def cmd_component(args: argparse.Namespace) -> int:
    _ensure_src_path()
    from cognitive_memory.components.manager import ComponentManager
    from cognitive_memory.components.profiles import list_profiles

    data_dir = _effective_data_dir(args)
    if not data_dir:
        paths = _paths("")
        data_dir = str(paths["root"])
    mgr = ComponentManager(data_dir)
    action = str(getattr(args, "component_action", "") or "")
    out: dict[str, Any]
    if action == "list":
        out = {"items": mgr.list()}
    elif action == "profiles":
        out = {"profiles": list_profiles()}
    elif action == "inspect":
        out = mgr.inspect(args.source)
    elif action == "resolve":
        out = mgr.resolve(args.source)
    elif action == "install":
        out = mgr.install(
            args.source,
            force=bool(getattr(args, "force", False)),
            skip_resolve=bool(getattr(args, "skip_resolve", False)),
            authorize=bool(getattr(args, "authorize", False)),
        )
    elif action == "configure":
        raw = str(getattr(args, "config_json", "") or "{}")
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"bad_config_json: {exc}"}, ensure_ascii=False))
            return 1
        if not isinstance(cfg, dict):
            print(json.dumps({"ok": False, "error": "config_must_be_object"}, ensure_ascii=False))
            return 1
        out = mgr.configure(args.component_id, cfg)
    elif action == "authorize":
        out = mgr.authorize(args.component_id)
    elif action == "enable":
        out = mgr.enable(
            args.component_id,
            require_auth=bool(getattr(args, "require_auth", False)),
        )
    elif action == "disable":
        out = mgr.disable(args.component_id)
    elif action == "health":
        out = mgr.health(args.component_id)
    elif action == "remove":
        out = mgr.remove(args.component_id, purge=not bool(getattr(args, "keep_files", False)))
    else:
        out = {"ok": False, "error": f"unknown_action:{action}"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", True) is not False else 1


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from cognitive_memory import onboarding
    from cognitive_memory.storage.paths import default_data_root
    data_dir=args.data_dir or str(default_data_root())
    action=args.cmd
    try:
        if action=="discover":
            out={"ok":True,"manifest":onboarding.manifest(),"environment":onboarding.diagnose(data_dir,args.target)}
        elif action=="diagnose":
            out=onboarding.diagnose(data_dir,args.target)
        elif action=="plan":
            out=onboarding.plan_install(data_dir,target=args.target,upgrade=args.upgrade)
            if args.output:
                Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
        elif action in {"install","upgrade"}:
            plan=json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if action=="upgrade" and not plan.get("upgrade"):
                out={"ok":False,"detail":"upgrade_plan_required"}
            else:
                out=onboarding.install(plan,confirm=args.confirm)
        elif action=="verify":
            out=onboarding.verify(data_dir)
        elif action=="rollback":
            out=onboarding.rollback(data_dir,args.receipt,confirm=args.confirm)
        else:
            import httpx
            if not args.url.startswith(("http://","https://")):
                raise ValueError("http_url_required")
            response=httpx.get(args.url.rstrip("/")+"/api/v1/health",
                               headers={"Authorization":"Bearer "+args.token},timeout=10)
            response.raise_for_status()
            health=response.json()
            out={"ok":bool(health.get("ok")),"url":args.url,"configured":False}
            if out["ok"] and args.confirm:
                root=Path(data_dir).resolve()
                root.mkdir(parents=True,exist_ok=True)
                (root/"connection.json").write_text(json.dumps({"url":args.url,"token_env":"DSH_MEMORY_TOKEN"}),encoding="utf-8")
                out["configured"]=True
    except Exception as exc:
        out={"ok":False,"detail":type(exc).__name__,"message":str(exc)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out.get("ok",True) else 1


def cmd_memory_adoption(args: argparse.Namespace) -> int:
    from cognitive_memory.migration.manager import cli_action
    try:
        out=cli_action(args)
    except (ValueError,PermissionError,OSError) as exc:
        out={"ok":False,"detail":str(exc)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mnemosyne", description="Mnemosyne Node (DSH-independent)")
    _default_bank = (
        os.environ.get("MNEMOSYNE_MEMORY_BANK")
        or os.environ.get("MNEMOSYNE_DATA_DIR")
        or os.environ.get("DSH_MEMORY_DATA_DIR")
        or ""
    )
    parser.add_argument("--data-dir", default=_default_bank)
    parser.add_argument("--token", default=os.environ.get("DSH_MEMORY_TOKEN", ""))
    parser.add_argument("--config", default="", help="node.config.json / .yaml 路径")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_memory=sub.add_parser("memory")
    p_memory_sub=p_memory.add_subparsers(dest="memory_action",required=True)
    for action in ("discover","providers","inspect"):
        p_mem=p_memory_sub.add_parser(action)
        p_mem.add_argument("--json",action="store_true")
        if action!="providers":
            p_mem.add_argument("--source",default="." if action=="discover" else None,required=action=="inspect")
        if action=="inspect":
            p_mem.add_argument("--provider",required=True)
        p_mem.set_defaults(func=cmd_memory_adoption)
    p_migrate=p_memory_sub.add_parser("migrate")
    p_migrate_sub=p_migrate.add_subparsers(dest="migration_action",required=True)
    for action in ("plan","run","verify","history","rollback"):
        p_mig=p_migrate_sub.add_parser(action)
        p_mig.add_argument("--json",action="store_true")
        if action=="plan":
            p_mig.add_argument("--source",required=True)
            p_mig.add_argument("--provider",required=True)
        if action in {"run","verify","rollback"}:
            p_mig.add_argument("--batch",required=True)
        if action=="run":
            p_mig.add_argument("--checksum",required=True)
        if action in {"run","rollback"}:
            p_mig.add_argument("--confirm",action="store_true")
        p_mig.set_defaults(func=cmd_memory_adoption)
    for command in ("discover","diagnose","plan","install","connect","verify","upgrade","rollback"):
        p_boot=sub.add_parser(command)
        p_boot.add_argument("--json",action="store_true",help="Structured JSON (default)")
        if command in {"discover","diagnose","plan"}:
            p_boot.add_argument("--target",default="standalone")
        if command=="plan":
            p_boot.add_argument("-o","--output",default="")
            p_boot.add_argument("--upgrade",action="store_true")
        if command in {"install","upgrade"}:
            p_boot.add_argument("--plan",required=True)
        if command=="connect":
            p_boot.add_argument("--url",required=True)
        if command=="rollback":
            p_boot.add_argument("--receipt",required=True)
        if command in {"install","upgrade","rollback","connect"}:
            p_boot.add_argument("--confirm",action="store_true")
        p_boot.set_defaults(func=cmd_bootstrap)

    p_doc = sub.add_parser("doctor", help="本地自检（不依赖 DSH）")
    p_doc.set_defaults(func=cmd_doctor)

    p_st = sub.add_parser("status", help="查看 Node 进程状态")
    p_st.set_defaults(func=cmd_status)

    p_h = sub.add_parser("health", help="打开本地库并打印 health")
    p_h.set_defaults(func=cmd_health)

    p_cfg = sub.add_parser("config", help="配置 show / init / validate")
    p_cfg_sub = p_cfg.add_subparsers(dest="config_action", required=True)
    p_cfg_show = p_cfg_sub.add_parser("show")
    p_cfg_show.set_defaults(func=cmd_config)
    p_cfg_val = p_cfg_sub.add_parser("validate")
    p_cfg_val.set_defaults(func=cmd_config)
    p_cfg_init = p_cfg_sub.add_parser("init")
    p_cfg_init.add_argument("-o", "--output", default="")
    p_cfg_init.set_defaults(func=cmd_config)

    p_bak = sub.add_parser("backup", help="备份 memory.db")
    p_bak.add_argument("-o", "--output", default="")
    p_bak.set_defaults(func=cmd_backup)

    p_res = sub.add_parser("restore", help="从备份恢复（Node 运行中默认拒绝）")
    p_res.add_argument("backup")
    p_res.add_argument("--force", action="store_true")
    p_res.add_argument(
        "--force-running",
        action="store_true",
        help="允许在 Node 运行中覆盖（危险）",
    )
    p_res.set_defaults(func=cmd_restore)

    p_start = sub.add_parser("start", help="启动 Node HTTP（默认前台）")
    p_start.add_argument("--host", default="")
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--background", action="store_true")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="按 service.json pid 停止 Node")
    p_stop.set_defaults(func=cmd_stop)

    p_comp = sub.add_parser("component", help="D8 组件管理（本地源；安装≠授权）")
    p_comp_sub = p_comp.add_subparsers(dest="component_action", required=True)
    for action in (
        "list",
        "inspect",
        "resolve",
        "install",
        "configure",
        "authorize",
        "enable",
        "disable",
        "health",
        "remove",
        "profiles",
    ):
        p_a = p_comp_sub.add_parser(action)
        if action in ("inspect", "resolve", "install"):
            p_a.add_argument("source", help="本地目录或 manifest.json 路径")
        if action in ("configure", "authorize", "enable", "disable", "health", "remove"):
            p_a.add_argument("component_id")
        if action == "install":
            p_a.add_argument("--force", action="store_true")
            p_a.add_argument("--authorize", action="store_true", help="安装后立即授权（默认否）")
            p_a.add_argument("--skip-resolve", action="store_true")
        if action == "enable":
            p_a.add_argument("--require-auth", action="store_true")
        if action == "configure":
            p_a.add_argument("--config-json", default="{}", help='JSON 对象，如 \'{"k":1}\'')
        if action == "remove":
            p_a.add_argument("--keep-files", action="store_true")
        p_a.set_defaults(func=cmd_component)

    p_auth = sub.add_parser("authority", help="Authority 状态 / Cutover")
    p_auth_sub = p_auth.add_subparsers(dest="authority_action", required=True)
    p_auth_show = p_auth_sub.add_parser("show")
    p_auth_show.set_defaults(func=cmd_authority)
    p_auth_tr = p_auth_sub.add_parser("transition")
    p_auth_tr.add_argument("state")
    p_auth_tr.add_argument("--reason", default="")
    p_auth_tr.add_argument("--allow-dual", action="store_true")
    p_auth_tr.set_defaults(func=cmd_authority)
    p_auth_prev = p_auth_sub.add_parser("cutover-preview")
    p_auth_prev.set_defaults(func=cmd_authority)
    p_auth_cut = p_auth_sub.add_parser("cutover")
    p_auth_cut.add_argument("--target", default="mnemosyne_primary")
    p_auth_cut.add_argument("--confirm", action="store_true")
    p_auth_cut.add_argument("--allow-dual", action="store_true")
    p_auth_cut.add_argument("--reason", default="")
    p_auth_cut.set_defaults(func=cmd_authority)

    p_ad = sub.add_parser("adopt", help="Adoption 导出（Interchange）")
    p_ad_sub = p_ad.add_subparsers(dest="adopt_action", required=True)
    p_ad_ex = p_ad_sub.add_parser("export")
    p_ad_ex.add_argument("-o", "--output", default="")
    p_ad_ex.add_argument("--limit", type=int, default=500)
    p_ad_ex.add_argument("--banks", default="", help="逗号分隔 bank，空=默认多空间")
    p_ad_ex.set_defaults(func=cmd_adopt)

    p_bank = sub.add_parser(
        "memory-bank",
        help="Mnemosyne Memory Bank：与代码仓分离的数据目录状态 / 旧路径迁移",
    )
    p_bank_sub = p_bank.add_subparsers(dest="bank_action", required=True)
    p_bank_st = p_bank_sub.add_parser("status", help="对比新 Bank 与旧 DSH\\cognitive-memory")
    p_bank_st.add_argument("--to", default="", help="目标 Bank 根目录（默认新路径）")
    p_bank_st.add_argument("--source", default="", help="旧源目录（默认 legacy DSH 路径）")
    p_bank_st.set_defaults(func=cmd_memory_bank)
    p_bank_init = p_bank_sub.add_parser("init", help="创建空的 Memory Bank 布局")
    p_bank_init.add_argument("--to", default="", help="目标 Bank 根目录")
    p_bank_init.set_defaults(func=cmd_memory_bank)
    p_bank_mig = p_bank_sub.add_parser(
        "migrate-legacy",
        help="从 %%LOCALAPPDATA%%\\DSH\\cognitive-memory 复制到 Memory Bank（不删源）",
    )
    p_bank_mig.add_argument("--to", default="", help="目标 Bank 根目录")
    p_bank_mig.add_argument("--source", default="", help="旧源目录")
    p_bank_mig.add_argument("--overwrite", action="store_true")
    p_bank_mig.set_defaults(func=cmd_memory_bank)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
