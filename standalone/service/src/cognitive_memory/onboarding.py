"""Local bootstrap lifecycle. Planning is read-only; mutation requires a bound plan."""
import hashlib
import importlib.util
import importlib.metadata
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path

MANIFEST_VERSION="mnemosyne.bootstrap.v1"


def __getattr__(name):
    if name=="BootstrapManifest":
        from cognitive_memory.onboarding_contract import BootstrapManifest
        return BootstrapManifest
    raise AttributeError(name)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def source_digest():
    root=Path(__file__).parent
    return digest({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.rglob("*.py"))})


def manifest():
    return {
        "manifest_version":MANIFEST_VERSION,"id":"mnemosyne.core-node","name":"Mnemosyne","version":"3.0.0",
        "source":{"type":"local_checkout","trust":"requires_explicit_local_approval"},
        "compatibility":{"python":">=3.10","targets":["standalone","dsh","mcp","rest"]},
        "dependencies":["fastapi","uvicorn","pydantic","httpx"],
        "permissions":["create_data_directory","write_node_config","migrate_database_on_upgrade"],
        "installation":{"mode":"configure_existing_python_runtime","entry":"mnemosyne install --plan FILE --confirm",
                        "skill":"resources/onboarding/SKILL.md"},
        "integration":{"protocols":["rest","mcp"],"dsh_adapter":"../src"},
        "lifecycle":["discover","diagnose","plan","install","connect","verify","upgrade","rollback"],
        "recovery":{"database":"sqlite online backup","configuration":"byte-exact snapshot"},
        "capabilities":["memory","evidence","claims","governance","cognitive_task_loop","validated_schema_learning","profile_hypotheses"],
        "checksums":{"source_sha256":source_digest()},"signatures":[],
    }


def diagnose(data_dir, target="standalone"):
    root=Path(data_dir).expanduser().resolve()
    parent=root
    while not parent.exists() and parent!=parent.parent:
        parent=parent.parent
    checks=[{"id":"python","status":"passed" if sys.version_info>=(3,10) else "failed"},
            {"id":"data_parent_writable","status":"passed" if os.access(parent,os.W_OK) else "failed"}]
    minimums={"fastapi":(0,115,0),"uvicorn":(0,30,0),"pydantic":(2,7,0),"httpx":(0,27,0)}
    for dep,minimum in minimums.items():
        try:
            import re
            installed=importlib.metadata.version(dep)
            matched=re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?",installed)
            good=bool(matched) and tuple(map(int,matched.groups()))>=minimum
        except importlib.metadata.PackageNotFoundError:
            installed=None;good=False
        checks.append({"id":dep,"status":"passed" if good else "failed","installed":installed,
                       "minimum":".".join(map(str,minimum))})
    checks.append({"id":"node_runtime","status":("passed" if shutil.which("node") else "failed") if target=="dsh" else "not_applicable"})
    checks.append({"id":"existing_database","status":"passed" if (root/"data/memory.db").is_file() else "not_applicable"})
    return {"ok":all(c["status"]!="failed" for c in checks),"target":target,
            "data_dir":str(root),"checks":checks,"read_only":True}


def _hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def plan_install(data_dir, *, target="standalone", upgrade=False):
    if target not in manifest()["compatibility"]["targets"]:
        raise ValueError("unsupported_target")
    report=diagnose(data_dir,target)
    root=Path(report["data_dir"])
    plan={"version":MANIFEST_VERSION,"data_dir":str(root),"target":target,
          "source_sha256":source_digest(),"config_before":_hash_file(root/"node.config.json"),
          "database_exists":(root/"data/memory.db").is_file(),"upgrade":bool(upgrade),
          "compatibility":report,
          "actions":["backup_existing_state","initialize_layout","write_config","apply_migrations","verify"],
          "permissions":manifest()["permissions"]}
    plan["plan_hash"]=digest(plan)
    return plan


def node_running(root):
    marker=Path(root)/"runtime/service.json"
    if not marker.is_file():
        return False
    try:
        pid=int(json.loads(marker.read_text(encoding="utf-8")).get("pid") or 0)
        if pid<=0:
            return True
        if os.name=="nt":
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if handle:
                kernel.CloseHandle(handle)
                return True
            return ctypes.get_last_error()!=87  # Access denied is not proof of absence.
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError,ValueError,json.JSONDecodeError):
        return True


