"""Inference-only identity pruning helpers for the V2.18 TRAIN34 OOF audit.

The module deliberately consumes runtime proposal records and runtime events
only.  Gold occurrences are kept outside these helpers and are used by the
runner solely for fold-local tuning and held-out scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .protocol_bridge_v217 import (
    Occurrence,
    _lexical_score,
    _normalise,
    bridge_occurrence_to_task,
    deduplicate_tasks,
    retrieve_cross_clause_evidence,
    runtime_occurrences,
)


@dataclass(frozen=True)
class RuntimeProposal:
    occurrence: Occurrence
    ranking_score: float
    confidence: float
    retrieval: str
    lexical_score: float = 0.0
    event_id: str = ""
    cluster_id: str = ""
    duplicate_group_key: str = ""

    @property
    def quality(self) -> float:
        """Stable runtime quality score used only to order a meeting pool."""

        return 0.55 * self.ranking_score + 0.45 * self.confidence + (0.03 if self.retrieval == "direct_event" else 0.0)


def _ranking_records(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = (trace.get("proposal_ranking_v3") or {}).get("records", [])
    return {
        str(item.get("identity_key")): item
        for item in records
        if isinstance(item, dict) and item.get("identity_key")
    }


def _event_for(trace: dict[str, Any], occurrence: Occurrence, *, allow_lexical: bool, lexical_threshold: float, events: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, str, float]:
    # ``runtime_proposals`` supplies the meeting event list once.  Keeping the
    # optional argument preserves the small standalone helper API while
    # avoiding an O(proposals * events) list rebuild on every candidate.
    events = events if events is not None else [item for item in trace.get("events_after_deduplication", []) if isinstance(item, dict)]
    direct = [item for item in events if occurrence.clause_id in {str(x) for x in item.get("source_clause_ids", [])}]
    if direct:
        return max(direct, key=lambda item: (float(item.get("confidence", 0.0)), -int(item.get("order_index", 0)))), "direct_event", 1.0
    if not allow_lexical:
        return None, "none", 0.0
    ranked = sorted(
        events,
        key=lambda item: (_lexical_score(occurrence.text, str(item.get("action_text", ""))), float(item.get("confidence", 0.0))),
        reverse=True,
    )
    score = _lexical_score(occurrence.text, str(ranked[0].get("action_text", ""))) if ranked else 0.0
    if ranked and score >= lexical_threshold:
        return ranked[0], "lexical_event", score
    return None, "none", score


def runtime_proposals(
    trace: dict[str, Any],
    *,
    ranking_threshold: float = 0.55,
    confidence_threshold: float = 0.0,
    allow_lexical: bool = True,
    lexical_threshold: float = 0.22,
) -> list[RuntimeProposal]:
    """Build one source-grounded proposal per runtime identity record.

    Duplicate span variants are retained here so the runner can tune whether
    clustering is exact or fuzzy.  Every returned proposal has a runtime event
    and passes the configured score/evidence filters.
    """

    ranking = _ranking_records(trace)
    events = [item for item in trace.get("events_after_deduplication", []) if isinstance(item, dict)]
    result: list[RuntimeProposal] = []
    for occurrence in runtime_occurrences(trace):
        if occurrence.state not in {"PROPOSED", ""} or occurrence.negative_signals:
            continue
        record = ranking.get(occurrence.candidate_id, {})
        score = float(record.get("score", 0.0) or 0.0)
        if score < ranking_threshold:
            continue
        event, retrieval, lexical_score = _event_for(
            trace,
            occurrence,
            allow_lexical=allow_lexical,
            lexical_threshold=lexical_threshold,
            events=events,
        )
        if event is None:
            continue
        confidence = float(event.get("confidence", 0.0) or 0.0)
        if confidence < confidence_threshold:
            continue
        result.append(
            RuntimeProposal(
                occurrence=occurrence,
                ranking_score=score,
                confidence=confidence,
                retrieval=retrieval,
                lexical_score=lexical_score,
                event_id=str(event.get("event_id", "") or ""),
                cluster_id=str(record.get("cluster_id", "") or ""),
                duplicate_group_key=str(record.get("duplicate_group_key", "") or ""),
            )
        )
    return sorted(result, key=lambda item: (-item.quality, item.occurrence.clause_id, item.occurrence.start, item.occurrence.end))


def deduplicate_tasks_v218(tasks: Iterable[dict[str, Any]], *, fuzzy_threshold: float | None = None) -> list[dict[str, Any]]:
    """Deduplicate task identities, optionally merging close same-owner names."""

    if fuzzy_threshold is None:
        return deduplicate_tasks(tasks)
    result: list[dict[str, Any]] = []
    for task in tasks:
        name = _normalise(task.get("task_name"))
        owner = _normalise(task.get("assignee"))
        if not name:
            continue
        duplicate = False
        for previous in result:
            if owner != _normalise(previous.get("assignee")):
                continue
            if _lexical_score(name, _normalise(previous.get("task_name"))) >= fuzzy_threshold:
                duplicate = True
                break
        if not duplicate:
            result.append(task)
    return result


def decode_proposals(
    trace: dict[str, Any],
    proposals: Iterable[RuntimeProposal],
    *,
    meeting_date: str,
    meeting_budget: int | None = None,
    fuzzy_threshold: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Decode quality-ranked proposals and return tasks plus retrieval counts."""

    selected = list(proposals)
    if meeting_budget is not None:
        selected = selected[: max(0, meeting_budget)]
    tasks: list[dict[str, Any]] = []
    retrieval: dict[str, int] = {}
    for proposal in selected:
        retrieval[proposal.retrieval] = retrieval.get(proposal.retrieval, 0) + 1
        task, _ = bridge_occurrence_to_task(trace, proposal.occurrence, meeting_date=meeting_date)
        if task is not None:
            tasks.append(task)
    return deduplicate_tasks_v218(tasks, fuzzy_threshold=fuzzy_threshold), retrieval


__all__ = [
    "RuntimeProposal",
    "decode_proposals",
    "deduplicate_tasks_v218",
    "runtime_proposals",
    "retrieve_cross_clause_evidence",
]
