"""Fail-closed safety counters required by the locked experiment."""

from __future__ import annotations

from dataclasses import dataclass, field


SAFETY_COUNTERS = (
    "unknown_clause_id_count",
    "invalid_action_span_count",
    "unknown_date_mention_id_count",
    "unknown_task_reference_count",
    "model_resolved_date_count",
    "model_minted_task_id_count",
    "update_minted_identity_count",
    "note_only_create_count",
    "question_without_acceptance_create_count",
    "suggestion_only_create_count",
    "past_completed_create_count",
    "recap_duplicate_count",
    "mutation_only_create_count",
    "sibling_unsafe_merge_count",
    "cross_fold_training_leak_count",
    "gold_feature_leak_count",
    "provider_budget_violation_count",
    "teacher_schema_error_accepted_count",
    "public_output_schema_change_count",
    "runtime_artifact_committed_count",
)


@dataclass
class SafetyReport:
    counters: dict[str, int] = field(default_factory=lambda: {name: 0 for name in SAFETY_COUNTERS})
    failures: list[str] = field(default_factory=list)

    def violation(self, counter: str, detail: str) -> None:
        if counter not in self.counters:
            raise KeyError(counter)
        self.counters[counter] += 1
        self.failures.append(f"{counter}:{detail}")

    def model_dump(self) -> dict:
        return {
            "schema_version": "distillation-safety-v1",
            **self.counters,
            "total_violations": sum(self.counters.values()),
            "failures": self.failures,
        }


def enforce_zero_safety(report: dict) -> None:
    missing = [name for name in SAFETY_COUNTERS if name not in report]
    if missing:
        raise ValueError(f"missing safety counters: {missing}")
    nonzero = [name for name in SAFETY_COUNTERS if report[name] != 0]
    if nonzero:
        raise ValueError(f"SAFETY_GATE_FAILED: {nonzero}")
