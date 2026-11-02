# -*- coding: utf-8 -*-
"""进程内事件总线（订阅者）+ Outbox 持久化由 MemoryService 负责。"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        with self._lock:
            self._subs[event_type].append(handler)
            self._subs["*"].append(handler)

    def publish_local(self, envelope: dict[str, Any]) -> None:
        et = str(envelope.get("event_type") or "")
        with self._lock:
            handlers = list(self._subs.get(et, [])) + list(self._subs.get("*", []))
        # 去重同一 handler 被 * 与具体类型各注册一次的情况
        seen: set[int] = set()
        for h in handlers:
            hid = id(h)
            if hid in seen:
                continue
            seen.add(hid)
            try:
                h(envelope)
            except Exception as exc:  # noqa: BLE001 — 消费者失败不拖垮发布
                print(f"[eventbus] handler error: {exc}", flush=True)
