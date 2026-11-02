# -*- coding: utf-8 -*-
"""入口：启动 REST（127.0.0.1 随机端口）+ 后台 Dispatcher；可选 MCP stdio。"""

from __future__ import annotations

import argparse
import os
import sys
import threading

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DSH Cognitive Memory Service")
    parser.add_argument("--host", default=os.environ.get("DSH_MEMORY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DSH_MEMORY_PORT", "0")))
    parser.add_argument("--data-dir", default=os.environ.get("DSH_MEMORY_DATA_DIR", ""))
    parser.add_argument("--token", default=os.environ.get("DSH_MEMORY_TOKEN", ""))
    parser.add_argument("--mcp", action="store_true", help="同时在本进程跑 MCP stdio（调试用）")
    parser.add_argument("--no-dispatcher", action="store_true")
    args = parser.parse_args(argv)

    # 确保 src 可导入（未 pip install -e 时）
    here = os.path.dirname(os.path.abspath(__file__))
    src_root = os.path.abspath(os.path.join(here, ".."))
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    from cognitive_memory.bootstrap import build_context, write_service_json

    ctx = build_context(
        data_dir=args.data_dir or None,
        host=args.host,
        port=args.port,
        token=args.token or None,
    )
    # 把 token 写回环境，方便同进程 MCP / 子工具
    os.environ["DSH_MEMORY_TOKEN"] = ctx.access_token

    if not args.no_dispatcher:
        ctx.runtime.start()

    config = uvicorn.Config(
        ctx.app,
        host=ctx.host,
        port=ctx.port,
        log_level=os.environ.get("DSH_MEMORY_LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _on_started() -> None:
        # uvicorn 绑定后写 service.json
        socks = getattr(server, "servers", None) or []
        bound = ctx.port
        for s in socks:
            for sock in getattr(s, "sockets", []) or []:
                try:
                    bound = sock.getsockname()[1]
                except Exception:
                    pass
        # fallback: wait briefly and read from config
        if bound == 0:
            # Server 在 run 前可能还没有 sockets；用回调线程轮询
            pass
        path = write_service_json(ctx, bound if bound else ctx.port)
        # 握手行：仅 stdout 一行 JSON，供父进程解析（非 MCP 模式）
        if not args.mcp:
            print(
                f"DSH_MEMORY_READY {path}",
                flush=True,
            )

    # 在单独线程跑 server，主线程可跑 MCP
    started = threading.Event()

    def _run_http() -> None:
        # monkey: after startup hook
        original = server.startup

        async def startup_wrap(*_args, **_kwargs) -> None:
            await original(*_args, **_kwargs)
            _on_started()
            # 修正端口：从 servers 读取
            bound_port = ctx.port
            for s in server.servers:
                for sock in s.sockets:
                    bound_port = sock.getsockname()[1]
            write_service_json(ctx, bound_port)
            if not args.mcp:
                print(
                    f"DSH_MEMORY_BOUND {ctx.host}:{bound_port}",
                    flush=True,
                )
            started.set()

        server.startup = startup_wrap  # type: ignore[method-assign]
        server.run()

    http_thread = threading.Thread(target=_run_http, name="cm-http", daemon=True)
    http_thread.start()
    started.wait(timeout=15)

    if args.mcp:
        from cognitive_memory.mcp.server import run_mcp_stdio

        try:
            run_mcp_stdio(ctx.service)
        finally:
            ctx.runtime.stop()
            server.should_exit = True
        return 0

    try:
        http_thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        ctx.runtime.stop()
        server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
