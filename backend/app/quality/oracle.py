"""Review-backed oracle accounting for the quality V2 roadmap.

This module deliberately does not participate in production routing.  It turns
an immutable Q2 baseline report plus reviewed evidence/error records into an
auditable statement of where the remaining error budget is located.  A stage
ceiling is an intervention accounting bound, not a claimed model result.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


EXPECTED_SCHEMA_VERSION = "quality-oracle-review-v2"
REQUIRED_ERROR_KINDS = {"MISSING", "UNEXPECTED", "FIELD_ERROR"}
_MISSING_STAGE = {
    "SOURCE_CANDIDATE_ROUTED_DROP": "candidate",
    "EVENT_CREATED_WRONG_ACTION": "identity",
    "OUTPUT_CANONICALIZATION_MISMATCH": "identity",
    "EVENT_DEDUP_DROPPED": "lifecycle",
}
_UNEXPECTED_STAGE = {
    "FALSE_SOURCE_CANDIDATE": "authority",
    "NEGATIVE_GUARD_MISSED": "authority",
    "MUTATION_MINTED_IDENTITY": "lifecycle",
    "RECAP_DUPLICATE": "lifecycle",
}


def task_f1(*, expected: int, actual: int, matched: int) -> float:
    """Return identity F1 directly from the evaluator's three task counts."""

    denominator = expected + actual
    return (2 * matched / denominator) if denominator else 1.0


def _int_metric(metrics: dict[str, Any], name: str) -> int:
    value = metrics.get(name)
    if value is None:
        raise ValueError(f"baseline metrics missing {name}")
    return int(value)


def validate_review_bundle(
    bundle: dict[str, Any],
    *,
    expected_task_count: int,
    baseline_records: list[dict[str, Any]],
) -> list[str]:
    """Reject incomplete, ungrounded, or stale review evidence.

    The baseline records are the exact records from the pinned Q2 attribution
    artifact.  Binding reviews to their record IDs prevents a later pipeline
    run from silently inheriting classifications for different errors.
    """

    errors: list[str] = []
    if bundle.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        errors.append(f"schema_version must be {EXPECTED_SCHEMA_VERSION}")

    expected_rows = list(bundle.get("expected_tasks", []))
    if len(expected_rows) != expected_task_count:
        errors.append(
            f"expected evidence count {len(expected_rows)} != {expected_task_count}"
        )
    expected_keys: set[tuple[str, int]] = set()
    for row in expected_rows:
        key = (str(row.get("case_id", "")), int(row.get("expected_task_index", -1)))
        if not key[0] or key[1] < 0:
            errors.append("expected evidence has invalid case_id or expected_task_index")
        elif key in expected_keys:
            errors.append(f"duplicate expected evidence for {key[0]}[{key[1]}]")
        expected_keys.add(key)
        if row.get("review_status") != "HUMAN_CONFIRMED" or not str(row.get("reviewer", "")).strip():
            errors.append(f"expected evidence {key[0]}[{key[1]}] is not human-confirmed")
        if not row.get("source_clause_ids"):
            errors.append(f"expected evidence {key[0]}[{key[1]}] has no source clause")
        for span in row.get("action_spans", []):
            if not span.get("clause_id") or int(span.get("end", 0)) <= int(span.get("start", 0)):
                errors.append(f"expected evidence {key[0]}[{key[1]}] has an invalid action span")
        if not row.get("action_spans"):
            errors.append(f"expected evidence {key[0]}[{key[1]}] has no action span")
        if not row.get("authority"):
            errors.append(f"expected evidence {key[0]}[{key[1]}] has no authority")

    baseline_by_kind: dict[str, Counter[str]] = {
        kind: Counter(
            str(record.get("record_id", ""))
            for record in baseline_records
            if record.get("kind") == kind
        )
        for kind in REQUIRED_ERROR_KINDS
    }
    reviews = list(bundle.get("error_reviews", []))
    review_ids: dict[str, Counter[str]] = {kind: Counter() for kind in REQUIRED_ERROR_KINDS}
    for row in reviews:
        kind = str(row.get("kind", ""))
        record_id = str(row.get("attribution_record_id", ""))
        if kind not in REQUIRED_ERROR_KINDS:
            errors.append(f"error review has invalid kind {kind!r}")
            continue
        if record_id not in baseline_by_kind[kind]:
            errors.append(f"error review references stale or unknown {kind} record {record_id}")
        review_ids[kind][record_id] += 1
        if row.get("review_status") != "HUMAN_CONFIRMED" or not str(row.get("reviewer", "")).strip():
            errors.append(f"error review {record_id} is not human-confirmed")
        if not row.get("category"):
            errors.append(f"error review {record_id} has no category")
    for kind, baseline_ids in baseline_by_kind.items():
        missing = baseline_ids - review_ids[kind]
        extra = review_ids[kind] - baseline_ids
        if missing:
            errors.append(f"{kind} reviews missing {sum(missing.values())} baseline records")
        if extra:
            errors.append(f"{kind} reviews contain {sum(extra.values())} unknown baseline records")
    return errors


