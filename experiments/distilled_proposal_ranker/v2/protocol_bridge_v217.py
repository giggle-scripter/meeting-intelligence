"""Inference-valid occurrence-to-task bridge for the V2.17 feasibility audit.

The bridge deliberately consumes only runtime trace structures at inference:
action occurrences, annotations, events, date mentions, and clauses.  Gold
evidence is accepted by the runner only for scoring and for an explicitly
named oracle diagnostic.  Cross-clause evidence is represented as pointers,
so a deadline or owner can be retrieved from a different clause without
changing the occurrence span itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
import re
from typing import Any, Iterable

from backend.app.models.annotation import DateMention
from backend.app.dates.resolver import resolve_date_mention


TOKEN_RE = re.compile(r"\w+", re.UNICODE)
NEGATIVE_SIGNALS = frozenset({"HYPOTHETICAL", "SUGGESTION_ONLY", "PAST_COMPLETED"})


@dataclass(frozen=True)
class Occurrence:
    clause_id: str
    start: int
    end: int
    text: str
    candidate_id: str = ""
    state: str = ""
    negative_signals: tuple[str, ...] = ()

    def key(self, case_id: str = "") -> tuple[str, str, int, int, str]:
        return (case_id, self.clause_id, self.start, self.end, self.text)


@dataclass(frozen=True)
class RetrievedEvidence:
    authority_clause_id: str = ""
    owner_clause_id: str = ""
    owner: str = ""
    deadline_clause_id: str = ""
    deadline_mention_id: str = ""
    support_clause_ids: tuple[str, ...] = ()
    retrieval: str = "none"


def _normalise(value: Any) -> str:
    text = str(value or "").casefold().replace("đ", "d")
    return " ".join(re.findall(r"\w+", text))


def _lexical_score(left: str, right: str) -> float:
    a, b = set(_normalise(left).split()), set(_normalise(right).split())
    overlap = len(a & b) / len(a | b) if a | b else 0.0
    sequence = SequenceMatcher(None, _normalise(left), _normalise(right)).ratio()
    return 0.8 * overlap + 0.2 * sequence


def runtime_occurrences(trace: dict[str, Any]) -> list[Occurrence]:
    """Return valid source occurrences from the runtime proposal inventory."""

    clauses = {str(c["clause_id"]): str(c.get("text_raw", "")) for c in trace.get("clauses", [])}
    out: dict[tuple[str, int, int, str], Occurrence] = {}
    records = (trace.get("proposal_span_identities_v3") or {}).get("records", [])
    for record in records:
        if not isinstance(record, dict):
            continue
        span = record.get("action_span")
        cid = str(record.get("primary_clause_id", ""))
        if not isinstance(span, dict) or cid not in clauses:
            continue
        start, end, text = int(span.get("start", -1)), int(span.get("end", -1)), str(span.get("text", ""))
        if not (0 <= start < end <= len(clauses[cid]) and clauses[cid][start:end] == text):
            continue
        negatives = tuple(sorted(str(x) for x in record.get("negative_signals", []) if x))
        value = Occurrence(cid, start, end, text, str(record.get("identity_key", "")), str(record.get("state", "")), negatives)
        out.setdefault((cid, start, end, text), value)
    return sorted(out.values(), key=lambda x: (x.clause_id, x.start, x.end, x.text))


def _date_mentions(trace: dict[str, Any]) -> dict[str, DateMention]:
    result: dict[str, DateMention] = {}
    for key, value in (trace.get("date_mentions") or {}).items():
        if not isinstance(value, dict):
            continue
        payload = dict(value)
        payload.setdefault("date_mention_id", key)
        result[str(key)] = DateMention(**{name: payload.get(name) for name in DateMention.__dataclass_fields__})
    return result


def retrieve_cross_clause_evidence(trace: dict[str, Any], occurrence: Occurrence) -> RetrievedEvidence:
    """Retrieve typed evidence pointers across the complete meeting.

    Direct event links are preferred.  If no direct event exists, the bridge
    performs bounded lexical retrieval over runtime events and carries their
    source clause IDs as support.  No gold evidence or final task object is
    consulted.
    """

    events = [x for x in trace.get("events_after_deduplication", []) if isinstance(x, dict)]
    direct = [x for x in events if occurrence.clause_id in set(map(str, x.get("source_clause_ids", [])))]
    if direct:
        event = max(direct, key=lambda x: (float(x.get("confidence", 0.0)), -int(x.get("order_index", 0))))
        retrieval = "direct_event"
    else:
        ranked = sorted(
            events,
            key=lambda x: (_lexical_score(occurrence.text, str(x.get("action_text", ""))), float(x.get("confidence", 0.0))),
            reverse=True,
        )
        event = ranked[0] if ranked and _lexical_score(occurrence.text, str(ranked[0].get("action_text", ""))) >= 0.22 else None
        retrieval = "lexical_event" if event else "none"
    if event is None:
        return RetrievedEvidence()
    source_ids = tuple(dict.fromkeys(str(x) for x in event.get("source_clause_ids", []) if x))
    deadline_id = str(event.get("deadline_mention_id", "") or "")
    owner_clause = source_ids[0] if source_ids else ""
    authority_clause = source_ids[0] if source_ids else ""
    return RetrievedEvidence(
        authority_clause_id=authority_clause,
        owner_clause_id=owner_clause,
        owner=str(event.get("assignee", "") or ""),
        deadline_clause_id=str(((_date_mentions(trace).get(deadline_id) or DateMention(deadline_id, "", "", "", "")).clause_id) or ""),
        deadline_mention_id=deadline_id,
        support_clause_ids=source_ids,
        retrieval=retrieval,
    )


def bridge_occurrence_to_task(
    trace: dict[str, Any], occurrence: Occurrence, *, meeting_date: str,
) -> tuple[dict[str, Any] | None, RetrievedEvidence]:
    """Convert one runtime occurrence into a task object and evidence pointers."""

    evidence = retrieve_cross_clause_evidence(trace, occurrence)
    if not evidence.retrieval:
        return None, evidence
    events = [x for x in trace.get("events_after_deduplication", []) if isinstance(x, dict)]
    matching = [x for x in events if set(map(str, x.get("source_clause_ids", []))) & set(evidence.support_clause_ids)]
    if not matching:
        return None, evidence
    event = max(matching, key=lambda x: (float(x.get("confidence", 0.0)), -int(x.get("order_index", 0))))
    task_name = str(event.get("action_text", "") or occurrence.text).strip()
    task: dict[str, Any] = {
        "task_name": task_name,
        "assignee": evidence.owner,
        "start_date": meeting_date,
        "due_date": "",
        "due_date_text": "",
        "status": "Proposed",
    }
    mentions = _date_mentions(trace)
    mention = mentions.get(evidence.deadline_mention_id)
    if mention:
        task["due_date"] = resolve_date_mention(meeting_date, meeting_date, mention)
        task["due_date_text"] = mention.raw_text
    return task, evidence


def deduplicate_tasks(tasks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate bridged tasks by identity fields while preserving order."""

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for task in tasks:
        key = (_normalise(task.get("task_name")), _normalise(task.get("assignee")))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        result.append(task)
    return result


def occurrence_token_metrics(
    predicted: Iterable[Occurrence], gold: Iterable[Occurrence], *, case_id: str,
) -> dict[str, float]:
    """Compute strict token F1 from BIO token overlap, independent of text F1."""

    def tokens(items: Iterable[Occurrence]) -> set[tuple[str, int, int]]:
        result: set[tuple[str, int, int]] = set()
        for item in items:
            for match in TOKEN_RE.finditer(item.text):
                result.add((item.clause_id, item.start + match.start(), item.start + match.end()))
        return result
    p, g = tokens(predicted), tokens(gold)
    hit = len(p & g); precision = hit / len(p) if p else 0.0; recall = hit / len(g) if g else 0.0
    return {"precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
