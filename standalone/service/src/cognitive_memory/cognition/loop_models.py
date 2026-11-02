"""Executable, deliberately bounded contracts for the two cognitive loops."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Action(Contract):
    id: str = Field(min_length=1, max_length=128)
    claim_ids: list[str] = Field(min_length=1, max_length=32)
    when: dict[str, Any] = Field(default_factory=dict)
    schema_id: str | None = None
    priority: float = 0  # task preference/order, never epistemic confidence


class CognitiveTask(Contract):
    goal: str = Field(min_length=1, max_length=2000)
    bank_id: str = "personal"
    context: dict[str, Any] = Field(default_factory=dict)
    acceptance: str | None = None
    allowed_actions: list[str] = Field(min_length=1, max_length=32)
    actions: list[Action] = Field(min_length=1, max_length=32)
    max_steps: int = Field(default=128, ge=1, le=2048)
    use_cache: bool = True

    @model_validator(mode="after")
    def distinct_actions(self):
        if len({a.id for a in self.actions}) != len(self.actions):
            raise ValueError("duplicate_action_id")
        return self


class ExecutableRule(Contract):
    action_id: str = Field(min_length=1)
    when: dict[str, Any] = Field(default_factory=dict)
    invalid_when: dict[str, Any] = Field(default_factory=dict)
    required_claim_ids: list[str] = Field(min_length=1, max_length=32)
    counter_claim_ids: list[str] = Field(default_factory=list, max_length=32)
    valid_from: str | None = None
    valid_until: str | None = None


class ValidationCase(Contract):
    id: str = Field(min_length=1)
    partition: Literal["heldout", "regression"]
    task: CognitiveTask
    expected_action: str | None = None


class ValidationSuite(Contract):
    bank_id: str = "personal"
    cases: list[ValidationCase] = Field(min_length=2, max_length=100)
    min_gain: int = Field(default=1, ge=0)
    max_regressions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def partitions(self):
        if {c.partition for c in self.cases} != {"heldout", "regression"}:
            raise ValueError("heldout_and_regression_required")
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case_id")
        if any(c.task.bank_id != self.bank_id for c in self.cases):
            raise ValueError("cross_bank_suite")
        if any(c.expected_action is not None and c.expected_action not in {a.id for a in c.task.actions} for c in self.cases):
            raise ValueError("expected_action_not_in_candidates")
        return self


class LearningProposal(Contract):
    schema_id: str
    suite_id: str
    rule: ExecutableRule
    reason: str = Field(min_length=1)
    training_case_ids: list[str] = Field(default_factory=list)
    training_evidence_ids: list[str] = Field(default_factory=list, max_length=64)


class ProfileHypothesis(Contract):
    subject: str = Field(min_length=1)
    kind: Literal["personality", "preference", "emotion", "value"]
    statement: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    source: Literal["user_statement", "inference"]
    evidence_ids: list[str] = Field(min_length=1, max_length=32)
    guided: bool = False
    bank_id: str = "personal"
    expires_at: str | None = None


class TaskFeedback(Contract):
    kind: Literal["fact_correction", "expression_preference", "task_outcome", "execution_failure", "constraint_violation", "unclassified"]
    source: Literal["user", "execution", "agent_self_eval"]
    outcome: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    possible_stages: list[Literal["encoding", "organization", "inference", "context", "retrieval", "strategy", "execution", "preference"]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