def validate_grounded_task_evidence(
    rows: list[dict[str, Any]], traces: dict[str, dict[str, Any]],
) -> list[str]:
    """Validate role-specific human evidence against immutable trace text."""
    errors: list[str] = []
    for row in rows:
        key = f"{row.get('case_id', '')}[{row.get('expected_task_index', '')}]"
        trace = traces.get(str(row.get("case_id", "")), {})
        clauses = {item.get("clause_id"): item for item in trace.get("clauses", [])}
        dates = trace.get("date_mentions", {})
        action = row.get("action_evidence") or {}
        clause = clauses.get(action.get("clause_id"))
        start, end = action.get("start"), action.get("end")
        if not clause or not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"{key}: action evidence is not grounded")
        elif start < 0 or end <= start or end > len(clause.get("text_raw", "")) or action.get("text") != clause["text_raw"][start:end]:
            errors.append(f"{key}: action span does not match raw transcript")
        authority = row.get("authority_evidence") or {}
        if not authority.get("clause_id") or authority.get("clause_id") not in clauses or not authority.get("type"):
            errors.append(f"{key}: authority evidence is invalid")
        owner = row.get("owner_evidence")
        if owner and (owner.get("clause_id") not in clauses or owner.get("basis") == "TASK_FINAL_STATE"):
            errors.append(f"{key}: owner evidence is invalid")
        deadline = row.get("deadline_evidence")
        if deadline:
            mention = dates.get(deadline.get("mention_id"), {})
            if not mention or mention.get("clause_id") != deadline.get("clause_id"):
                errors.append(f"{key}: deadline evidence is invalid")
        if row.get("review_status") == "HUMAN_CONFIRMED":
            if not row.get("reviewer") or not row.get("reviewed_at") or "SUGGESTION" in str(row.get("review_basis", "")):
                errors.append(f"{key}: human confirmation metadata is invalid")
    return errors


def _stage_count(reviews: list[dict[str, Any]], *, kind: str, stage: str) -> int:
    mapping = _MISSING_STAGE if kind == "MISSING" else _UNEXPECTED_STAGE
    return sum(
        1 for item in reviews
        if item.get("kind") == kind and mapping.get(item.get("category")) == stage
    )


def build_oracle_report(
    baseline_metrics: dict[str, Any], bundle: dict[str, Any]
) -> dict[str, Any]:
    """Calculate independent intervention ceilings from confirmed error buckets."""

    expected = _int_metric(baseline_metrics, "expected_task_count")
    actual = _int_metric(baseline_metrics, "actual_task_count")
    matched = _int_metric(baseline_metrics, "matched_task_count")
    reviews = list(bundle.get("error_reviews", []))
    missing = Counter(
        item.get("category", "") for item in reviews if item.get("kind") == "MISSING"
    )
    unexpected = Counter(
        item.get("category", "") for item in reviews if item.get("kind") == "UNEXPECTED"
    )

    def ceiling(name: str, recovered: int = 0, removed: int = 0) -> dict[str, Any]:
        result_matched = matched + recovered
        result_actual = actual + recovered - removed
        return {
            "stage": name,
            "recovered_missing": recovered,
            "removed_unexpected": removed,
            "expected_task_count": expected,
            "actual_task_count": result_actual,
            "matched_task_count": result_matched,
            "task_identity_f1_ceiling": round(
                task_f1(expected=expected, actual=result_actual, matched=result_matched), 6
            ),
        }

    candidate = _stage_count(reviews, kind="MISSING", stage="candidate")
    authority = _stage_count(reviews, kind="UNEXPECTED", stage="authority")
    identity = _stage_count(reviews, kind="MISSING", stage="identity")
    lifecycle_recovered = _stage_count(reviews, kind="MISSING", stage="lifecycle")
    lifecycle_removed = _stage_count(reviews, kind="UNEXPECTED", stage="lifecycle")
    return {
        "schema_version": "quality-oracle-ceiling-v2",
        "baseline": {
            "expected_task_count": expected,
            "actual_task_count": actual,
            "matched_task_count": matched,
            "task_identity_f1": round(task_f1(expected=expected, actual=actual, matched=matched), 6),
        },
        "reviewed_error_taxonomy": {
            "missing": dict(sorted(missing.items())),
            "unexpected": dict(sorted(unexpected.items())),
            "field": dict(sorted(
                Counter(item.get("category", "") for item in reviews if item.get("kind") == "FIELD_ERROR").items()
            )),
        },
        "stage_ceilings": [
            ceiling("candidate", recovered=candidate),
            ceiling("authority", removed=authority),
            ceiling("identity", recovered=identity),
            ceiling("lifecycle", recovered=lifecycle_recovered, removed=lifecycle_removed),
            ceiling(
                "full_oracle",
                recovered=expected - matched,
                removed=actual - matched,
            ),
        ],
        "field_error_counts": {
            "owner": sum(1 for item in reviews if item.get("category") == "FIELD_ASSIGNEE_MISMATCH"),
            "deadline": sum(1 for item in reviews if item.get("category") == "FIELD_DUE_DATE_MISMATCH"),
            "deadline_text": sum(1 for item in reviews if item.get("category") == "FIELD_DUE_DATE_TEXT_MISMATCH"),
            "start_date": sum(1 for item in reviews if item.get("category") == "FIELD_START_DATE_MISMATCH"),
        },
        "interpretation": (
            "Each stage is an independent accounting ceiling: it assumes only its "
            "reviewed error bucket is corrected while all other baseline behavior stays "
            "fixed. It is not a production or model-quality result."
        ),
    }
