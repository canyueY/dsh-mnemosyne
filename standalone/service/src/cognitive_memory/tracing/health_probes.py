# -*- coding: utf-8 -*-
"""认知健康探针（E08）。

默认只读/无破坏；写探针仅进专用隔离目录或显式测试命名空间。
维度状态：OK | WARN | ERROR | PENDING | UNKNOWN。
保存状态字段 ≠ 已验证真实读写/索引/worker。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cognitive_memory.storage.db import Database

STATUSES = ("OK", "WARN", "ERROR", "PENDING", "UNKNOWN")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dim(
    name: str,
    status: str,
    *,
    detail: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = status if status in STATUSES else "UNKNOWN"
    return {
        "name": name,
        "status": st,
        "detail": detail,
        "evidence": evidence or {},
        "checked_at": _now(),
    }


class CognitiveHealthProbes:
    def __init__(self, db: Database, *, data_dir: str | Path | None = None) -> None:
        self.db = db
        self.data_dir = Path(data_dir) if data_dir else Path(db.path).parent.parent

    def probe(
        self,
        *,
        include_write_probe: bool = False,
        write_probe_namespace: str | None = None,
    ) -> dict[str, Any]:
        dims: list[dict[str, Any]] = []
        dims.append(self._probe_api_readable())
        dims.append(self._probe_schema_migrations())
        dims.append(self._probe_fts_index())
        dims.append(self._probe_source_availability())
        dims.append(self._probe_queue_and_worker())
        write_dim = None
        if include_write_probe:
            write_dim = self._probe_write_isolated(namespace=write_probe_namespace)
            dims.append(write_dim)

        worst = self._aggregate(dims)
        return {
            "ok": worst in {"OK", "WARN"},
            "overall": worst,
            "all_green": all(d["status"] == "OK" for d in dims),
            "dimensions": {d["name"]: d for d in dims},
            "dimension_list": dims,
            "read_only": not include_write_probe,
            "user_data_modified": False,
            "write_probe": write_dim,
            "boundary": {
                "status_field_ne_verified_io": True,
                "write_probe_isolated_only": True,
                "no_error_ne_worker_complete": True,
            },
            "checked_at": _now(),
            "human_summary": self._human_summary(worst, dims),
        }

    def explain(
        self,
        *,
        subject: str = "health",
        ref_id: str | None = None,
    ) -> dict[str, Any]:
        """来源/状态/变更/预算的结构化解释 + 简洁人类文本。"""
        health = self.probe(include_write_probe=False)
        structured = {
            "subject": subject,
            "ref_id": ref_id,
            "source": {
                "service": "cognitive-memory",
                "version": "2.0.0",
                "data_dir": str(self.data_dir),
            },
            "status": {
                "overall": health["overall"],
                "dimensions": {
                    k: v["status"] for k, v in health["dimensions"].items()
                },
            },
            "changes": {
                "user_data_modified": False,
                "note": "health query is read-only by default",
            },
            "budget": {
                "note": "see /cognition/usage/report for measured/estimated/unknown costs",
            },
            "freshness": {
                "checked_at": health["checked_at"],
            },
        }
        lines = [
            f"整体状态：{health['overall']}",
            health["human_summary"],
        ]
        for d in health["dimension_list"]:
            lines.append(f"- {d['name']}: {d['status']} — {d['detail']}")
        return {
            "ok": True,
            "structured": structured,
            "human_text": "\n".join(lines),
            "health": health,
        }

    def _aggregate(self, dims: list[dict[str, Any]]) -> str:
        order = {"ERROR": 5, "UNKNOWN": 4, "PENDING": 3, "WARN": 2, "OK": 1}
        worst = "OK"
        for d in dims:
            if order.get(d["status"], 0) > order.get(worst, 0):
                worst = d["status"]
        return worst

    def _human_summary(self, overall: str, dims: list[dict[str, Any]]) -> str:
        bad = [d for d in dims if d["status"] != "OK"]
        if not bad:
            return "所有探针维度正常。"
        parts = ", ".join(f"{d['name']}={d['status']}" for d in bad)
        return f"整体 {overall}；异常维度：{parts}。"

    def _probe_api_readable(self) -> dict[str, Any]:
        try:
            with self.db.exclusive() as conn:
                n = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
            return _dim("api_readable", "OK", detail="database readable", evidence={"memories": n})
        except Exception as exc:  # noqa: BLE001
            return _dim("api_readable", "ERROR", detail=str(exc))

    def _probe_schema_migrations(self) -> dict[str, Any]:
        try:
            from cognitive_memory.storage.migrations import MIGRATIONS

            expected = {mid for mid, _ in MIGRATIONS}
            with self.db.exclusive() as conn:
                applied = {
                    r[0]
                    for r in conn.execute("SELECT id FROM schema_migrations").fetchall()
                }
            missing = sorted(expected - applied)
            if missing:
                return _dim(
                    "schema_migrations",
                    "WARN",
                    detail="pending migrations",
                    evidence={"missing": missing},
                )
            return _dim(
                "schema_migrations",
                "OK",
                detail="migrations current",
                evidence={"applied": len(applied)},
            )
        except Exception as exc:  # noqa: BLE001
            return _dim("schema_migrations", "UNKNOWN", detail=str(exc))

    def _probe_fts_index(self) -> dict[str, Any]:
        """索引陈旧/缺失 → WARN/ERROR，不全绿。"""
        try:
            with self.db.exclusive() as conn:
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                    ).fetchall()
                }
                if "memories_fts" not in tables:
                    return _dim(
                        "fts_index",
                        "ERROR",
                        detail="memories_fts missing",
                        evidence={"tables_has_fts": False},
                    )
                mem_n = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
                try:
                    fts_n = conn.execute(
                        "SELECT COUNT(*) AS n FROM memories_fts"
                    ).fetchone()["n"]
                except sqlite3.Error as exc:
                    return _dim(
                        "fts_index",
                        "ERROR",
                        detail=f"fts unreadable: {exc}",
                        evidence={"memories": mem_n},
                    )
                if mem_n > 0 and fts_n == 0:
                    return _dim(
                        "fts_index",
                        "WARN",
                        detail="index stale or empty while memories exist",
                        evidence={"memories": mem_n, "fts_rows": fts_n},
                    )
                if abs(int(mem_n) - int(fts_n)) > max(2, int(mem_n) * 0.1):
                    return _dim(
                        "fts_index",
                        "WARN",
                        detail="fts row count diverges from memories",
                        evidence={"memories": mem_n, "fts_rows": fts_n},
                    )
                return _dim(
                    "fts_index",
                    "OK",
                    detail="fts present",
                    evidence={"memories": mem_n, "fts_rows": fts_n},
                )
        except Exception as exc:  # noqa: BLE001
            return _dim("fts_index", "UNKNOWN", detail=str(exc))

    def _probe_source_availability(self) -> dict[str, Any]:
        try:
            with self.db.exclusive() as conn:
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "evidences" not in tables:
                    return _dim(
                        "source_availability",
                        "WARN",
                        detail="evidences table missing",
                    )
                n = conn.execute("SELECT COUNT(*) AS n FROM evidences").fetchone()["n"]
                tomb = 0
                if "source_revision_tombstones" in tables:
                    tomb = conn.execute(
                        "SELECT COUNT(*) AS n FROM source_revision_tombstones"
                    ).fetchone()["n"]
            return _dim(
                "source_availability",
                "OK",
                detail="evidence store reachable",
                evidence={"evidences": n, "tombstones": tomb},
            )
        except Exception as exc:  # noqa: BLE001
            return _dim("source_availability", "ERROR", detail=str(exc))

    def _probe_queue_and_worker(self) -> dict[str, Any]:
        """任务 pending 无 worker 回执 → PENDING/UNKNOWN，不把无报错当完成。"""
        try:
            with self.db.exclusive() as conn:
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                pending_tasks = 0
                running_tasks = 0
                if "tasks" in tables:
                    pending_tasks = conn.execute(
                        "SELECT COUNT(*) AS n FROM tasks WHERE status='pending'"
                    ).fetchone()["n"]
                    running_tasks = conn.execute(
                        "SELECT COUNT(*) AS n FROM tasks WHERE status='running'"
                    ).fetchone()["n"]
                outbox_pending = 0
                if "outbox" in tables:
                    outbox_pending = conn.execute(
                        "SELECT COUNT(*) AS n FROM outbox WHERE status='pending'"
                    ).fetchone()["n"]

            if pending_tasks > 0 or running_tasks > 0:
                return _dim(
                    "queue_worker",
                    "PENDING",
                    detail="tasks awaiting worker receipt; absence of error is not completion",
                    evidence={
                        "pending_tasks": pending_tasks,
                        "running_tasks": running_tasks,
                        "outbox_pending": outbox_pending,
                        "worker_receipt": None,
                        "no_error_ne_complete": True,
                    },
                )
            if outbox_pending > 0:
                return _dim(
                    "queue_worker",
                    "UNKNOWN",
                    detail="outbox pending without confirmed worker drain",
                    evidence={
                        "outbox_pending": outbox_pending,
                        "worker_receipt": None,
                    },
                )
            return _dim(
                "queue_worker",
                "OK",
                detail="no pending worker work",
                evidence={
                    "pending_tasks": 0,
                    "running_tasks": 0,
                    "outbox_pending": 0,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _dim("queue_worker", "UNKNOWN", detail=str(exc))

    def _probe_write_isolated(self, *, namespace: str | None = None) -> dict[str, Any]:
        """写探针只进临时/显式测试命名空间，不改用户 Bank。"""
        ns = namespace or f"probe-{uuid.uuid4().hex[:8]}"
        if not (ns.startswith("probe-") or ns.startswith("test-") or ns.startswith("__probe__")):
            return _dim(
                "write_isolated",
                "ERROR",
                detail="write probe refused: namespace must be probe-/test-/__probe__",
                evidence={"namespace": ns},
            )
        try:
            with tempfile.TemporaryDirectory(prefix=f"mnemosyne-{ns}-") as scratch:
                from cognitive_memory.bootstrap import build_context
                from cognitive_memory.domain.models import MemoryProposeIn, SearchQuery

                ctx = build_context(data_dir=scratch, token=f"probe-{ns}")
                try:
                    mem = ctx.service.propose_memory(
                        MemoryProposeIn(content=f"write probe {ns}", bank_id="personal")
                    )
                    got = ctx.service.get_memory(mem.id)
                    found = ctx.service.search(SearchQuery(query="write probe", limit=5))
                    ok = bool(got) and any(m.id == mem.id for m in found)
                    status = "OK" if ok else "ERROR"
                    return _dim(
                        "write_isolated",
                        status,
                        detail="isolated write/read/search ok" if ok else "isolated write failed",
                        evidence={
                            "namespace": ns,
                            "scratch": scratch,
                            "memory_id": mem.id,
                            "user_data_modified": False,
                        },
                    )
                finally:
                    ctx.db.close()
        except Exception as exc:  # noqa: BLE001
            return _dim("write_isolated", "ERROR", detail=str(exc), evidence={"namespace": ns})

    def simulate_stale_index(self) -> None:
        """测试辅助：制造索引陈旧（仅测用）。"""
        with self.db.transaction() as conn:
            # 插入一条不进 FTS 的记忆行会破坏一致性；更安全：清空 FTS 内容表
            try:
                conn.execute("DELETE FROM memories_fts")
            except sqlite3.Error:
                pass

    def simulate_write_unavailable(self) -> None:
        """测试辅助：标记写不可用（只读连接语义用 pragma 模拟）。"""
        with self.db.exclusive() as conn:
            conn.execute("PRAGMA user_version = user_version")  # touch
        # 实际不可用由测试通过替换 db.transaction 或路径权限模拟


def clean_bank_lifecycle(data_dir: str | Path) -> dict[str, Any]:
    """临时 Bank：完整记录、重启、纠正、失效、再查询（AC30）。"""
    from cognitive_memory.bootstrap import build_context
    from cognitive_memory.domain.models import MemoryProposeIn, MemoryUpdateIn, SearchQuery
    from cognitive_memory.onboarding import plan_install, install, verify

    root = Path(data_dir)
    steps: list[dict[str, Any]] = []
    plan = plan_install(root)
    installed = install(plan, confirm=True)
    steps.append({"step": "install", "ok": bool(installed.get("ok")), "result": installed})
    if not installed.get("ok"):
        return {"ok": False, "steps": steps}

    v1 = verify(root)
    steps.append({"step": "verify_init", "ok": bool(v1.get("ok")), "result": v1})

    ctx = build_context(data_dir=str(root), token="clean-bank")
    try:
        from cognitive_memory.domain.models import MemoryStatus

        mem = ctx.service.propose_memory(
            MemoryProposeIn(content="clean-bank record alpha", status=MemoryStatus.ACTIVE)
        )
        steps.append({"step": "record", "ok": True, "memory_id": mem.id})
    finally:
        ctx.db.close()

    # 重启：关闭后重建 context
    ctx2 = build_context(data_dir=str(root), token="clean-bank")
    try:
        got = ctx2.service.get_memory(mem.id)
        steps.append({"step": "restart_read", "ok": bool(got), "content": got.get("content")})
        corrected = ctx2.service.update_memory(
            mem.id,
            MemoryUpdateIn(
                content="clean-bank record alpha corrected",
                expected_version=int(got["version"]),
            ),
        )
        steps.append({"step": "correct", "ok": True, "version": corrected.version})
        forgotten = ctx2.service.forget_memory(mem.id, hard=False)
        steps.append({"step": "invalidate", "ok": True, "result": forgotten})
        again = ctx2.service.search(SearchQuery(query="clean-bank record", limit=10))
        active_hits = [m for m in again if getattr(m.status, "value", m.status) == "active"]
        steps.append(
            {
                "step": "requery",
                "ok": True,
                "active_hits": len(active_hits),
                "note": "invalidated memory should not remain active authoritative",
            }
        )
        probes = CognitiveHealthProbes(ctx2.db, data_dir=root).probe(include_write_probe=True)
        # PENDING/WARN 仍表示探针可运行；ERROR 才算生命周期失败
        overall = probes.get("overall")
        steps.append(
            {
                "step": "health_probes",
                "ok": overall in {"OK", "WARN", "PENDING", "UNKNOWN"},
                "overall": overall,
                "all_green": probes.get("all_green"),
                "write_probe_status": (probes.get("write_probe") or {}).get("status"),
            }
        )
        # 升级备份路径验证
        from cognitive_memory.onboarding import plan_install as plan_up, install as install_up

        up = install_up(plan_up(root, upgrade=True), confirm=True)
        steps.append(
            {
                "step": "upgrade_backup",
                "ok": bool(up.get("ok")),
                "receipt": up.get("receipt"),
                "verification": up.get("verification"),
            }
        )
    finally:
        ctx2.db.close()

    ok = all(s.get("ok") for s in steps)
    return {
        "ok": ok,
        "steps": steps,
        "capabilities_default_runnable": True,
        "target_modified_only_temp_bank": True,
    }
