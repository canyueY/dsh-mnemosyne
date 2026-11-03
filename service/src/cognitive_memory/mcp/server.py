# -*- coding: utf-8 -*-
"""MCP stdio 工具入口 — 全部委托 MemoryService（无第二套逻辑）。

写工具须 confirm=true；memory_observe / memory_import_preview 已纳入。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from cognitive_memory.core.service import ConflictError, MemoryService, NotFoundError
from cognitive_memory.domain.models import (
    ContextBuildIn,
    FeedbackIn,
    MemoryProposeIn,
    MemoryStatus,
    MemoryType,
    MemoryUpdateIn,
    ObservationIn,
    SearchQuery,
    SourceKind,
)


TOOLS = [
    {
        "name": "memory_search",
        "description": "搜索相关记忆（FTS5，可降级 LIKE）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "user_id": {"type": "string", "minLength": 1},
                "agent_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "banks": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["current", "historical", "any"]},
                "limit": {"type": "integer"},
                "types": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_get",
        "description": "获取记忆详情与证据",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "include_evidence": {"type": "boolean"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "memory_context",
        "description": "按 Token 预算组装当前对话上下文",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "token_budget": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_observe",
        "description": "写入观察（须 confirm=true）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "source_kind": {"type": "string"},
                "producer": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["content", "confirm"],
        },
    },
    {
        "name": "memory_import_preview",
        "description": "Interchange 导入预览（Staging，不 Commit）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "records": {"type": "array", "items": {"type": "object"}},
                "dedupe": {"type": "boolean"},
            },
            "required": ["records"],
        },
    },
    {
        "name": "memory_propose",
        "description": "提交候选记忆（须 confirm=true）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "type": {"type": "string"},
                "source_ids": {"type": "array", "items": {"type": "string"}},
                "confirm": {"type": "boolean"},
            },
            "required": ["content", "confirm"],
        },
    },
    {
        "name": "memory_feedback",
        "description": "反馈记忆是否有用或有误",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "useful": {"type": "boolean"},
                "incorrect": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "memory_update",
        "description": "更新已有记忆（须 confirm=true）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
                "status": {"type": "string"},
                "expected_version": {"type": "integer"},
                "confirm": {"type": "boolean"},
            },
            "required": ["id", "confirm"],
        },
    },
    {
        "name": "memory_forget",
        "description": "删除或使记忆失效（须 confirm=true；hard 另需 allow_hard）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "hard": {"type": "boolean"},
                "allow_hard": {"type": "boolean"},
                "confirm": {"type": "boolean"},
            },
            "required": ["id", "confirm"],
        },
    },
    {
        "name": "memory_task_status",
        "description": "查询后台任务",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "memory_health",
        "description": "服务健康检查",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _dispatch(service: MemoryService, name: str, args: dict[str, Any]) -> Any:
    if name == "memory_search":
        types = None
        if args.get("types"):
            types = [MemoryType(t) for t in args["types"]]
        rows = service.search(
            SearchQuery(query=args.get("query") or "", limit=int(args.get("limit") or 10), types=types,
                        user_id=args.get("user_id"), agent_id=args.get("agent_id"),
                        run_id=args.get("run_id"), banks=args.get("banks"), mode=args.get("mode") or "current")
        )
        return {"items": [m.model_dump() for m in rows]}
    if name == "memory_get":
        return service.get_memory(args["id"], include_evidence=bool(args.get("include_evidence")))
    if name == "memory_context":
        return service.build_context(
            ContextBuildIn(query=args["query"], token_budget=args.get("token_budget"))
        ).model_dump()
    if name == "memory_observe":
        if not bool(args.get("confirm")):
            raise ValueError("confirm_required: memory_observe requires confirm=true")
        from cognitive_memory.ingestion import ingest_observation

        sk = args.get("source_kind") or "system"
        try:
            kind = SourceKind(sk)
        except Exception:
            kind = SourceKind.SYSTEM
        obs = ingest_observation(
            service,
            ObservationIn(
                content=str(args.get("content") or ""),
                source_kind=kind,
                producer=str(args.get("producer") or "mcp"),
            ),
        )
        return obs.model_dump()
    if name == "memory_import_preview":
        from cognitive_memory.migration.staging import preview_batch

        records = list(args.get("records") or [])
        batch = preview_batch(records, dedupe=bool(args.get("dedupe", True)))
        return {
            "batch_id": batch.batch_id,
            "state": batch.state,
            "count": len(batch.records),
            "checksum": batch.checksum,
            "dedupe_report": batch.dedupe_report,
            "note": "preview only; approve+commit required",
        }
    if name == "memory_propose":
        if not bool(args.get("confirm")):
            raise ValueError("confirm_required: memory_propose requires confirm=true")
        return service.propose_memory(
            MemoryProposeIn(
                content=args["content"],
                type=MemoryType(args["type"]) if args.get("type") else MemoryType.SEMANTIC,
                source_ids=list(args.get("source_ids") or []),
            )
        ).model_dump()
    if name == "memory_feedback":
        return service.feedback(
            FeedbackIn(
                memory_id=args["memory_id"],
                useful=args.get("useful"),
                incorrect=args.get("incorrect"),
                note=args.get("note") or "",
            )
        )
    if name == "memory_update":
        if not bool(args.get("confirm")):
            raise ValueError("confirm_required: memory_update requires confirm=true")
        return service.update_memory(
            args["id"],
            MemoryUpdateIn(
                content=args.get("content"),
                status=MemoryStatus(args["status"]) if args.get("status") else None,
                expected_version=args.get("expected_version"),
            ),
        ).model_dump()
    if name == "memory_forget":
        if not bool(args.get("confirm")):
            raise ValueError("confirm_required: memory_forget requires confirm=true")
        hard = bool(args.get("hard"))
        if hard and not bool(args.get("allow_hard")):
            raise ValueError(
                "hard_delete_denied: pass allow_hard=true (HTTP also needs MEMORY_HARD_DELETE)"
            )
        return service.forget_memory(args["id"], hard=hard)
    if name == "memory_task_status":
        return service.get_task(args["id"]).model_dump()
    if name == "memory_health":
        return service.health()
    raise KeyError(f"unknown tool: {name}")


def run_mcp_stdio(service: MemoryService) -> None:
    """极简 MCP JSON-RPC over stdio（服务进程内入口；DSH 宿主优先用 REST 网关）。"""
    initialized = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        try:
            if method == "initialize":
                initialized = True
                result = {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "mnemosyne", "version": "2.0.0"},
                    "capabilities": {"tools": {}},
                }
            elif method == "ping":
                result = {}
            elif not initialized:
                raise PermissionError("not initialized; call initialize first")
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise TypeError("invalid params: arguments must be object")
                data = _dispatch(service, name, arguments)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
            else:
                raise LookupError(f"method not found: {method}")
            resp = {"jsonrpc": "2.0", "id": rid, "result": result}
        except PermissionError as exc:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": str(exc)}}
        except LookupError as exc:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": str(exc)}}
        except (TypeError, KeyError) as exc:
            # KeyError from unknown tool → -32601; TypeError invalid params → -32602
            code = -32601 if isinstance(exc, KeyError) else -32602
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": str(exc)}}
        except (NotFoundError, ConflictError, ValueError) as exc:
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32000, "message": str(exc)},
            }
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
