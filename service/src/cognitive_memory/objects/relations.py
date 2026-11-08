# -*- coding: utf-8 -*-
"""Canonical relation types. Raw stored type is never discarded."""

from __future__ import annotations

CANONICAL = {
    "contains": "contains",
    "child_of": "child_of",
    "alias_of": "alias_of",
    "merged_into": "merged_into",
    "evidenced_by": "evidenced_by",
    "observed_by": "observed_by",
    "produced_by": "produced_by",
    "supports": "supports",
    "contradicts": "contradicts",
    "qualifies": "qualifies",
    "derived_from": "derived_from",
    "derives": "derived_from",
    "supersedes": "supersedes",
    "constrains": "constrains",
    "applies_to": "applies_to",
    "mentions": "mentions",
    "extracted_from": "extracted_from",
    "summarized_as": "summarized_as",
    "cites": "cites",
    "references": "references",
    "rendered_by": "rendered_by",
    "copied_from": "copied_from",
    "used_by": "used_by",
    "likes": "likes",
    "dislikes": "dislikes",
    "interested_in": "interested_in",
    "cares_about": "cares_about",
    "accompanies": "accompanies",
    "listens_to": "listens_to",
    "performed_by": "performed_by",
    "has_style": "has_style",
    "airing": "airing",
    "rated_by": "rated_by",
    "belongs_to_season": "belongs_to_season",
    "located_in": "located_in",
    "located_at": "located_at",
    "discussed": "discussed",
    "shared_meme": "shared_meme",
    "uses": "uses",
    "remembers": "remembers",
    "related": "related",
    "before": "before",
    "after": "after",
    "continues": "continues",
    "occurred_in": "occurred_in",
    "depends_on": "depends_on",
    "observed_during": "observed_during",
    "tagged_as": "tagged_as",
    "listened_to": "listens_to",
    "saves": "saves",
    "follows": "follows",
    "favorites": "likes",
    "imported_from": "extracted_from",
    "composed_by": "performed_by",
    "belongs_to": "contains",
}


def canonicalize(relation_type: str | None) -> str:
    raw = str(relation_type or "").strip().lower()
    if not raw:
        return "unknown"
    return CANONICAL.get(raw, "other")
