from backend.app.candidate import (
    batch_ai_windows,
    build_candidate_windows,
    choose_extraction_strategy,
    merge_windows,
)
from backend.app.annotation.cue_annotator import annotate_clause
from backend.app.models import CandidateWindow, Clause, ClauseAnnotation


def test_windows_merge_and_route_ambiguous_case_to_ai() -> None:
    clauses = [Clause(f"C-{index}", f"S-{index}", "SPK", "Nam", index, index, f"text {index}", f"text {index}", order_index=index) for index in range(8)]
    annotations = {clause.clause_id: ClauseAnnotation(clause.clause_id) for clause in clauses}
    annotations["C-3"] = ClauseAnnotation("C-3", {"DIRECT_ASSIGNMENT", "ACTION_VERB", "DATE_MENTION"}, 0.9, 6)
    annotations["C-5"] = ClauseAnnotation("C-5", {"BRAINSTORM", "ACTION_VERB", "DATE_MENTION"}, 0.6, 0)
    assert choose_extraction_strategy(annotations["C-3"]) == "RULE"
    windows = merge_windows(build_candidate_windows(clauses, annotations))
    assert len(windows) == 1
    assert "C-3" in windows[0].primary_clause_ids


def test_negative_or_date_only_clause_is_context_not_ai() -> None:
    past = ClauseAnnotation(
        "C1",
        {"PAST_COMPLETED", "ACTION_VERB", "DATE_MENTION"},
        0.6,
        3,
    )
    date_only = ClauseAnnotation("C2", {"DATE_MENTION"}, 0.57, 2)
    assert choose_extraction_strategy(past) == "CONTEXT"
    assert choose_extraction_strategy(date_only) == "CONTEXT"


def test_grounded_note_action_routes_progress_locally_but_ambiguous_note_uses_ai() -> None:
    grounded = ClauseAnnotation(
        "C-NOTE-1",
        {"NOTE_GROUNDED_ACTION", "PROGRESS_UPDATE", "ACTION_VERB"},
        0.72,
        0,
    )
    ambiguous = ClauseAnnotation(
        "C-NOTE-2",
        {"NOTE_AMBIGUOUS_ACTION", "PROGRESS_UPDATE", "ACTION_VERB"},
        0.65,
        0,
    )

    assert choose_extraction_strategy(grounded) == "RULE"
    assert choose_extraction_strategy(ambiguous) == "AI"


def test_ambiguous_assignment_and_state_change_route_to_ai() -> None:
    assignment = ClauseAnnotation(
        "C1",
        {"DIRECT_ASSIGNMENT", "ACTION_VERB"},
        0.69,
        4,
    )
    cancellation = ClauseAnnotation("C2", {"CANCELLATION"}, 0.57, 3)
    assert choose_extraction_strategy(assignment) == "AI"
    assert choose_extraction_strategy(cancellation) == "AI"


def test_completed_first_person_report_is_not_mistaken_for_assignment() -> None:
    clause = Clause(
        "C1",
        "S1",
        "SPK",
        "Lan",
        0,
        0,
        "Em đã viết xong phần tổng quan.",
        "em đã viết xong phần tổng quan.",
    )
    annotation = annotate_clause(clause)
    assert "PAST_COMPLETED" in annotation.flags
    assert "DIRECT_ASSIGNMENT" not in annotation.flags
    assert choose_extraction_strategy(annotation) == "CONTEXT"


def test_root_question_and_progress_are_context_but_explicit_assignment_is_not() -> None:
    cases = {
        "Có cần viết test UAT không?": "ROOT_QUESTION",
        "Có thắc mắc gì không?": "ROOT_QUESTION",
        "Should we update the dashboard?": "ROOT_QUESTION",
        "Em đang cập nhật dashboard doanh thu.": "PROGRESS_UPDATE",
        "I already sent the report.": "PAST_COMPLETED",
    }
    for index, (text, expected_flag) in enumerate(cases.items()):
        clause = Clause(f"Q-{index}", "S", "SPK", "Lan", 0, 0, text, text.casefold())
        annotation = annotate_clause(clause)
        assert expected_flag in annotation.flags
        assert choose_extraction_strategy(annotation) == "CONTEXT"

    assignment = Clause(
        "A-1", "S", "SPK", "Nam", 0, 0,
        "Giao Tuấn viết báo cáo, em làm thế nào?",
        "giao tuấn viết báo cáo, em làm thế nào?",
    )
    annotation = annotate_clause(assignment)
    assert "DIRECT_ASSIGNMENT" in annotation.flags
    assert "ROOT_QUESTION" not in annotation.flags


def test_nearby_windows_are_coalesced_within_context_limit() -> None:
    clauses = [
        Clause(
            f"CLAUSE-{index:06d}",
            f"S-{index}",
            "SPK",
            "Nam",
            index,
            index,
            f"text {index}",
            f"text {index}",
            order_index=index,
        )
        for index in range(20)
    ]
    annotations = {
        clause.clause_id: ClauseAnnotation(clause.clause_id) for clause in clauses
    }
    for index in (3, 12):
        clause_id = f"CLAUSE-{index:06d}"
        annotations[clause_id] = ClauseAnnotation(
            clause_id,
            {"DIRECT_ASSIGNMENT", "ACTION_VERB"},
            0.69,
            4,
        )
    windows = merge_windows(build_candidate_windows(clauses, annotations))
    assert len(windows) == 1
    assert windows[0].primary_clause_ids == ["CLAUSE-000003", "CLAUSE-000012"]


def test_ai_batches_keep_source_windows_and_respect_context_budget() -> None:
    windows = [
        CandidateWindow(
            window_id=f"WIN-{index}",
            primary_clause_ids=[f"P-{index}"],
            context_clause_ids=[f"C-{index * 2}", f"C-{index * 2 + 1}"],
            score=4,
            extraction_strategy="AI",
        )
        for index in range(3)
    ]

    batches = batch_ai_windows(windows, max_context_clauses=4)

    assert len(batches) == 2
    assert batches[0].source_window_ids == ("WIN-0", "WIN-1")
    assert batches[0].window.primary_clause_ids == ["P-0", "P-1"]
    assert batches[0].primary_clause_ids_by_window["WIN-1"] == frozenset({"P-1"})
    assert batches[1].source_window_ids == ("WIN-2",)


def test_ai_batches_do_not_flatten_distant_windows() -> None:
    windows = [
        CandidateWindow("WIN-1", ["C-5"], ["C-2", "C-5"], 4, "AI"),
        CandidateWindow("WIN-2", ["C-80"], ["C-77", "C-80"], 4, "AI"),
    ]

    batches = batch_ai_windows(windows, max_context_clauses=56)

    assert [batch.source_window_ids for batch in batches] == [
        ("WIN-1",),
        ("WIN-2",),
    ]
