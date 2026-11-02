# -*- coding: utf-8 -*-
"""Host / importer adapters — 一律经 Service Contract / HTTP，禁止直连 SQLite。"""

from cognitive_memory.adapters.dsh_http import DshHttpAdapter, FORBIDDEN_IMPORT_PREFIXES

__all__ = ["DshHttpAdapter", "FORBIDDEN_IMPORT_PREFIXES"]
