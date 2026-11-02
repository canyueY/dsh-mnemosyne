# -*- coding: utf-8 -*-
"""Minimal rule-based Claim parser (P0.5).

Conservative: prefers Unresolved over forced Atom.
No LLM calls. Model-assisted path is reserved for later phases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cognitive_memory.claims.ast import (
    AST_VERSION,
    Atom,
    ClaimAST,
    Conditional,
    Negation,
    Temporal,
    TemporalInterval,
    Unresolved,
)
from cognitive_memory.claims.normalize import normalized_hash

PARSER_VERSION = "claim.parser.v2"

_PREFERENCE = re.compile(
    r"^(?P<sub>我|用户|主人|[\u4e00-\u9fffA-Za-z0-9_]{1,24}?)\s*(?P<neg>不|没)?\s*(?P<pred>喜欢|喜爱|讨厌|爱听|偏好)\s*(?P<obj>.+)",
    re.UNICODE,
)
_MUSIC_PREF = re.compile(
    r"偏好(?:艺术家|歌手|曲目|歌曲)?[「『《]?(?P<obj>[^」』》\n]{1,64})[」』》]?",
    re.UNICODE,
)
_MUSIC_SAVED = re.compile(
    r"收藏了(?:歌曲|歌单)?[「『《]?(?P<obj>[^」』》(\n]{1,64})[」』》]?",
    re.UNICODE,
)
_IS_A = re.compile(
    r"^(?P<sub>[^\s，。！？]{1,24}?)\s*(?P<rel>不是|并非|是|为)\s*(?P<obj>[^\s，。！？]{1,48})$",
    re.UNICODE,
)
_LOCATED = re.compile(
    r"^(?P<sub>[^\s，。！？]{1,24}?)\s*(?P<rel>不位于|不在|位于|在)\s*(?P<obj>[^\s，。！？]{1,48})$",
    re.UNICODE,
)
_COND = re.compile(
    r"(如果|若|当)(?P<cond>.{1,40}?)(就|则|那么)(?P<cons>.{1,60})",
    re.UNICODE,
)
_NEG_PREFIX = re.compile(r"^(不是|并非|没有|未)(?P<body>.+)$", re.UNICODE)
_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2}|今天|昨天|明天|今晚|今早|上周|本周|今年|去年)")
_MULTI_SEP = re.compile(r"[;\n；]|。(?=.)")


@dataclass
class ParseResult:
    status: str  # parsed | unresolved | needs_review
    confidence: float
    ast: ClaimAST
    attempts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    multi_claim: bool = False
    sibling_asts: list[ClaimAST] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_version": PARSER_VERSION,
            "parse_score_kind": "heuristic_not_probability",
            "parse_status": self.status,
            "parse_confidence": self.confidence,
            "ast_version": self.ast.ast_version,
            "normalized_hash": normalized_hash(self.ast),
            "parsed_claim_ast": self.ast.model_dump(mode="json"),
            "multi_claim": self.multi_claim,
            "sibling_count": len(self.sibling_asts),
            "attempts": list(self.attempts),
            "notes": list(self.notes),
        }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _unresolved(text: str, *, missing: list[str], attempts: list[str], reasons: list[str]) -> ClaimAST:
    return ClaimAST(
        ast_version=AST_VERSION,
        root=Unresolved(
            raw_text=text,
            missing_fields=missing,
            parse_attempts=attempts,
            reason_codes=reasons,
        ),
        surface=text,
    )


def _try_preference(text: str, attempts: list[str]) -> ClaimAST | None:
    attempts.append("rule:preference")
    m = _MUSIC_PREF.search(text)
    if m:
        obj = _clean(m.group("obj")).strip("。！？. 、的作品")
        if 1 <= len(obj) <= 64:
            return ClaimAST(
                root=Atom(
                    subject="user",
                    predicate="偏好",
                    arguments=[obj],
                    modality="preference",
                ),
                surface=text,
                scope={"modality": "preference", "epistemic": "inferred_signal"},
            )
    # Remove a leading temporal label from the subject, retaining it in scope.
    preference_text = _DATE.sub("", text, count=1) if _DATE.match(text) else text
    m = _PREFERENCE.search(preference_text)
    if not m:
        return None
    obj = _clean(m.group("obj")).strip("。！？. ")
    subject = _clean(m.group("sub") or "")
    if re.search(r"不太|不怎么|现在|暂时|最近|偶尔|有时|一般|更|只在", subject) or re.search(r"[，,]|但是|不过|但下午", obj):
        return _unresolved(text, missing=["context_or_degree"], attempts=attempts, reasons=["qualified_preference_requires_review"])
    # trim trailing commentary
    obj = re.split(r"[（(]", obj, maxsplit=1)[0].strip()
    if len(obj) < 1 or len(obj) > 64:
        return None
    pred = m.group("pred")
    atom = Atom(
        subject=subject,
        predicate=pred,
        arguments=[obj],
        modality="preference",
    )
    if m.group("neg"):
        return ClaimAST(root=Negation(target=atom), surface=text, scope={"modality": "preference"})
    return ClaimAST(root=atom, surface=text, scope={"modality": "preference"})


def _try_music_saved(text: str, attempts: list[str]) -> ClaimAST | None:
    attempts.append("rule:music_saved")
    if "music:observed" not in text and "收藏了" not in text:
        return None
    m = _MUSIC_SAVED.search(text)
    if not m:
        return None
    obj = _clean(m.group("obj")).strip()
    if not obj:
        return None
    return ClaimAST(
        root=Atom(subject="user", predicate="收藏", arguments=[obj], modality="behavior"),
        surface=text,
        scope={"domain": "music", "data_kind": "user_action", "epistemic": "observed"},
    )


def _try_is_a(text: str, attempts: list[str]) -> ClaimAST | None:
    attempts.append("rule:is_a")
    m = _IS_A.search(text)
    if not m:
        return None
    atom = Atom(
            subject=_clean(m.group("sub")),
            predicate="是",
            arguments=[_clean(m.group("obj"))],
            modality="fact",
        )
    return ClaimAST(root=Negation(target=atom) if m.group("rel") in {"不是", "并非"} else atom, surface=text)


def _try_located(text: str, attempts: list[str]) -> ClaimAST | None:
    attempts.append("rule:located")
    m = _LOCATED.search(text)
    if not m:
        return None
    atom = Atom(
            subject=_clean(m.group("sub")),
            predicate="位于",
            arguments=[_clean(m.group("obj"))],
            modality="fact",
        )
    return ClaimAST(root=Negation(target=atom) if m.group("rel") in {"不位于", "不在"} else atom, surface=text)


def _try_conditional(text: str, attempts: list[str]) -> ClaimAST | None:
    attempts.append("rule:conditional")
    m = _COND.search(text)
    if not m:
        return None
    cond_txt = _clean(m.group("cond"))
    cons_txt = _clean(m.group("cons"))
    # Nested parse without recursion explosion: treat as atoms if short
    cond = Atom(subject="situation", predicate="holds", arguments=[cond_txt], modality="condition")
    cons = Atom(subject="outcome", predicate="holds", arguments=[cons_txt], modality="consequence")
    return ClaimAST(root=Conditional(condition=cond, consequence=cons), surface=text)


def _try_negation_wrap(text: str, attempts: list[str]) -> ClaimAST | None:
    attempts.append("rule:negation_prefix")
    m = _NEG_PREFIX.match(text)
    if not m:
        return None
    body = _clean(m.group("body"))
    inner = _parse_single(body, attempts, allow_negation=False)
    if inner.is_unresolved():
        return None
    return ClaimAST(root=Negation(target=inner.root), surface=text, scope=dict(inner.scope))


def _wrap_temporal(text: str, ast: ClaimAST, attempts: list[str]) -> ClaimAST:
    attempts.append("rule:temporal_wrap")
    m = _DATE.search(text)
    if not m or ast.is_unresolved():
        return ast
    token = m.group(0)
    # Map relative tokens to open-ended intervals without inventing absolute dates
    if re.match(r"20\d{2}-\d{2}-\d{2}", token):
        interval = TemporalInterval(start=token, end=None)
    else:
        interval = TemporalInterval(start=None, end=None)
        # keep relative label in scope, not as invented absolute time
        scope = dict(ast.scope)
        scope["relative_time"] = token
        return ClaimAST(
            root=Temporal(interval=interval, target=ast.root),
            surface=text,
            scope=scope,
        )
    return ClaimAST(
        root=Temporal(interval=interval, target=ast.root),
        surface=text,
        scope=dict(ast.scope),
    )


def _parse_single(text: str, attempts: list[str], *, allow_negation: bool = True) -> ClaimAST:
    text = _clean(text)
    if not text or len(text) < 2:
        return _unresolved(
            text,
            missing=["content"],
            attempts=attempts,
            reasons=["empty_or_too_short"],
        )
    # Skip pure system markers
    if text.startswith("[music:") and "inference" in text:
        # preference signal — still try preference-like extract from Chinese body
        pass

    for fn in (_try_conditional, _try_preference, _try_music_saved, _try_is_a, _try_located):
        hit = fn(text, attempts)
        if hit is not None:
            return _wrap_temporal(text, hit, attempts)

    if allow_negation:
        neg = _try_negation_wrap(text, attempts)
        if neg is not None:
            return _wrap_temporal(text, neg, attempts)

    return _unresolved(
        text,
        missing=["subject", "predicate"] if len(text) > 80 else ["stable_predicate"],
        attempts=list(attempts),
        reasons=["no_rule_match", "complex_or_underspecified"],
    )


def parse_memory_text(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    split_multi: bool = True,
) -> ParseResult:
    """Parse memory content into ClaimAST candidate(s)."""
    attempts: list[str] = []
    notes: list[str] = []
    meta = metadata or {}
    raw = text or ""

    # Domain hints → scope only (never invent verification)
    scope_hint: dict[str, Any] = {}
    if str(meta.get("domain") or "") == "music":
        scope_hint["domain"] = "music"
        notes.append("domain_hint:music")
    if str(meta.get("data_kind") or "") == "preference_signal":
        scope_hint["epistemic"] = "inferred_signal"
        notes.append("preference_signal_not_confirmed_fact")

    segments = [_clean(s) for s in _MULTI_SEP.split(raw) if _clean(s)] if split_multi else [_clean(raw)]
    if not segments:
        segments = [_clean(raw)]

    primary = _parse_single(segments[0], attempts)
    if scope_hint:
        primary = ClaimAST(
            root=primary.root,
            surface=primary.surface,
            scope={**primary.scope, **scope_hint},
            ast_version=primary.ast_version,
        )

    siblings: list[ClaimAST] = []
    if len(segments) > 1:
        for seg in segments[1:6]:
            siblings.append(_parse_single(seg, attempts))

    multi = len(segments) > 1
    if primary.is_unresolved():
        status = "unresolved"
        conf = 0.15
    elif siblings and any(s.is_unresolved() for s in siblings):
        status = "needs_review"
        conf = 0.45
        notes.append("multi_claim_partial_unresolved")
    else:
        status = "parsed"
        conf = 0.72 if primary.root.kind == "atom" else 0.6

    # Long free-form → force needs_review even if a rule matched a substring
    if len(raw) > 160 and status == "parsed":
        status = "needs_review"
        conf = min(conf, 0.5)
        notes.append("long_text_requires_review")

    return ParseResult(
        status=status,
        confidence=conf,
        ast=primary,
        attempts=attempts,
        notes=notes,
        multi_claim=multi,
        sibling_asts=siblings,
    )
