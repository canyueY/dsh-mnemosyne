# -*- coding: utf-8 -*-
"""In-process grant snapshot cache. Revoke MUST invalidate immediately (P8)."""

from __future__ import annotations

import threading
import time
from typing import Any


class GrantCache:
    """Thread-safe TTL cache keyed by agent_id → grant snapshot.

    Authenticate may read cache; revoke_grant / issue_grant must call invalidate.
    """

    def __init__(self, *, ttl_sec: float = 30.0) -> None:
        self._ttl = float(ttl_sec)
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            hit = self._entries.get(agent_id)
            if not hit:
                return None
            expires_at, payload = hit
            if time.monotonic() >= expires_at:
                self._entries.pop(agent_id, None)
                return None
            return dict(payload)

    def put(self, agent_id: str, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._entries[agent_id] = (time.monotonic() + self._ttl, dict(snapshot))

    def invalidate(self, agent_id: str | None = None) -> int:
        """Drop one agent or entire cache. Returns number of entries removed."""
        with self._lock:
            if agent_id is None:
                n = len(self._entries)
                self._entries.clear()
                return n
            if agent_id in self._entries:
                del self._entries[agent_id]
                return 1
            return 0

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


# Process-wide default cache used by grant auth path.
DEFAULT_GRANT_CACHE = GrantCache(ttl_sec=30.0)


def invalidate_grant_cache(agent_id: str | None = None) -> int:
    return DEFAULT_GRANT_CACHE.invalidate(agent_id)
