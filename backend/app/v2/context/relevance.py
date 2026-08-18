"""Mark clause relevance while preserving every transcript clause in trace."""

from __future__ import annotations

import re

from ...models import Clause, ClauseAnnotation, DateMention, MeetingInput
from .grounding import ground_note_lines
from .keywords import extract_keywords, keyword_overlap
from .meeting_notes import parse_meeting_note
from .note_claim_parser import parse_note_claims
from .note_grounder import ground_note_claims
from .overview import build_overview
from ..models import ClauseRelevance, MeetingContext, RelevanceClass, TopicHint


_BACKCHANNEL = re.compile(r"^(?:ok(?:ay)?|ro|vang|uh|um|cam on|thanks?|duoc)\s*[.!]*$", re.I)
_LOGISTICS = re.compile(r"\b(?:mat tieng|nghe ro|cho .* vao|phong hop|audio check)\b", re.I)
_TASK_LABEL = re.compile(r"\btask\s*[- ]?[a-z0-9]+\b", re.I)
_PRESERVE_FLAGS = {"FIRST_PERSON_COMMITMENT", "DIRECT_ASSIGNMENT", "CONFIRMATION", "CORRECTION", "CANCELLATION", "REJECTION", "DATE_MENTION"}


def _hard_preserve(clause: Clause, annotation: ClauseAnnotation) -> tuple[bool, list[str]]:
    reasons = sorted(_PRESERVE_FLAGS & annotation.flags)
    if _TASK_LABEL.search(clause.text_raw):
        reasons.append("TASK_LABEL")
    return bool(reasons), reasons


def classify_all_clauses(
    clauses: list[Clause],
    annotations: dict[str, ClauseAnnotation],
    topics: list[TopicHint],
    grounded_hints,
    mentions: dict[str, DateMention],
    *,
    topic_likely_threshold: float = 0.45,
) -> dict[str, ClauseRelevance]:
    grounded_by_clause: dict[str, list[str]] = {}
    for hint in grounded_hints:
        if hint.status == "GROUNDED":
            for clause_id in hint.clause_ids:
                grounded_by_clause.setdefault(clause_id, []).append(hint.note_line_id)
    result: dict[str, ClauseRelevance] = {}
    for clause in clauses:
        annotation = annotations[clause.clause_id]
        hard, reasons = _hard_preserve(clause, annotation)
        clause_keywords = extract_keywords(clause.text_raw)
        matched_topics = tuple(
            topic.topic_id
            for topic in topics
            if keyword_overlap(clause_keywords, topic.keywords) >= topic_likely_threshold
        )
        grounded = tuple(grounded_by_clause.get(clause.clause_id, ()))
        normalized = clause.text_normalized
        if hard:
            relevance, score = RelevanceClass.MANDATORY, 100.0
        elif _BACKCHANNEL.match(normalized) or _LOGISTICS.search(normalized):
            relevance, score = RelevanceClass.NOISE, -30.0
            reasons.append("HARD_NOISE")
        elif grounded or matched_topics:
            relevance, score = RelevanceClass.LIKELY, 25.0 if grounded else 10.0
            reasons.append("NOTE_GROUNDED" if grounded else "TOPIC_MATCH")
        else:
            relevance, score = RelevanceClass.CONTEXT, 0.0
        result[clause.clause_id] = ClauseRelevance(clause.clause_id, relevance, score, tuple(reasons), matched_topics, grounded)
    return result


def build_meeting_context(
    meeting: MeetingInput,
    clauses: list[Clause],
    annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention],
    grounding_threshold: float = 0.72,
    grounding_margin: float = 0.12,
    max_topics: int = 12,
    max_topic_keywords: int = 8,
    topic_likely_threshold: float = 0.45,
    note_dual_view_mode: str = "off",
    note_claim_max_transcript_clauses: int = 8,
    note_claim_grounding_threshold: float = 0.72,
    note_claim_grounding_margin: float = 0.12,
) -> MeetingContext:
    if meeting.meeting_note and meeting.meeting_note.content.strip():
        note_lines = parse_meeting_note(meeting.meeting_note)
        topics = [
            TopicHint(f"TOPIC-{index + 1:03d}", line.raw_text, line.keywords[:max_topic_keywords], "MEETING_NOTE", line.confidence)
            for index, line in enumerate(note_lines)
            if line.kind.name in {"ACTION_HINT", "STATE_HINT", "TOPIC"} and line.keywords
        ][:max_topics]
        note_source = meeting.meeting_note.source
    else:
        note_lines = []
        topics = [
            TopicHint(topic.topic_id, topic.label, topic.keywords[:max_topic_keywords], topic.source, topic.confidence)
            for topic in build_overview(meeting, clauses, annotations, max_topics)
        ]
        note_source = "AUTO_OVERVIEW"
    grounded = ground_note_lines(note_lines, clauses, annotations, mentions, grounding_threshold, grounding_margin)
    note_claims = []
    note_claim_groundings = []
    if note_dual_view_mode != "off" and meeting.meeting_note:
        note_claims = parse_note_claims(meeting, note_lines)
        note_claim_groundings = ground_note_claims(
            note_claims, clauses, annotations, mentions,
            max_clauses=note_claim_max_transcript_clauses,
            threshold=note_claim_grounding_threshold,
            margin=note_claim_grounding_margin,
        )
    return MeetingContext(
        note_present=bool(meeting.meeting_note and meeting.meeting_note.content.strip()),
        note_source=note_source,
        note_lines=note_lines,
        topics=topics,
        grounded_hints=grounded,
        clause_relevance=classify_all_clauses(
            clauses, annotations, topics, grounded, mentions,
            topic_likely_threshold=topic_likely_threshold,
        ),
        note_claims=note_claims,
        note_claim_groundings=note_claim_groundings,
    )