def verify(data_dir):
    """Check target read-only and exercise the code in a separate disposable bank."""
    from cognitive_memory.bootstrap import build_context
    from cognitive_memory.domain.models import MemoryProposeIn, SearchQuery
    root=Path(data_dir).resolve()
    path=root/"data/memory.db"
    checks=[]
    if path.is_file():
        conn=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
        try:
            checks.append({"id":"target_integrity","status":"passed" if conn.execute("PRAGMA integrity_check").fetchone()[0]=="ok" else "failed"})
        finally:
            conn.close()
    else:
        checks.append({"id":"target_database","status":"not_checked"})
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        ctx=build_context(data_dir=scratch,token="bootstrap-verification")
        try:
            mem=ctx.service.propose_memory(MemoryProposeIn(content="bootstrap persistence probe"))
            found=ctx.service.get_memory(mem.id)
            checks.append({"id":"scratch_persistence","status":"passed" if found else "failed"})
            ctx.service.propose_memory(MemoryProposeIn(content="bootstrap shared probe",bank_id="shared"))
            items=ctx.service.search(SearchQuery(query="",limit=50))
            checks.append({"id":"scratch_bank_isolation","status":"passed" if all(m.bank_id=="personal" for m in items) else "failed"})
            backup=Path(scratch)/"verified-backup.db"
            dest=sqlite3.connect(backup)
            try:
                ctx.db.connect().backup(dest)
                checks.append({"id":"scratch_backup","status":"passed" if dest.execute("PRAGMA integrity_check").fetchone()[0]=="ok" else "failed"})
            finally:
                dest.close()
        finally:
            ctx.db.close()
    return {"ok":all(c["status"]=="passed" for c in checks),"checks":checks,
            "target_modified":False,"scope":"local runtime; remote adapter handshake not asserted"}


def install(plan, *, confirm=False):
    if not confirm:
        return {"ok":False,"detail":"approval_required"}
    unsigned={k:v for k,v in plan.items() if k!="plan_hash"}
    if plan.get("plan_hash")!=digest(unsigned) or plan.get("source_sha256")!=source_digest():
        return {"ok":False,"detail":"plan_or_source_changed"}
    root=Path(plan["data_dir"]).resolve()
    if node_running(root):
        return {"ok":False,"detail":"stop_node_before_install"}
    if _hash_file(root/"node.config.json")!=plan.get("config_before"):
        return {"ok":False,"detail":"configuration_changed"}
    db_path=root/"data/memory.db"
    if db_path.is_file() and not plan.get("upgrade"):
        return {"ok":False,"detail":"existing_bank_requires_upgrade_plan"}
    if not diagnose(root,plan["target"])["ok"]:
        return {"ok":False,"detail":"incompatible_environment"}
    receipt_id=uuid.uuid4().hex
    backup=root/"backups"/receipt_id
    backup.mkdir(parents=True)
    receipt={"id":receipt_id,"data_dir":str(root),"had_database":db_path.is_file(),
             "had_config":(root/"node.config.json").is_file(),"plan_hash":plan["plan_hash"]}
    if receipt["had_config"]:
        shutil.copy2(root/"node.config.json",backup/"node.config.json")
    if receipt["had_database"]:
        source=sqlite3.connect(db_path.as_uri()+"?mode=ro",uri=True)
        dest=sqlite3.connect(backup/"memory.db")
        try:
            source.backup(dest)
        finally:
            source.close();dest.close()
    (backup/"receipt.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    try:
        from cognitive_memory.storage.paths import ensure_layout
        from cognitive_memory.node_config import write_default_config
        from cognitive_memory.storage.db import Database
        ensure_layout(root)
        if not receipt["had_config"]:
            write_default_config(root/"node.config.json",data_dir=str(root))
        db=Database(db_path)
        try:
            db.ensure_schema()
        finally:
            db.close()
        report=verify(root)
        if not report["ok"]:
            raise RuntimeError("functional_verification_failed")
        return {"ok":True,"receipt":receipt_id,"verification":report,"data_dir":str(root)}
    except Exception:
        restored=rollback(root,receipt_id,confirm=True)
        return {"ok":False,"detail":"installation_failed","recovery":restored}


def rollback(data_dir,receipt_id,*,confirm=False):
    if not confirm:
        return {"ok":False,"detail":"approval_required"}
    if len(receipt_id)!=32 or any(c not in "0123456789abcdef" for c in receipt_id):
        return {"ok":False,"detail":"invalid_receipt"}
    root=Path(data_dir).resolve()
    if node_running(root):
        return {"ok":False,"detail":"stop_node_before_rollback"}
    backup=root/"backups"/receipt_id
    receipt=json.loads((backup/"receipt.json").read_text(encoding="utf-8"))
    if Path(receipt["data_dir"]).resolve()!=root:
        return {"ok":False,"detail":"receipt_scope_mismatch"}
    if receipt["had_database"]:
        src=sqlite3.connect((backup/"memory.db").as_uri()+"?mode=ro",uri=True)
        dst=sqlite3.connect(root/"data/memory.db")
        try:
            src.backup(dst)
        finally:
            src.close();dst.close()
    else:
        # Keep a failed fresh bank for inspection rather than deleting its files.
        db=root/"data/memory.db"
        if db.exists():
            shutil.copy2(db,backup/"failed-fresh-memory.db")
    if receipt["had_config"]:
        shutil.copy2(backup/"node.config.json",root/"node.config.json")
    return {"ok":True,"receipt":receipt_id,"restored_existing_database":receipt["had_database"],
            "fresh_bank_retained":not receipt["had_database"]}
