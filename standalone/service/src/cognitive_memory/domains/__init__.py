# -*- coding: utf-8 -*-
"""Pluggable domain packs (ontology registration; not user data import)."""

from cognitive_memory.domains.pack import DomainPack, load_domain_pack
from cognitive_memory.domains.registrar import register_domain_pack
from cognitive_memory.domains.retrieval import (
    MUSIC_INTENT_RE,
    apply_domain_gate,
    detect_music_intent,
)

__all__ = [
    "DomainPack",
    "load_domain_pack",
    "register_domain_pack",
    "MUSIC_INTENT_RE",
    "apply_domain_gate",
    "detect_music_intent",
]
