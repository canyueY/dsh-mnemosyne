# -*- coding: utf-8 -*-
"""Deterministic Claim AST normalization (no LLM).

Same input + same AST version ⇒ same normalized form and hash.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from typing import Any

from cognitive_memory.claims.ast import (
    AST_VERSION,
    Atom,
    ClaimAST,
    ClaimNode,
    Conditional,
    Negation,
    Temporal,
    TemporalInterval,
    Unresolved,
)

_WS = re.compile(r"\s+")
NORMALIZATION_VERSION = "claim.normalize.v2"


def _canon_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.strip().lower()
    text = _WS.sub(" ", text)
    return text


def _canon_subject(subject: str) -> str:
    s = _canon_text(subject)
    aliases = {
        "我": "user",
        "用户": "user",
        "主人": "user",
        "self": "user",
        "owner": "user",
    }
    return aliases.get(s, s or "unknown_subject")


def _canon_argument(value: str) -> str:
    raw=unicodedata.normalize("NFKC",value).strip()
    match=re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]+)",raw)
    if match:
        units={"kg":("kg","1"),"g":("kg","0.001"),"mg":("kg","0.000001"),
               "m":("m","1"),"cm":("m","0.01"),"mm":("m","0.001"),"km":("m","1000"),
               "s":("s","1"),"ms":("s","0.001"),"W":("W","1"),
               "mW":("W","0.001"),"MW":("W","1000000")}
        unit=match[2]
        if unit in units:
            canonical,factor=units[unit]
            number=Decimal(match[1])
            if not number.is_finite() or abs(number.adjusted())>128:
                return raw
            number=number*Decimal(factor)
            return "quantity:"+format(number.normalize(),"f")+" "+canonical
        return raw  # Unknown units retain case; MW and mW must never collapse.
    return _canon_text(value)


def _canon_predicate(pred: str) -> str:
    p = _canon_text(pred)
    aliases = {
        "喜欢": "likes",
        "喜爱": "likes",
        "爱听": "likes",
        "偏好": "likes",
        "讨厌": "dislikes",
        "不喜欢": "dislikes",
        "是": "is_a",
        "位于": "located_at",
        "在": "located_at",
        "有": "has",
        "包含": "contains",
        "等于": "equals",
        "收藏": "saves",
    }
    return aliases.get(p, p or "unknown_predicate")


def _canon_interval(interval: TemporalInterval) -> dict[str, Any]:
    return {
        "start": interval.start or None,
        "end": interval.end or None,
        "kind": "half_open",
    }


def _normalize_node(node: ClaimNode) -> dict[str, Any]:
    if isinstance(node, Atom) or getattr(node, "kind", None) == "atom":
        args = [_canon_argument(a) for a in (node.arguments or [])]
        return {
            "kind": "atom",
            "subject": _canon_subject(node.subject),
            "predicate": _canon_predicate(node.predicate),
            "arguments": args,
            "modality": _canon_text(node.modality or "") or None,
        }
    if isinstance(node, Negation) or getattr(node, "kind", None) == "negation":
        return {"kind": "negation", "target": _normalize_node(node.target)}
    if isinstance(node, Temporal) or getattr(node, "kind", None) == "temporal":
        return {
            "kind": "temporal",
            "interval": _canon_interval(node.interval),
            "target": _normalize_node(node.target),
        }
    if isinstance(node, Conditional) or getattr(node, "kind", None) == "conditional":
        return {
            "kind": "conditional",
            "condition": _normalize_node(node.condition),
            "consequence": _normalize_node(node.consequence),
        }
    # Unresolved
    return {
        "kind": "unresolved",
        "raw_text": _canon_text(getattr(node, "raw_text", "")),
        "missing_fields": sorted(getattr(node, "missing_fields", []) or []),
        "reason_codes": sorted(getattr(node, "reason_codes", []) or []),
    }


def normalize_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    if not scope:
        return {}
    out: dict[str, Any] = {}
    for key in sorted(scope.keys()):
        val = scope[key]
        if isinstance(val, str):
            out[key] = _canon_text(val)
        elif isinstance(val, (int, float, bool)) or val is None:
            out[key] = val
        else:
            out[key] = json.loads(json.dumps(val, ensure_ascii=False, sort_keys=True))
    return out


def normalize_ast(ast: ClaimAST) -> dict[str, Any]:
    """Return canonical dict used for identity / hashing."""
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "ast_version": ast.ast_version or AST_VERSION,
        "root": _normalize_node(ast.root),
        "scope": normalize_scope(ast.scope),
    }


def normalized_hash(ast: ClaimAST) -> str:
    payload = json.dumps(normalize_ast(ast), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
