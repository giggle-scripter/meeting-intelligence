"""Strict, versioned data contracts for the locked V1 protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import sha256_file


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelWeights(StrictModel):
    human_confirmed: float
    teacher_consensus: float
    teacher_single: float
    deterministic_hard_negative: float
    weak_regex: float


class ProtocolGates(StrictModel):
    span_exact_recall: float
    span_token_f1: float
    proposal_recall_at_30: float
    task_identity_precision: float
    task_identity_recall: float
    task_identity_f1: float
    max_fold_f1_regression: float
    safety_violations: int


class DistillationProtocol(StrictModel):
    schema_version: Literal["distilled-proposal-ranker-protocol-v1"]
    base_commit: Literal["a04e815"]
    evidence_path: str
    trace_path: str
    baseline_trace_path: str
    expected_cases: Literal[86]
    expected_evidence_cases: Literal[78]
    expected_zero_task_cases: Literal[8]
    zero_task_case_ids: list[str]
    expected_tasks: Literal[277]
    baseline_expected: Literal[277]
    baseline_actual: Literal[345]
    baseline_matched: Literal[166]
    outer_folds: Literal[5]
    inner_folds: Literal[4]
    fold_seed: Literal[1729]
    training_seeds: list[int]
    span_model_name: Literal["FacebookAI/xlm-roberta-base"]
    reranker_model_name: Literal["FacebookAI/xlm-roberta-base"]
    span_max_length: Literal[384]
    reranker_max_length: Literal[512]
    local_context_before: Literal[3]
    local_context_after: Literal[5]
    max_context_clauses: Literal[30]
    max_context_characters: Literal[12000]
    max_span_candidates_per_clause: Literal[3]
    max_proposals_per_meeting: Literal[60]
    proposal_recall_k: Literal[30]
    teacher_mode_default: Literal["cache-only"]
    teacher_prompt_versions: list[str]
    label_weights: LabelWeights
    span_learning_rates: list[float]
    reranker_learning_rates: list[float]
    weight_decay: float
    warmup_ratio: float
    max_epochs: int
    early_stopping_patience: int
    gradient_clip_norm: float
    threshold_grid: list[float]
    gates: ProtocolGates

    @model_validator(mode="after")
    def validate_locked_values(self) -> "DistillationProtocol":
        checks = {
            "training_seeds": self.training_seeds == [17, 29, 43],
            "teacher_prompt_versions": self.teacher_prompt_versions
            == ["teacher-a-v1", "teacher-b-v1"],
            "span_learning_rates": self.span_learning_rates == [0.00002, 0.00003],
            "reranker_learning_rates": self.reranker_learning_rates == [0.00001, 0.00002],
            "threshold_grid": self.threshold_grid
            == [value / 100 for value in range(20, 91, 5)],
            "zero_task_case_ids": len(self.zero_task_case_ids) == 8
            and len(set(self.zero_task_case_ids)) == 8,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise ValueError(f"locked protocol value mismatch: {', '.join(failed)}")
        return self


class ProtocolSnapshot(StrictModel):
    protocol: DistillationProtocol
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def load_protocol(path: Path) -> ProtocolSnapshot:
    protocol = DistillationProtocol.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return ProtocolSnapshot(protocol=protocol, sha256=sha256_file(path))


class GroundedSpan(StrictModel):
    clause_id: str
    start: int
    end: int
    text: str

    @model_validator(mode="after")
    def validate_boundary(self) -> "GroundedSpan":
        if self.start < 0 or self.end <= self.start or not self.text.strip():
            raise ValueError("invalid grounded span boundary")
        return self


class ContextClause(StrictModel):
    clause_id: str
    speaker: str
    text_raw: str
    order_index: int
    context_start: int
    text_start: int
    text_end: int


class SpanExample(StrictModel):
    schema_version: Literal["action-span-example-v1"] = "action-span-example-v1"
    example_id: str
    input_hash: str
    case_id: str
    fold_id: int
    target_clause_id: str
    target_clause_text: str
    context: str
    context_clauses: list[ContextClause]
    target_start_in_context: int
    target_end_in_context: int
    gold_spans: list[GroundedSpan]
    source_label: Literal[
        "human_confirmed",
        "teacher_consensus",
        "teacher_single",
        "deterministic_hard_negative",
        "weak_regex",
        "unlabeled",
    ]
    weight: float
    overlap_group_id: str | None = None


class SpanPrediction(StrictModel):
    schema_version: Literal["action-span-prediction-v1"] = "action-span-prediction-v1"
    prediction_id: str
    input_hash: str
    case_id: str
    target_clause_id: str
    start: int
    end: int
    text: str
    start_logit: float
    end_logit: float
    span_score: float
    no_action_score: float
    model_name: str
    outer_fold: int
    training_seed: int
    checkpoint_hash: str


class TypedRelation(StrictModel):
    relation_type: str
    nucleus_clause_id: str
    support_clause_id: str
    distance: int
    chronology: Literal["BEFORE", "SAME", "AFTER"]


class ProposalRecord(StrictModel):
    schema_version: Literal["proposal-record-v1"] = "proposal-record-v1"
    proposal_id: str
    input_hash: str
    case_id: str
    kind: Literal["CREATE", "UPDATE", "REFERENCE", "DROP", "UNRESOLVED"]
    cluster_identity: str
    action_span: GroundedSpan
    authority_refs: list[str]
    acceptance_refs: list[str]
    owner_refs: list[str]
    deadline_refs: list[str]
    negative_refs: list[str]
    relations: list[TypedRelation]
    deterministic_score: float
    deterministic_reasons: list[str]
    neural_span_score: float
    source_type: Literal["LATTICE", "NEURAL", "BOTH"]
    ambiguity_flags: list[str]
    order_index: int


TeacherDecision = Literal["CREATE", "UPDATE", "REFERENCE", "DROP", "UNRESOLVED"]
TeacherReason = Literal[
    "DIRECT_ASSIGNMENT",
    "SELF_COMMITMENT",
    "QUESTION_ACCEPTED",
    "ASSIGNMENT_ACKNOWLEDGED",
    "ACTION_CONTINUATION",
    "OWNER_CONTINUATION",
    "DEADLINE_CONTINUATION",
    "SUGGESTION_ONLY",
    "QUESTION_UNACCEPTED",
    "HYPOTHETICAL",
    "PAST_COMPLETED",
    "PROGRESS_ONLY",
    "RECAP_DUPLICATE",
    "MUTATION_ONLY",
    "REFERENCE_ONLY",
    "INSUFFICIENT_EVIDENCE",
    "SIBLING_AMBIGUITY",
]


class TeacherClause(StrictModel):
    clause_id: str
    speaker: str
    order_index: int
    text_raw: str


class TeacherProposal(StrictModel):
    proposal_id: str
    action_span: GroundedSpan
    kind: TeacherDecision
    authority_refs: list[str]
    acceptance_refs: list[str]
    owner_refs: list[str]
    deadline_refs: list[str]
    negative_refs: list[str]
    relation_types: list[str]


class TeacherPayload(StrictModel):
    schema_version: Literal["teacher-payload-v1"] = "teacher-payload-v1"
    payload_id: str
    input_hash: str
    case_id: str
    outer_fold: int
    shard_index: int
    clauses: list[TeacherClause]
    proposals: list[TeacherProposal]
    allowed_date_mention_ids: list[str]
    allowed_existing_task_refs: list[str]


class TeacherDecisionRecord(StrictModel):
    proposal_id: str
    decision: TeacherDecision
    action_span_ref: str
    authority_clause_ids: list[str]
    owner_clause_id: str | None
    deadline_mention_id: str | None
    existing_task_ref: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[TeacherReason]


class TeacherResponse(StrictModel):
    schema_version: Literal["teacher-response-v1"] = "teacher-response-v1"
    payload_id: str
    prompt_version: Literal["teacher-a-v1", "teacher-b-v1"]
    payload_hash: str
    prompt_hash: str
    model_hash: str
    records: list[TeacherDecisionRecord]


class RerankerExample(StrictModel):
    schema_version: Literal["reranker-example-v1"] = "reranker-example-v1"
    example_id: str
    input_hash: str
    case_id: str
    outer_fold: int
    proposal_id: str
    serialized_input: str
    label: float = Field(ge=0.0, le=1.0)
    sample_weight: float = Field(gt=0.0)
    paired_negative_ids: list[str]
