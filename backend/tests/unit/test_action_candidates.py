from dataclasses import asdict

from backend.app.candidate import CandidateState, build_action_candidates, build_action_proposals_v3
from backend.app.models import Clause, ClauseAnnotation, DateMention, MeetingInput
from backend.app.pipeline import process_meeting


def _clause(identifier: str, text: str, order: int) -> Clause:
    return Clause(identifier, "S", "SPK", "Lan", None, None, text, text.casefold(), [], order)


def _mention(clause_id: str) -> DateMention:
    return DateMention("DATE-1", clause_id, "deadline thứ Sáu", "ON_DATE", "WEEKDAY")


def test_builder_keeps_raw_action_owner_and_deadline_span_grounded() -> None:
    clauses = [_clause("C-1", "Lan, em hoàn thành tài liệu trước thứ Sáu.", 0)]
    annotations = {"C-1": ClauseAnnotation("C-1", {"DIRECT_ASSIGNMENT", "ACTION_VERB", "DATE_MENTION"})}

    candidate = build_action_candidates(clauses, annotations, {"DATE-1": _mention("C-1")})[0]

    assert candidate.candidate_kind == "CREATE"
    assert candidate.action_spans[0].text == "hoàn thành tài liệu"
    assert candidate.owner_spans[0].text == "Lan"
    assert candidate.deadline_mention_ids == ("DATE-1",)
    assert candidate.action_spans[0].clause_id == "C-1"


def test_builder_links_question_then_acceptance_without_creating_from_question_alone() -> None:
    clauses = [
        _clause("C-1", "Lan, em làm báo cáo được không?", 0),
        _clause("C-2", "Vâng, em nhận phần đó.", 1),
    ]
    annotations = {
        "C-1": ClauseAnnotation("C-1", {"ROOT_QUESTION", "ACTION_VERB"}),
        "C-2": ClauseAnnotation("C-2", {"CONFIRMATION"}),
    }

    candidates = build_action_candidates(clauses, annotations, {})

    assert len(candidates) == 1
    assert candidates[0].state is CandidateState.ACCEPTED
    assert candidates[0].primary_clause_ids == ("C-1", "C-2")
    assert candidates[0].action_spans[0].text == "làm báo cáo được không"


def test_builder_keeps_negative_action_like_sentence_non_create() -> None:
    clauses = [_clause("C-1", "Em sẽ chuẩn bị báo cáo nếu cần.", 0)]
    annotations = {"C-1": ClauseAnnotation("C-1", {"FIRST_PERSON_COMMITMENT", "ACTION_VERB", "HYPOTHETICAL"})}

    candidate = build_action_candidates(clauses, annotations, {})[0]

    assert candidate.candidate_kind == "UNKNOWN"
    assert candidate.state is CandidateState.REFERENCE_ONLY
    assert candidate.negative_signals == ("HYPOTHETICAL",)


def test_builder_attaches_only_adjacent_standalone_deadline() -> None:
    clauses = [
        _clause("C-1", "Em sẽ chuẩn bị môi trường test.", 0),
        _clause("C-2", "Deadline thứ Sáu.", 1),
    ]
    annotations = {
        "C-1": ClauseAnnotation("C-1", {"FIRST_PERSON_COMMITMENT", "ACTION_VERB"}),
        "C-2": ClauseAnnotation("C-2", {"DATE_MENTION"}),
    }
    candidates = build_action_candidates(clauses, annotations, {"DATE-1": _mention("C-2")})

    assert candidates[0].support_clause_ids == ("C-2",)
    assert candidates[0].deadline_mention_ids == ("DATE-1",)


def test_shadow_builder_never_changes_v1_tasks_or_events() -> None:
    meeting = MeetingInput(
        "action-candidate-shadow", "Action candidate shadow", "2026-08-21",
        "[09:00:00] Lan: Em sẽ gửi báo cáo trước thứ Sáu.",
    )

    baseline = process_meeting(meeting)
    shadow = process_meeting(meeting, action_candidate_builder_mode="shadow")

    assert [asdict(item) for item in shadow.tasks] == [
        asdict(item) for item in baseline.tasks
    ]
    assert shadow.diagnostics.action_candidate_builder_mode == "shadow"
    assert shadow.diagnostics.action_candidate_count == 1
    assert shadow.diagnostics.action_candidate_action_span_count == 1
    assert shadow.diagnostics.action_candidate_kind_counts == {"CREATE": 1}
    assert shadow.diagnostics.action_candidate_builder_error_count == 0


def test_v3_proposal_keeps_grounded_multiclause_contract_and_recap_reference() -> None:
    clauses = [
        _clause("C-1", "Lan, em hoàn thành tài liệu.", 0),
        _clause("C-2", "Deadline thứ Sáu.", 1),
        _clause("C-3", "Tổng kết: Em sẽ hoàn thành tài liệu.", 2),
    ]
    annotations = {
        "C-1": ClauseAnnotation("C-1", {"DIRECT_ASSIGNMENT", "ACTION_VERB"}),
        "C-2": ClauseAnnotation("C-2", {"DATE_MENTION"}),
        "C-3": ClauseAnnotation("C-3", {"ACTION_VERB"}),
    }

    proposals = build_action_proposals_v3(clauses, annotations, {"DATE-1": _mention("C-2")})

    create = proposals[0]
    assert create.proposal_kind == "CREATE"
    assert create.chronological_anchor_clause_id == "C-1"
    assert create.support_clause_ids == ("C-2",)
    assert create.confidence_components["grounded_action"] == 1.0
    assert proposals[1].proposal_kind == "REFERENCE"
