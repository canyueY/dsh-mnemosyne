# -*- coding: utf-8 -*-
"""领域模型与枚举（与 SQLite / API 契约对齐）。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class SourceKind(str, Enum):
    USER_STATEMENT = "user_statement"
    DIALOG = "dialog"
    VIDEO_OBSERVATION = "video_observation"
    AGENT_INFERENCE = "agent_inference"
    SYSTEM = "system"
    FEEDBACK = "feedback"


class RelationKind(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DERIVES = "derives"
    SUPERSEDES = "supersedes"
    # P3 时空/时序关联（写入 memory_relations）
    BEFORE = "before"
    AFTER = "after"
    CONTINUES = "continues"
    OCCURRED_IN = "occurred_in"
    LOCATED_AT = "located_at"
    DEPENDS_ON = "depends_on"
    OBSERVED_DURING = "observed_during"
    CONTAINS = "contains"
    PERFORMED_BY = "performed_by"
    COMPOSED_BY = "composed_by"
    BELONGS_TO = "belongs_to"
    TAGGED_AS = "tagged_as"
    LISTENED_TO = "listened_to"
    SAVES = "saves"
    FOLLOWS = "follows"
    LIKES = "likes"
    FAVORITES = "favorites"
    IMPORTED_FROM = "imported_from"


class MemoryRelationIn(BaseModel):
    from_id: str
    to_id: str
    relation: str
    note: str = ""
    valid_from: str | None = None
    valid_until: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeDetectIn(BaseModel):
    observation_id: str
    dry_run: bool = True
    apply_l2: bool = False


class ChangePlanBuildIn(BaseModel):
    observation_id: str
    dry_run: bool = True
    persist: bool = True


class ChangePlanApplyIn(BaseModel):
    confirm: bool = False


class ReevaluateIn(BaseModel):
    dry_run: bool = True
    apply_l2: bool = False
    limit: int = 40


class PatternScanIn(BaseModel):
    limit_obs: int = 400
    min_days: int = 3
    min_count: int = 3


class EmotionExtractIn(BaseModel):
    observation_id: str
    shadow_only: bool = True
    write_enabled: bool = False
    min_confidence: float = 0.35


class EmotionPatternScanIn(BaseModel):
    limit_obs: int = 400
    min_days: int = 2
    min_count: int = 2
    persist_summary: bool = False


class ObservationIn(BaseModel):
    source_kind: SourceKind
    content: str
    occurred_at: str | None = None
    producer: str = "dsh"
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    data_ref: str | None = None
    idempotency_key: str | None = None
    # Identity 附着（E1.6）；缺省由 ingestion 归一化填充
    agent_id: str | None = None
    subject_id: str | None = None
    node_id: str | None = None


class Observation(ObservationIn):
    id: str
    created_at: str


class MemoryProposeIn(BaseModel):
    type: MemoryType = MemoryType.SEMANTIC
    content: str
    source_ids: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.CANDIDATE
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    expires_at: str | None = None
    idempotency_key: str | None = None
    bank_id: str = "personal"


class MemoryUpdateIn(BaseModel):
    content: str | None = None
    status: MemoryStatus | None = None
    metadata: dict[str, Any] | None = None
    confidence: float | None = None
    expires_at: str | None = None
    expected_version: int | None = None


class Memory(BaseModel):
    id: str
    type: MemoryType
    status: MemoryStatus
    content: str
    source_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    last_confirmed_at: str | None = None
    version: int = 1
    confidence: float | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    bank_id: str = "personal"


class EvidenceLink(BaseModel):
    memory_id: str
    observation_id: str
    relation: RelationKind = RelationKind.SUPPORTS
    note: str = ""
    confidence: float | None = None
    evidence_kind: str = "observation"
    validation_state: str = "unverified"
    producer: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorityTransitionIn(BaseModel):
    state: str
    reason: str = ""
    allow_dual: bool = False
    actor: str = "api"


class FeedbackIn(BaseModel):
    memory_id: str
    useful: bool | None = None
    incorrect: bool | None = None
    note: str = ""
    producer: str = "dsh.agent"
    feedback_kind: Literal["unclassified", "fact_correction", "expression_preference", "task_outcome", "execution_failure", "constraint_violation"] = "unclassified"


class SearchQuery(BaseModel):
    # Optional exact metadata scopes, combined with AND before SQL LIMIT.
    # Filters narrow results; authorization remains the caller's responsibility.
    user_id: str | None = Field(default=None, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, min_length=1, max_length=256)
    run_id: str | None = Field(default=None, min_length=1, max_length=256)
    domain: str | None = None
    query: str = ""
    types: list[MemoryType] | None = None
    status: list[MemoryStatus] | None = None
    limit: int = 10
    include_evidence: bool = False
    # None → 仅 personal；须显式列入 shared/public
    banks: list[str] | None = None
    # current：降权过期/历史空间；historical：偏历史；any：不时空重排
    mode: str = "current"
    spatiotemporal_rerank: bool = True
    # Mnemosyne P5：按查询情绪信号对带 mnemosyne 的记忆重排
    emotion_rerank: bool = False


class ContextBuildIn(BaseModel):
    domain: str | None = None
    query: str
    token_budget: int | None = None
    types: list[MemoryType] | None = None
    limit: int = 12
    mode: str = "current"
    spatiotemporal_rerank: bool = True
    emotion_rerank: bool = False
    # E04：完整信封预算（可选覆盖）
    envelope: dict[str, Any] | None = None
    """sections: stable_prefix/tools/history/header_tags/output_reserve；budget_config 等。"""
    has_accurate_tokenizer: bool = False


class ContextBuildOut(BaseModel):
    fragments: list[dict[str, Any]]
    token_estimate: int
    brief: str
    fits: bool | None = True
    measurement: str = "estimated"
    hard_limit_verified: bool = False
    budget_envelope: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    status: str = "ok"


class RetrieveIn(BaseModel):
    domain: str | None = None
    """Mnemosyne P1：统一检索入口（含触发规划）。"""

    query: str = ""
    force: bool = False
    limit: int = 8
    mode: str = "current"
    spatiotemporal_rerank: bool = True
    emotion_rerank: bool = False
    # P3：语义旁路加分；不可作为 Verified / Evidence 替代
    semantic_assist: bool = True
    semantic_weight: float = 0.25
    # P5：opt-in A/B；需 BANDIT_EXPERIMENT；control 关闭 assist
    ab_experiment: bool = False
    ab_subject_key: str = ""
    token_budget: int | None = None
    types: list[MemoryType] | None = None


class TaskCreateIn(BaseModel):
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class Task(BaseModel):
    id: str
    task_type: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    last_error: str = ""
    created_at: str
    updated_at: str


# ---- Mnemosyne 2.0 schema / gate ----


class SchemaDiscoverIn(BaseModel):
    limit_memories: int = 200
    min_cluster: int = 3
    min_similarity: float = 0.22
    persist: bool = True


class ClaimProposeIn(BaseModel):
    text: str
    bank_id: str = "personal"
    source_memory_id: str | None = None
    persist_unresolved: bool = True
    validity_from: str | None = None
    validity_to: str | None = None


class ClaimTransitionIn(BaseModel):
    expected_version: int | None = None
    target_status: str
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    replacement_claim_id: str | None = None
    confidence: float | None = None


class ClaimSupersedeIn(BaseModel):
    replacement_claim_id: str
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    confidence: float | None = None


class ClaimRetractIn(BaseModel):
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    confidence: float | None = None


class ClaimExpireIn(BaseModel):
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    confidence: float | None = None


class ClaimEvidenceIn(BaseModel):
    evidence_ref: str
    role: str = "support"
    independent: bool = False


class ClaimRedirectIn(BaseModel):
    target_id: str
    reason: str = "merge"


class ClaimMergeIn(BaseModel):
    target_id: str
    reason: str = "claim_merge"
    move_evidence: bool = True


class ClaimSplitIn(BaseModel):
    parts: list[str] = Field(default_factory=list)
    reason: str = "claim_split"
    bank_id: str | None = None


class ClaimRelateIn(BaseModel):
    other_id: str


class ClaimParseIn(BaseModel):
    text: str


class FaultPipelineIn(BaseModel):
    claim_id: str
    apply_tombstone_repair: bool = False
    run_regression: bool = True


class ClaimVerifyIn(BaseModel):
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    evidence_still_valid: bool = True
    expected_version: int | None = None


class EvidenceObjectCreateIn(BaseModel):
    source_type: str = "observation"
    source_ref: str = ""
    bank_id: str = "personal"
    producer: str = ""
    content: str = ""
    role_hint: str = "support"
    legacy_memory_id: str | None = None
    root_source_id: str = ""
    revision: str | None = None
    source_role: str | None = None
    coverage: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnswerSelectIn(BaseModel):
    """E03：当前/历史答案选择（读路径，不写 verified）。"""

    bank_id: str = "personal"
    query: str = ""
    target: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    at: str | None = None
    mode: str = "current"  # current|history
    budget: dict[str, Any] = Field(default_factory=dict)
    expected_source_revisions: list[dict[str, Any]] = Field(default_factory=list)
    current_source_revisions: dict[str, str] = Field(default_factory=dict)


class SessionMessageIn(BaseModel):
    """E06：会话消息账本（稳定键去重；曝光≠事实支持）。"""

    session_id: str
    bank_id: str = "personal"
    role: str
    content: str
    message_id: str | None = None
    producer: str = "host"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionDeliveryIn(BaseModel):
    """E06：交付账本（交付≠理解）。"""

    session_id: str
    bank_id: str = "personal"
    card_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionCheckpointIn(BaseModel):
    """E06：ContextManifest / 任务 checkpoint。"""

    session_id: str
    bank_id: str = "personal"
    task_id: str | None = None
    checkpoint_id: str | None = None
    goal: str = ""
    constraints: list[str] = Field(default_factory=list)
    verified_progress: list[dict[str, Any]] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    pending_external_operations: list[dict[str, Any]] = Field(default_factory=list)
    delivered_cards: list[str] = Field(default_factory=list)
    evidence_revisions: list[dict[str, Any]] = Field(default_factory=list)
    assistant_claims: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResumeIn(BaseModel):
    """E06：恢复前重新鉴权；未知副作用禁止盲重试。"""

    checkpoint_id: str
    confirm_blind_retry: bool = False


class UsageReceiptIn(BaseModel):
    """E07：调用级 usage 回执（预留/实测分开；同 attempt 去重）。"""

    correlation_id: str
    attempt: int = 1
    provider: str = "node"
    meter_scope: str = "node"
    amount: float | None = None
    measurement: str | None = None
    success: bool | None = None
    unit_key: str = ""
    phase: str = "receipt"  # reserve|receipt
    estimated_cost: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageReportIn(BaseModel):
    correlation_id: str


class SessionConsolidateIn(BaseModel):
    """E10：会话巩固（规则摘要；后台默认关闭至 E11）。"""

    session_id: str
    atoms: list[dict[str, Any]] = Field(default_factory=list)
    allowed_input_ids: list[str] | None = None
    topic: str = ""
    foreground_preempt: bool = False


class ExecutionPlanIn(BaseModel):
    actor: str
    bank_id: str = "personal"
    capability: str
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: int = 1
    correlation_id: str | None = None
    idempotency_key: str | None = None


class ExecutionObserveIn(BaseModel):
    outcome: str = "unknown"
    details: dict[str, Any] = Field(default_factory=dict)


class EvidenceIndependenceIn(BaseModel):
    evidence_ids: list[str] = Field(default_factory=list)


class TmsJustificationIn(BaseModel):
    conclusion_id: str
    premises: list[str] = Field(default_factory=list)
    kind: str = "support"
    reason: str = ""
    actor: str = ""


class TmsPropagateIn(BaseModel):
    seed_ids: list[str] = Field(default_factory=list)
    max_nodes: int = 64
    max_depth: int = 16


class ExperienceCreateIn(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    memory_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    reward: float | None = None
    bank_id: str = "personal"
    source: str = "manual"


class StrategyProposeIn(BaseModel):
    name: str
    description: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    status: str = "proposed"


class StrategyLogIn(BaseModel):
    experience_id: str | None = None
    decision: str = ""
    outcome: str = ""
    propensity: float | None = None
    overlap: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class StrategyOfflineEvalIn(BaseModel):
    strategy_id: str
    persist_propensity: bool = True
    log_result: bool = True


class StrategyPropensityIn(BaseModel):
    strategy_id: str | None = None
    experience_ids: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)


class MetaControllerIn(BaseModel):
    action: str = "reflect"
    strategy_id: str | None = None
    ab_experiment: bool = False


class AbAssignIn(BaseModel):
    experiment_id: str = "semantic_assist.v1"
    subject_key: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class AbOutcomeIn(BaseModel):
    exposure_id: str
    reward: float | None = None
    label: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class AbReportIn(BaseModel):
    experiment_id: str = "semantic_assist.v1"
    limit: int = 500


class KnowledgeProposeIn(BaseModel):
    kind: str
    name: str
    description: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    schema_id: str | None = None
    strategy_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    security: dict[str, Any] = Field(default_factory=dict)
    status: str = "proposed"


class KnowledgeStatusIn(BaseModel):
    status: str


class KnowledgeFromSchemaIn(BaseModel):
    schema_id: str
    name: str = ""
    as_pattern: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class ValueAdaptIn(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    actor: str = "governance"


class SchemaAssimilateIn(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    note: str = ""
    # P1.5：confirm  alone 不足；落盘另需 SCHEMA_MUTATION
    confirm: bool = False
    preview: bool = False


class SchemaAccommodateIn(BaseModel):
    memory_ids: list[str] = Field(default_factory=list)
    counter_note: str = ""
    propose_split: bool = True
    confirm: bool = False
    preview: bool = False


class CognitiveChangePlanIn(BaseModel):
    operation: str = "UPDATE"
    target_schema_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    expected_impact: list[str] = Field(default_factory=list)
    risk_level: str = "MEDIUM"
    approval_required: bool = True
    reason: str = ""


class CognitiveChangePlanApplyIn(BaseModel):
    confirm: bool = False
    preview: bool = False


class CapabilityCheckIn(BaseModel):
    capability: str


class CapabilitySetIn(BaseModel):
    """仅外部治理；Agent 不得调用。"""

    capability: str
    level: str
    enabled: bool | None = None
    actor: str = "external_governance"


class ReflectionIn(BaseModel):
    judgment: str = ""
    schema_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)


class ValueAnalyzeIn(BaseModel):
    intent: str
    stakeholders: list[str] = Field(default_factory=list)
    proposed_action: str = ""
