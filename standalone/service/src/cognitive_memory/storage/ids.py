# -*- coding: utf-8 -*-
"""稳定可读 ID（ULID 风格简化：时间戳 + 随机）。"""

from __future__ import annotations

import secrets
import time


def new_id(prefix: str) -> str:
    ms = int(time.time() * 1000)
    rand = secrets.token_hex(5)
    return f"{prefix}_{ms:x}{rand}"
