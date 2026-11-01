#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E15：本地构建包 → 临时 venv → import/CLI 最小闭环（不改生产解释器）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = SERVICE_ROOT / "reports"


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
        "ok": proc.returncode == 0,
    }


def main() -> int:
    steps: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="mnemosyne-clean-install-") as scratch:
        scratch_p = Path(scratch)
        build_dir = scratch_p / "build"
        venv_dir = scratch_p / "venv"
        bank_dir = scratch_p / "bank"
        build_dir.mkdir()
        bank_dir.mkdir()

        # 1 build wheel from local service source
        build = _run(
            [sys.executable, "-m", "pip", "wheel", ".", "-w", str(build_dir), "--no-deps"],
            cwd=SERVICE_ROOT,
        )
        steps.append({"step": "build_wheel", **build})
        wheels = list(build_dir.glob("mnemosyne-*.whl"))
        if not wheels:
            # setuptools may name package differently
            wheels = list(build_dir.glob("*.whl"))
        if not wheels:
            report = {"ok": False, "steps": steps, "error": "wheel_missing"}
            _write(report)
            return 1
        wheel = wheels[0]
        steps.append({"step": "wheel_found", "ok": True, "path": str(wheel), "name": wheel.name})

        # 2 fresh venv
        venv.create(venv_dir, with_pip=True, clear=True)
        if os.name == "nt":
            py = venv_dir / "Scripts" / "python.exe"
            pip = venv_dir / "Scripts" / "pip.exe"
        else:
            py = venv_dir / "bin" / "python"
            pip = venv_dir / "bin" / "pip"
        steps.append({"step": "venv_create", "ok": py.is_file(), "python": str(py)})

        # 3 install wheel + minimal runtime deps
        install = _run(
            [
                str(pip),
                "install",
                str(wheel),
                "fastapi>=0.115.0",
                "uvicorn[standard]>=0.30.0",
                "pydantic>=2.7.0",
                "pydantic-settings>=2.3.0",
                "httpx>=0.27.0",
            ]
        )
        steps.append({"step": "pip_install_wheel", **install})
        if not install["ok"]:
            report = {"ok": False, "steps": steps, "error": "install_failed"}
            _write(report)
            return 1

        # 4 import + version
        check_import = _run(
            [
                str(py),
                "-c",
                "import cognitive_memory; import cognitive_memory.bootstrap as b; "
                "print(getattr(cognitive_memory, '__version__', '2.0.0'))",
            ]
        )
        steps.append({"step": "import_check", **check_import})

        # 5 minimal closed loop in temp bank
        loop_code = f"""
from pathlib import Path
from cognitive_memory.bootstrap import build_context
from cognitive_memory.domain.models import MemoryProposeIn, MemoryStatus, MemoryUpdateIn
root = Path(r"{bank_dir}")
ctx = build_context(data_dir=str(root), token="clean-install")
try:
    mem = ctx.service.propose_memory(MemoryProposeIn(content="clean-install-alpha", status=MemoryStatus.ACTIVE))
    mid = mem.id
finally:
    ctx.db.close()
ctx2 = build_context(data_dir=str(root), token="clean-install")
try:
    got = ctx2.service.get_memory(mid)
    assert got["content"] == "clean-install-alpha"
    upd = ctx2.service.update_memory(mid, MemoryUpdateIn(content="clean-install-beta", expected_version=got["version"]))
    assert upd.content == "clean-install-beta"
    print("OK", mid, upd.version)
finally:
    ctx2.db.close()
"""
        loop = _run([str(py), "-c", loop_code])
        steps.append({"step": "min_closed_loop", **loop})

        ok = all(s.get("ok") for s in steps if "ok" in s) and loop["ok"] and check_import["ok"]
        report = {
            "ok": ok,
            "install_source": str(wheel),
            "wheel_name": wheel.name,
            "venv": str(venv_dir),
            "bank": str(bank_dir),
            "python_used_for_build": sys.executable,
            "production_interpreter_modified": False,
            "user_effect": "not_measured",
            "steps": steps,
        }
        _write(report)
        print(json.dumps({"ok": ok, "wheel": wheel.name}, ensure_ascii=False))
        return 0 if ok else 1


def _write(report: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "clean_install.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # also mirror into engineering pack reports if present
    pack = SERVICE_ROOT.parent / "engineering" / "mnemosyne-next" / "reports"
    if pack.is_dir():
        (pack / "clean_install.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
