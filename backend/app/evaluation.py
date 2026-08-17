"""Deterministic, identity-first comparison helpers for regression datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any


# ``task_key`` is deliberately not a field comparison: it is task identity.
# Assignee and dates are also deliberately absent from matching.  They are
# evaluated *after* a task has been matched so a wrong owner is one field error,
# rather than one missing task plus one unexpected task.
TASK_FIELDS = (
    "task_name",
    "assignee",
    "start_date",
    "due_date",
    "due_date_text",
    "date_mention_id",
    "deadline_mention_id",
    "date_resolution_status",
    "due_date_resolution_status",
    "status",
)
TASK_NAME_THRESHOLD = 0.72


def _text(value: Any) -> str:
    return str(value or "").strip()


TASK_NAME_STOPWORDS = {
    "anh", "chi", "em", "task", "viec", "phan", "va", "cho", "cac",
    "the", "a", "an", "to", "for",
}


def _normalized(value: Any) -> str:
    text = _text(value).casefold().replace("đ", "d")
    text = "".join(
        character
        for character in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"\w+", text))


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalized(value).split()
        if token not in TASK_NAME_STOPWORDS
    }


def _task_name_score(expected: Any, actual: Any) -> float:
    expected_text = _normalized(expected)
    actual_text = _normalized(actual)
    if not expected_text or not actual_text:
        return 0.0
    if expected_text == actual_text:
        return 1.0
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)
    if not expected_tokens or not actual_tokens:
        return 0.0
    intersection = len(expected_tokens & actual_tokens)
    containment = intersection / min(len(expected_tokens), len(actual_tokens))
    jaccard = intersection / len(expected_tokens | actual_tokens)
    sequence = SequenceMatcher(None, expected_text, actual_text).ratio()
    return max(containment * 0.9 + jaccard * 0.1, sequence)


def _identity_score(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    """Return an identity-only score; zero means these tasks cannot be paired."""

    expected_key = _text(expected.get("task_key"))
    actual_key = _text(actual.get("task_key"))
    if expected_key or actual_key:
        return 1.0 if expected_key and expected_key == actual_key else 0.0
    score = _task_name_score(expected.get("task_name"), actual.get("task_name"))
    return score if score >= TASK_NAME_THRESHOLD else 0.0


def _maximum_weight_matching(
    expected_tasks: list[dict[str, Any]], actual_tasks: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """Find the maximum-weight bipartite match without field-based identity.

    Hungarian matching is global rather than greedy and has no practical task
    count ceiling.  Dummy columns permit unmatched expected items.  Match count
    is weighted ahead of fuzzy score so a valid identity is never discarded in
    favour of a slightly higher single similarity score.
    """

    if not expected_tasks or not actual_tasks:
        return []
    scores = [
        [_identity_score(expected, actual) for actual in actual_tasks]
        for expected in expected_tasks
    ]
    row_count = len(expected_tasks)
    actual_count = len(actual_tasks)
    column_count = actual_count + row_count
    # Cost is negative because the standard algorithm minimizes.  Every valid
    # identity gets a large cardinality bonus; dummy and invalid pairs are zero.
    costs = [
        [
            -(1_000_000_000 + round(score * 1_000_000)) if score else 0
            for score in row
        ] + [0] * row_count
        for row in scores
    ]
    potentials_row = [0] * (row_count + 1)
    potentials_column = [0] * (column_count + 1)
    matched_row_for_column = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        matched_row_for_column[0] = row
        column0 = 0
        min_cost = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column0] = True
            active_row = matched_row_for_column[column0]
            delta = float("inf")
            next_column = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                value = costs[active_row - 1][column - 1] - potentials_row[active_row] - potentials_column[column]
                if value < min_cost[column]:
                    min_cost[column] = value
                    predecessor[column] = column0
                if min_cost[column] < delta:
                    delta = min_cost[column]
                    next_column = column
            for column in range(column_count + 1):
                if used[column]:
                    potentials_row[matched_row_for_column[column]] += delta
                    potentials_column[column] -= delta
                else:
                    min_cost[column] -= delta
            column0 = next_column
            if matched_row_for_column[column0] == 0:
                break
        while True:
            previous_column = predecessor[column0]
            matched_row_for_column[column0] = matched_row_for_column[previous_column]
            column0 = previous_column
            if column0 == 0:
                break
    assignment = [-1] * row_count
    for column in range(1, actual_count + 1):
        row = matched_row_for_column[column]
        if row and scores[row - 1][column - 1]:
            assignment[row - 1] = column - 1
    return [(row, column) for row, column in enumerate(assignment) if column >= 0]


def _field_equal(field_name: str, expected_value: str, actual_value: str) -> bool:
    if field_name == "task_name":
        return _task_name_score(expected_value, actual_value) >= TASK_NAME_THRESHOLD
    if field_name in {"assignee", "status", "date_resolution_status", "due_date_resolution_status"}:
        return expected_value.casefold() == actual_value.casefold()
    return expected_value == actual_value


def _metric_name(field_name: str) -> str | None:
    return {
        "assignee": "assignee_accuracy",
        "due_date": "due_date_accuracy",
        "due_date_text": "due_date_text_exact_accuracy",
        "date_mention_id": "date_mention_link_accuracy",
        "deadline_mention_id": "date_mention_link_accuracy",
        "date_resolution_status": "date_resolution_status_accuracy",
        "due_date_resolution_status": "date_resolution_status_accuracy",
        "status": "state_link_accuracy",
    }.get(field_name)


@dataclass
class CaseComparison:
    case_id: str
    passed: bool
    expected_task_count: int
    actual_task_count: int
    matched_task_count: int
    missing_tasks: list[dict[str, Any]] = field(default_factory=list)
    unexpected_tasks: list[dict[str, Any]] = field(default_factory=list)
    field_errors: list[dict[str, str]] = field(default_factory=list)
    checked_field_count: int = 0
    correct_field_count: int = 0
    field_counts: dict[str, tuple[int, int]] = field(default_factory=dict)


def compare_case(case_id: str, expected: dict[str, Any], actual: dict[str, Any]) -> CaseComparison:
    """Compare identities globally, then compare the expected task fields."""

    expected_tasks = list(expected.get("tasks", []))
    actual_tasks = list(actual.get("tasks", []))
    pairs = _maximum_weight_matching(expected_tasks, actual_tasks)
    matched_actual = {actual_index for _, actual_index in pairs}
    matched_expected = {expected_index for expected_index, _ in pairs}
    field_errors: list[dict[str, str]] = []
    field_counts: dict[str, tuple[int, int]] = {}
    checked = correct = 0

    for expected_index, actual_index in pairs:
        expected_task = expected_tasks[expected_index]
        actual_task = actual_tasks[actual_index]
        for field_name in TASK_FIELDS:
            if field_name not in expected_task:
                continue
            checked += 1
            expected_value = _text(expected_task.get(field_name))
            actual_value = _text(actual_task.get(field_name))
            is_equal = _field_equal(field_name, expected_value, actual_value)
            if is_equal:
                correct += 1
            metric = _metric_name(field_name)
            if metric:
                metric_checked, metric_correct = field_counts.get(metric, (0, 0))
                field_counts[metric] = (metric_checked + 1, metric_correct + int(is_equal))
            if not is_equal:
                field_errors.append(
                    {
                        "task_name": _text(expected_task.get("task_name")),
                        "field": field_name,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

    missing = [task for index, task in enumerate(expected_tasks) if index not in matched_expected]
    unexpected = [task for index, task in enumerate(actual_tasks) if index not in matched_actual]
    return CaseComparison(
        case_id=case_id,
        passed=not missing and not unexpected and not field_errors,
        expected_task_count=len(expected_tasks),
        actual_task_count=len(actual_tasks),
        matched_task_count=len(pairs),
        missing_tasks=missing,
        unexpected_tasks=unexpected,
        field_errors=field_errors,
        checked_field_count=checked,
        correct_field_count=correct,
        field_counts=field_counts,
    )


def aggregate_results(comparisons: list[CaseComparison]) -> dict[str, Any]:
    expected = sum(item.expected_task_count for item in comparisons)
    actual = sum(item.actual_task_count for item in comparisons)
    matched = sum(item.matched_task_count for item in comparisons)
    checked = sum(item.checked_field_count for item in comparisons)
    correct = sum(item.correct_field_count for item in comparisons)
    passed = sum(item.passed for item in comparisons)
    counts: dict[str, list[int]] = {}
    for comparison in comparisons:
        for name, (field_checked, field_correct) in comparison.field_counts.items():
            aggregate = counts.setdefault(name, [0, 0])
            aggregate[0] += field_checked
            aggregate[1] += field_correct

    result: dict[str, Any] = {
        "case_count": len(comparisons),
        "passed_case_count": passed,
        "failed_case_count": len(comparisons) - passed,
        "case_pass_rate": passed / len(comparisons) if comparisons else 0.0,
        "task_identity_precision": matched / actual if actual else (1.0 if expected == 0 else 0.0),
        "task_identity_recall": matched / expected if expected else (1.0 if actual == 0 else 0.0),
        # Retain old keys for scripts that consumed V1 evaluation output.
        "task_precision": matched / actual if actual else (1.0 if expected == 0 else 0.0),
        "task_recall": matched / expected if expected else (1.0 if actual == 0 else 0.0),
        "field_accuracy": correct / checked if checked else 1.0,
    }
    for metric in (
        "assignee_accuracy",
        "due_date_accuracy",
        "due_date_text_exact_accuracy",
        "date_mention_link_accuracy",
        "date_resolution_status_accuracy",
        "state_link_accuracy",
    ):
        field_checked, field_correct = counts.get(metric, (0, 0))
        result[metric] = field_correct / field_checked if field_checked else None
    return result
