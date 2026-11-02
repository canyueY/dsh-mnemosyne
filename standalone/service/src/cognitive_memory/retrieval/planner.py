# -*- coding: utf-8 -*-
"""RetrievalPlanner：规则优先决定是否检索及查询范围。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# 显式 / 隐式历史指代
_HISTORY_RE = re.compile(
    r"(上次|上次我们|之前|从前|那天|那次|记得|记不记得|有没有说过|说过|"
    r"继续|接着|原定|约定|按照之前|按我们说好|讨论过|聊过|"
    r"你还记得|你记得|历史|回顾|当时|那会儿|上个月|去年|第一次|"
    r"last\s+time|remember|previously|as\s+we\s+(said|agreed)|continue\s+(the|our))",
    re.IGNORECASE,
)
# 纠正 / 变更 → 维护触发
_MAINTAIN_RE = re.compile(
    r"(改了|变了|不对|记错|不是这样|纠正|更正|取消|作废|已经不|"
    r"以前说的.*错|别再记|忘掉|forget|correct|changed|no\s+longer)",
    re.IGNORECASE,
)
# 决策核对
_DECISION_RE = re.compile(
    r"(按(照)?(之前|原定|约定)|方案执行|照着办|别违背|约束|红线)",
    re.IGNORECASE,
)
# 明显寒暄 / 无需历史
_CHITCHAT_RE = re.compile(
    r"^(你好|您好|早|晚安|在吗|在不在|嗨|哈喽|hello|hi|hey|嗯|哦|好的|谢谢|"
    r"拜拜|再见)[\s！!。.~～?？]*$",
    re.IGNORECASE,
)


@dataclass
class QueryPlan:
    should_retrieve: bool
    trigger: str  # none|explicit|implicit|decision|maintain|forced
    reason: str
    query_type: str  # factual|temporal|continuation|correction|general
    depth: str  # shallow|normal|deep
    mode: str  # current|historical|any
    limit: int = 8
    time_hint: str = ""
    expected_min_hits: int = 1
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_retrieval(
    query: str,
    *,
    force: bool = False,
    default_mode: str = "current",
    default_limit: int = 8,
) -> QueryPlan:
    text = (query or "").strip()
    limit = max(1, min(40, int(default_limit or 8)))
    mode = default_mode if default_mode in ("current", "historical", "any") else "current"

    if force:
        return QueryPlan(
            should_retrieve=True,
            trigger="forced",
            reason="caller_forced",
            query_type="general",
            depth="normal",
            mode=mode,
            limit=limit,
        )

    if not text:
        return QueryPlan(
            should_retrieve=False,
            trigger="none",
            reason="empty_query",
            query_type="general",
            depth="shallow",
            mode=mode,
            limit=limit,
        )

    if _CHITCHAT_RE.match(text) and not _HISTORY_RE.search(text):
        return QueryPlan(
            should_retrieve=False,
            trigger="none",
            reason="chitchat_no_history_ref",
            query_type="general",
            depth="shallow",
            mode=mode,
            limit=limit,
        )

    if _MAINTAIN_RE.search(text):
        return QueryPlan(
            should_retrieve=True,
            trigger="maintain",
            reason="correction_or_change_signal",
            query_type="correction",
            depth="deep",
            mode="any",
            limit=max(limit, 12),
            expected_min_hits=1,
        )

    if _DECISION_RE.search(text) or _HISTORY_RE.search(text):
        temporal = bool(
            re.search(r"(上个月|去年|那天|当时|历史|以前|从前)", text)
        )
        return QueryPlan(
            should_retrieve=True,
            trigger="explicit" if _HISTORY_RE.search(text) else "decision",
            reason="history_or_decision_ref",
            query_type="temporal" if temporal else "continuation",
            depth="normal",
            mode="historical" if temporal else mode,
            limit=limit,
            time_hint="past" if temporal else "",
        )

    # 较长任务句且含项目/偏好线索 → 隐式（中文 12 字起）
    if len(text) >= 12 and re.search(
        r"(项目|方案|偏好|习惯|喜欢|讨厌|不要|必须|约定|计划)", text
    ):
        return QueryPlan(
            should_retrieve=True,
            trigger="implicit",
            reason="task_depends_on_preference_or_project",
            query_type="factual",
            depth="shallow",
            mode=mode,
            limit=min(limit, 6),
        )

    return QueryPlan(
        should_retrieve=False,
        trigger="none",
        reason="no_retrieval_signal",
        query_type="general",
        depth="shallow",
        mode=mode,
        limit=limit,
    )
