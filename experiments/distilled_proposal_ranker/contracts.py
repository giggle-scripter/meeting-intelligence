"""Strict, versioned data contracts for the locked V1 protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

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
