"""Deterministic first-divergence suggestions for evaluation failures.

The output is deliberately conservative: it points reviewers to clauses and
pipeline provenance, but all classifications remain ``NEEDS_REVIEW`` until a
human accepts them.  Nothing in this module is imported by production routing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import re
from typing import Any

from ..evaluation import CaseComparison
from ..preprocessing.unicode_normalizer import normalize_for_match
from .review_models import AttributionKind, AttributionRecord, GroundedEvidence


_STOPWORDS = {
    "anh", "chi", "em", "toi", "minh", "task", "viec", "phan", "va", "voi",
    "cho", "cac", "the", "a", "an", "to", "for", "and", "duoc", "nhung",
}
_NEGATIVE_FLAGS = {
    "BRAINSTORM", "HYPOTHETICAL", "PAST_COMPLETED", "FUTURE_DISCUSSION",
    "ROOT_QUESTION", "SUGGESTION_ONLY", "ADMIN_FOLLOWUP", "REJECTION",
    "CANCELLATION",
}
_MUTATION_EVENTS = {
    "OWNER_ASSIGN", "OWNER_REASSIGN", "DEADLINE_SET", "DEADLINE_REPLACE",
    "TASK_CANCEL", "TASK_REJECT",
}


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"\w+", normalize_for_match(value))
        if len(token) > 1 and token not in _STOPWORDS
    }


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _record_id(case_id: str, kind: AttributionKind, task: dict[str, Any]) -> str:
    key = (
        f"{case_id}|{kind.value}|{task.get('task_key', '')}|"
        f"{task.get('task_name', '')}|{task.get('assignee', '')}"
    )
    return "ATTR-" + sha256(key.encode("utf-8")).hexdigest()[:16]


def _task_view(task: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(task.get(key, ""))
        for key in ("task_name", "assignee", "start_date", "due_date", "due_date_text", "status")
        if task.get(key) not in (None, "")
    }


def _suggested_sources(task: dict[str, Any], clauses: list[dict[str, Any]]) -> tuple[GroundedEvidence, ...]:
    action = str(task.get("task_name", ""))
    candidates: list[GroundedEvidence] = []
    for clause in clauses:
        score = _similarity(action, str(clause.get("text_raw", "")))
        if score >= 0.12:
            candidates.append(GroundedEvidence(
                clause_id=str(clause.get("clause_id", "")),
                text=str(clause.get("text_raw", "")), score=round(score, 4),
            ))
    return tuple(sorted(candidates, key=lambda item: (-item.score, item.clause_id))[:3])


def _window_ids(source_ids: set[str], windows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(
        str(window.get("window_id", ""))
        for window in windows
        if source_ids.intersection(window.get("primary_clause_ids", []))
    ))


def _missing_record(
    case_id: str,
    task: dict[str, Any],
    *,
    metadata: dict[str, Any],
    clauses: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> AttributionRecord:
    evidence = _suggested_sources(task, clauses)
    source_ids = {item.clause_id for item in evidence}
    related_events = [
        event for event in events
        if source_ids.intersection(event.get("source_clause_ids", []))
        or _similarity(str(task.get("task_name", "")), str(event.get("action_text", ""))) >= 0.34
    ]
    related_states = [
        state for state in states
        if source_ids.intersection(state.get("source_clause_ids", []))
        or _similarity(str(task.get("task_name", "")), str(state.get("task_name", ""))) >= 0.34
    ]
    if not evidence:
        taxonomy, confidence, note = "NO_SOURCE_CANDIDATE", 0.72, "No lexical source evidence was found in transcript clauses."
    elif not related_events:
        taxonomy, confidence, note = "SOURCE_CANDIDATE_ROUTED_DROP", 0.62, "Source-like clauses exist but no related event was emitted."
    elif not related_states:
        taxonomy, confidence, note = "EVENT_DEDUP_DROPPED", 0.56, "A related event exists but no related ledger task state survives."
    elif any(_similarity(str(task.get("task_name", "")), str(state.get("task_name", ""))) >= 0.5 for state in related_states):
        taxonomy, confidence, note = "OUTPUT_CANONICALIZATION_MISMATCH", 0.52, "A related state survives but did not align with final output identity."
    else:
        taxonomy, confidence, note = "EVENT_CREATED_WRONG_ACTION", 0.50, "Related provenance exists but action identity differs."
    return AttributionRecord(
        record_id=_record_id(case_id, AttributionKind.MISSING, task),
        case_id=case_id, wave=str(metadata.get("wave", "")),
        labels=tuple(str(item) for item in metadata.get("labels", [])),
        kind=AttributionKind.MISSING, task=_task_view(task),
        suggested_taxonomy=taxonomy, confidence=confidence,
        suggested_source_evidence=evidence,
        candidate_window_ids=_window_ids(source_ids, windows),
        provenance_event_ids=tuple(sorted(str(item.get("event_id", "")) for item in related_events)),
        provenance_task_ids=tuple(sorted(str(item.get("task_id", "")) for item in related_states)),
        notes=(note,),
    )


def attribute_expected_tasks(
    case_id: str,
    expected: dict[str, Any],
    trace: dict[str, Any],
    metadata: dict[str, Any],
) -> list[AttributionRecord]:
    """Create a review-state record for every expected task, matched or not."""

    clauses = list(trace.get("clauses", []))
    windows = list(trace.get("candidate_windows", []))
    records: list[AttributionRecord] = []
    for index, task in enumerate(expected.get("tasks", []), 1):
        evidence = _suggested_sources(task, clauses)
        source_ids = {item.clause_id for item in evidence}
        records.append(AttributionRecord(
            record_id=_record_id(case_id, AttributionKind.EXPECTED_EVIDENCE, {
                **task, "task_key": str(index),
            }),
            case_id=case_id, wave=str(metadata.get("wave", "")),
            labels=tuple(str(item) for item in metadata.get("labels", [])),
            kind=AttributionKind.EXPECTED_EVIDENCE, task=_task_view(task),
            suggested_taxonomy="EXPECTED_TASK_EVIDENCE", confidence=(0.72 if evidence else 0.0),
            suggested_source_evidence=evidence,
            candidate_window_ids=_window_ids(source_ids, windows),
            notes=("Source/action evidence is a deterministic suggestion and requires reviewer confirmation.",),
        ))
    return records


def _unexpected_record(
    case_id: str,
    task: dict[str, Any],
    *,
    metadata: dict[str, Any],
    clauses_by_id: dict[str, dict[str, Any]],
    annotations: dict[str, dict[str, Any]],
    windows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> AttributionRecord:
    action = str(task.get("task_name", ""))
    related_states = [
        state for state in states
        if _similarity(action, str(state.get("task_name", ""))) >= 0.34
    ]
    source_ids = {
        source_id for state in related_states for source_id in state.get("source_clause_ids", [])
    }
    related_events = [
        event for event in events
        if source_ids.intersection(event.get("source_clause_ids", []))
        or _similarity(action, str(event.get("action_text", ""))) >= 0.45
    ]
    evidence = tuple(
        GroundedEvidence(
            clause_id=clause_id,
            text=str(clauses_by_id.get(clause_id, {}).get("text_raw", "")),
            score=round(_similarity(action, str(clauses_by_id.get(clause_id, {}).get("text_raw", ""))), 4),
            role="EVENT_PROVENANCE",
        )
        for clause_id in sorted(source_ids)
    )[:3]
    flags = {
        flag for clause_id in source_ids
        for flag in annotations.get(clause_id, {}).get("flags", [])
    }
    if any(event.get("extraction_source") == "RULE_FINAL_RECAP" for event in related_events):
        taxonomy, confidence, note = "RECAP_DUPLICATE", 0.65, "Unexpected identity has final-recap provenance."
    elif flags & _NEGATIVE_FLAGS:
        taxonomy, confidence, note = "NEGATIVE_GUARD_MISSED", 0.70, "Source provenance contains an explicit negative creation signal."
    elif any(event.get("event_type") in _MUTATION_EVENTS for event in related_events):
        taxonomy, confidence, note = "MUTATION_MINTED_IDENTITY", 0.60, "Unexpected task has mutation-event provenance."
    elif len(_tokens(action)) <= 1:
        taxonomy, confidence, note = "ACTION_SPAN_TOO_BROAD", 0.58, "Final action is too generic to identify an independent deliverable."
    else:
        taxonomy, confidence, note = "FALSE_SOURCE_CANDIDATE", 0.48, "Event/task provenance requires reviewer classification."
    return AttributionRecord(
        record_id=_record_id(case_id, AttributionKind.UNEXPECTED, task),
        case_id=case_id, wave=str(metadata.get("wave", "")),
        labels=tuple(str(item) for item in metadata.get("labels", [])),
        kind=AttributionKind.UNEXPECTED, task=_task_view(task),
        suggested_taxonomy=taxonomy, confidence=confidence,
        suggested_source_evidence=evidence,
        candidate_window_ids=_window_ids(source_ids, windows),
        provenance_event_ids=tuple(sorted(str(item.get("event_id", "")) for item in related_events)),
        provenance_task_ids=tuple(sorted(str(item.get("task_id", "")) for item in related_states)),
        notes=(note,),
    )


def attribute_case(
    case_id: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    comparison: CaseComparison,
    trace: dict[str, Any],
    metadata: dict[str, Any],
) -> list[AttributionRecord]:
    """Return review queue records for missing/unexpected/failing fields."""

    del expected, actual  # Comparison and trace are the authoritative inputs here.
    clauses = list(trace.get("clauses", []))
    clauses_by_id = {str(item.get("clause_id", "")): item for item in clauses}
    annotations = dict(trace.get("annotations", {}))
    windows = list(trace.get("candidate_windows", []))
    events = list(trace.get("events_after_deduplication", []))
    states = list(trace.get("task_states", []))
    records = [
        _missing_record(case_id, task, metadata=metadata, clauses=clauses, windows=windows, events=events, states=states)
        for task in comparison.missing_tasks
    ]
    records.extend(
        _unexpected_record(case_id, task, metadata=metadata, clauses_by_id=clauses_by_id,
                           annotations=annotations, windows=windows, events=events, states=states)
        for task in comparison.unexpected_tasks
    )
    for error in comparison.field_errors:
        task = {"task_name": error["task_name"], "actual": error["actual"], "expected": error["expected"]}
        records.append(AttributionRecord(
            record_id=_record_id(case_id, AttributionKind.FIELD_ERROR, task),
            case_id=case_id, wave=str(metadata.get("wave", "")),
            labels=tuple(str(item) for item in metadata.get("labels", [])),
            kind=AttributionKind.FIELD_ERROR, task=_task_view(task),
            suggested_taxonomy=f"FIELD_{error['field'].upper()}_MISMATCH", confidence=1.0,
            notes=("Identity matched; this is a field-level grounding discrepancy.",),
        ))
    return records


def summarize_attribution(records: list[AttributionRecord]) -> dict[str, Any]:
    by_kind = Counter(record.kind.value for record in records)
    taxonomy = Counter(record.suggested_taxonomy for record in records)
    by_wave: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in records:
        by_wave[record.wave or "unknown"][record.suggested_taxonomy] += 1
    missing = [record for record in records if record.kind is AttributionKind.MISSING]
    expected = [record for record in records if record.kind is AttributionKind.EXPECTED_EVIDENCE]
    source_covered = sum(bool(record.suggested_source_evidence) for record in missing)
    expected_source_covered = sum(bool(record.suggested_source_evidence) for record in expected)
    return {
        "record_count": len(records),
        "records_by_kind": dict(sorted(by_kind.items())),
        "suggested_taxonomy_counts": dict(sorted(taxonomy.items())),
        "missing_source_coverage": {
            "covered": source_covered,
            "total": len(missing),
            "rate": source_covered / len(missing) if missing else 1.0,
        },
        "expected_task_source_coverage": {
            "covered": expected_source_covered,
            "total": len(expected),
            "rate": expected_source_covered / len(expected) if expected else 1.0,
        },
        "missing_stage_provenance": {
            "candidate_window": sum(bool(record.candidate_window_ids) for record in missing),
            "event": sum(bool(record.provenance_event_ids) for record in missing),
            "task_state": sum(bool(record.provenance_task_ids) for record in missing),
            "total_missing": len(missing),
        },
        "by_wave": {wave: dict(sorted(counts.items())) for wave, counts in sorted(by_wave.items())},
        "review_status_counts": {"NEEDS_REVIEW": len(records)},
    }
