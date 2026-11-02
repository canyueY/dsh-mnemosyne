# -*- coding: utf-8 -*-
"""Component Contract + Manager（D8 最小面）：注册 ≠ 授权；安装 ≠ 授权。"""

from cognitive_memory.components.contract import (
    ComponentManifest,
    ComponentRegistry,
    UnauthorizedComponentError,
)
from cognitive_memory.components.manager import ComponentManager
from cognitive_memory.components.manifest import FullComponentManifest, ManifestError, load_manifest
from cognitive_memory.components.profiles import get_profile, list_profiles
from cognitive_memory.components.resolver import ResolveResult, resolve_dependencies

__all__ = [
    "ComponentManager",
    "ComponentManifest",
    "ComponentRegistry",
    "FullComponentManifest",
    "ManifestError",
    "ResolveResult",
    "UnauthorizedComponentError",
    "get_profile",
    "list_profiles",
    "load_manifest",
    "resolve_dependencies",
]
