"""Construct clause-target span examples without expected-task model features."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import GroundedSpan, SpanExample
from .context_builder import build_context
from .hashing import canonical_json_hash, sha256_bytes


HARD_NEGATIVE_FLAGS = {
    "QUESTION",
    "SUGGESTION",
    "PAST_COMPLETED",
    "PROGRESS_ONLY",
    "RECAP_ITEM",
    "MUTATION_ONLY",
    "REJECTED_PROPOSAL",
    "HYPOTHETICAL",
}


def read_evidence(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["case_id"], int(row["expected_task_index"]))
            if key in seen:
                raise ValueError("duplicated evidence row")
            seen.add(key)
            if row.get("review_status") != "HUMAN_CONFIRMED" or not row.get("reviewer") or not row.get("reviewed_at"):
                raise ValueError("human-confirmed evidence lacks reviewer/timestamp")
            rows.append(row)
    return rows


def validate_gold_span(span: GroundedSpan, clause_text: str) -> None:
    if span.end > len(clause_text) or clause_text[span.start : span.end] != span.text:
        raise ValueError("gold span is not exact raw substring")


def build_span_examples(
    traces: dict[str, dict[str, Any]],
    evidence_rows: Iterable[dict[str, Any]],
    fold_by_case: dict[str, int],
    *,
    include_gold_case_ids: set[str],
    label_weights: dict[str, float],
) -> list[SpanExample]:
    gold_by_clause: dict[tuple[str, str], list[GroundedSpan]] = defaultdict(list)
    for row in evidence_rows:
        if row["case_id"] not in include_gold_case_ids:
            continue
        evidence = row["action_evidence"]
        gold_by_clause[(row["case_id"], evidence["clause_id"])].append(GroundedSpan(**evidence))
    examples: list[SpanExample] = []
    for case_id in sorted(traces):
        clauses = traces[case_id].get("clauses", [])
        annotations = traces[case_id].get("annotations", {})
        for clause in clauses:
            clause_id = clause["clause_id"]
            gold = gold_by_clause.get((case_id, clause_id), [])
            for span in gold:
                validate_gold_span(span, clause["text_raw"])
            flags = set(annotations.get(clause_id, {}).get("flags", []))
            if gold:
                source_label, weight = "human_confirmed", label_weights["human_confirmed"]
            elif flags & HARD_NEGATIVE_FLAGS:
                source_label, weight = "deterministic_hard_negative", label_weights["deterministic_hard_negative"]
            elif "ACTION_VERB" in flags:
                source_label, weight = "weak_regex", label_weights["weak_regex"]
            else:
                source_label, weight = "unlabeled", label_weights["weak_regex"]
            context, context_clauses, target_start, target_end = build_context(clauses, clause_id)
            input_value = {
                "case_id": case_id,
                "target_clause_id": clause_id,
                "target_clause_text": clause["text_raw"],
                "context": context,
                "target_start_in_context": target_start,
                "target_end_in_context": target_end,
            }
            example_id = sha256_bytes(f"{case_id}\0{clause_id}".encode())
            examples.append(
                SpanExample(
                    example_id=example_id,
                    input_hash=canonical_json_hash(input_value),
                    case_id=case_id,
                    fold_id=fold_by_case[case_id],
                    target_clause_id=clause_id,
                    target_clause_text=clause["text_raw"],
                    context=context,
                    context_clauses=context_clauses,
                    target_start_in_context=target_start,
                    target_end_in_context=target_end,
                    gold_spans=gold,
                    source_label=source_label,
                    weight=weight,
                )
            )
    return examples


def bio_labels(offset_mapping: list[tuple[int, int]], example: SpanExample) -> list[int]:
    labels: list[int] = []
    context_spans = [
        (example.target_start_in_context + span.start, example.target_start_in_context + span.end)
        for span in example.gold_spans
    ]
    for start, end in offset_mapping:
        if start == end or start < example.target_start_in_context or end > example.target_end_in_context:
            labels.append(-100)
            continue
        label = 0
        for gold_start, gold_end in context_spans:
            if start >= gold_start and end <= gold_end:
                label = 1 if start == gold_start else 2
                break
        labels.append(label)
    return labels
