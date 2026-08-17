from backend.app.models import Clause, ClauseAnnotation, MeetingInput, MeetingNoteInput
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.v2.context.grounding import ground_note_lines
from backend.app.v2.context.cues import apply_note_cues_to_annotations, build_note_cue_index
from backend.app.v2.context.meeting_notes import meeting_note_topic, parse_meeting_note
from backend.app.v2.context.relevance import classify_all_clauses
from backend.app.v2.context.relevance import build_meeting_context
from backend.app.v2.models import NoteLineKind, RelevanceClass
from backend.app.v2.pipeline import process_meeting_v2
from backend.app.pipeline import preprocess_meeting, process_meeting_by_version


def _clause(clause_id: str, text: str) -> Clause:
    return Clause(
        clause_id, "SENT-1", "SPK-1", "Lan", 0, 1, text,
        normalize_for_match(text), order_index=0,
    )


def test_note_question_is_not_promoted_to_action_hint() -> None:
    lines = parse_meeting_note(MeetingNoteInput("- Còn cần chốt người xử lý dashboard không?"))

    assert lines[0].kind is NoteLineKind.QUESTION


def test_explicit_note_topic_is_summary_only_not_an_action_hint() -> None:
    note = MeetingNoteInput(
        "Chủ đề: Chuẩn bị release quý ba\nKW: release, rollback\n- cập nhật checklist"
    )
    lines = parse_meeting_note(note)

    assert lines[0].kind is NoteLineKind.TOPIC
    assert lines[1].kind is NoteLineKind.TOPIC
    assert meeting_note_topic(note) == "Chuẩn bị release quý ba"


def test_note_cannot_ground_to_transcript_by_owner_alone() -> None:
    clause = _clause("CLAUSE-1", "Lan đã gửi tài liệu tuần trước rồi.")
    note = parse_meeting_note(MeetingNoteInput("- Lan: cập nhật dashboard"))

    grounded = ground_note_lines(
        note,
        [clause],
        {clause.clause_id: ClauseAnnotation(clause.clause_id)},
        {},
    )

    assert grounded[0].status == "UNGROUNDED"
    assert grounded[0].clause_ids == ()


def test_confirmation_is_preserved_even_when_it_looks_like_a_short_backchannel() -> None:
    clause = _clause("CLAUSE-1", "OK.")
    relevance = classify_all_clauses(
        [clause],
        {clause.clause_id: ClauseAnnotation(clause.clause_id, {"CONFIRMATION"})},
        [],
        [],
        {},
    )

    assert relevance[clause.clause_id].relevance_class is RelevanceClass.MANDATORY


def test_context_shadow_mode_generates_overview_without_changing_v2_entities() -> None:
    meeting = MeetingInput(
        "M-CONTEXT", "Release", "2026-07-20",
        "Lan: Em sẽ cập nhật dashboard trước thứ Sáu.",
    )

    without_context = process_meeting_v2(meeting, meeting_context_mode="off")
    with_context = process_meeting_v2(meeting, meeting_context_mode="shadow")

    assert with_context.meeting_context is not None
    assert with_context.meeting_context.note_present is False
    assert with_context.meeting_context.note_source == "AUTO_OVERVIEW"
    assert with_context.entities == without_context.entities


def test_positive_human_note_merges_with_more_specific_transcript_task() -> None:
    base = MeetingInput(
        "M-NOTE-AB", "Release", "2026-07-20",
        "Lan: Em sẽ cập nhật dashboard doanh thu trước thứ Sáu.",
    )
    with_note = MeetingInput(
        base.meeting_id, base.meeting_title, base.meeting_date, base.transcript_raw,
        meeting_note=MeetingNoteInput("- Lan cập nhật dashboard trước thứ Sáu"),
    )

    transcript_only = process_meeting_by_version(base, pipeline_version="v1")
    note_result = process_meeting_by_version(with_note, pipeline_version="v1")

    assert len(note_result.tasks) == len(transcript_only.tasks) == 1
    assert note_result.tasks[0].task_name == transcript_only.tasks[0].task_name
    assert note_result.tasks[0].assignee == transcript_only.tasks[0].assignee
    assert note_result.tasks[0].due_date == transcript_only.tasks[0].due_date
    assert "Meeting Note" in note_result.tasks[0].evidence
    assert base.transcript_raw in note_result.tasks[0].evidence


def test_grounded_note_cue_can_promote_transcript_progress_to_local_task() -> None:
    transcript = "Lan: Em đang cập nhật dashboard doanh thu, chiều mai xong."
    base = MeetingInput("M-CUE", "Dashboard", "2026-07-20", transcript)
    with_note = MeetingInput(
        "M-CUE", "Dashboard", "2026-07-20", transcript,
        meeting_note=MeetingNoteInput("- cập nhật dashboard doanh thu"),
    )

    transcript_only = process_meeting_by_version(
        base, pipeline_version="v1", meeting_context_mode="assist"
    )
    note_result = process_meeting_by_version(
        with_note, pipeline_version="v1", meeting_context_mode="assist"
    )

    assert transcript_only.tasks == []
    assert len(note_result.tasks) == 1
    assert note_result.tasks[0].task_name == "Cập nhật dashboard doanh thu"
    assert note_result.tasks[0].assignee == "Lan"
    assert note_result.tasks[0].due_date_text == "chiều mai"
    assert note_result.tasks[0].evidence == transcript


def test_note_owner_and_ungrounded_deadline_never_become_task_fields() -> None:
    transcript = "Lan: Em đang cập nhật dashboard doanh thu."
    meeting = MeetingInput(
        "M-CUE-FIELDS", "Dashboard", "2026-07-20", transcript,
        meeting_note=MeetingNoteInput(
            "- cập nhật dashboard doanh thu (@Minh)\n- deadline: thứ Sáu"
        ),
    )

    result = process_meeting_by_version(
        meeting, pipeline_version="v1", meeting_context_mode="assist"
    )

    assert len(result.tasks) == 1
    assert result.tasks[0].assignee == "Lan"
    assert result.tasks[0].due_date == ""
    assert result.tasks[0].due_date_text == ""


def test_stale_note_does_not_change_rule_output() -> None:
    transcript = "Lan: Em đang cập nhật dashboard doanh thu."
    base = MeetingInput("M-STALE", "Dashboard", "2026-07-20", transcript)
    with_stale_note = MeetingInput(
        "M-STALE", "Dashboard", "2026-07-20", transcript,
        meeting_note=MeetingNoteInput("- xóa dữ liệu production"),
    )

    without_note = process_meeting_by_version(
        base, pipeline_version="v1", meeting_context_mode="assist"
    )
    with_note = process_meeting_by_version(
        with_stale_note, pipeline_version="v1", meeting_context_mode="assist"
    )

    assert with_note.tasks == without_note.tasks == []


def test_positive_human_note_can_create_task_and_fill_missing_fields() -> None:
    meeting = MeetingInput(
        "M-HUMAN-POSITIVE",
        "Dashboard",
        "2026-08-03",
        "Lan: Chúng ta chuyển sang phần khác.",
        meeting_note=MeetingNoteInput(
            '- Task "Cập nhật dashboard doanh thu", owner Lan, deadline thứ Sáu.',
            "Thư ký",
            "SECRETARY",
        ),
    )

    result = process_meeting_by_version(
        meeting, pipeline_version="v1", meeting_context_mode="assist"
    )

    assert len(result.tasks) == 1
    task = result.tasks[0]
    assert task.task_name == "Cập nhật dashboard doanh thu"
    assert task.assignee == "Lan"
    assert task.due_date == "2026-08-07"
    assert task.due_date_text == "deadline thứ Sáu"
    assert task.evidence.startswith("Meeting Note (Thư ký):")


def test_human_note_question_is_context_only_and_auto_overview_is_never_evidence() -> None:
    transcript = "Lan: Chúng ta chuyển sang phần khác."
    question = MeetingInput(
        "M-HUMAN-QUESTION", "Dashboard", "2026-08-03", transcript,
        meeting_note=MeetingNoteInput("- Có thể Lan cập nhật dashboard doanh thu?"),
    )
    auto = MeetingInput(
        "M-AUTO-NOTE", "Dashboard", "2026-08-03", transcript,
        meeting_note=MeetingNoteInput(
            '- Task "Cập nhật dashboard doanh thu", owner Lan.',
            source="AUTO_OVERVIEW",
        ),
    )

    assert process_meeting_by_version(
        question, pipeline_version="v1", meeting_context_mode="assist"
    ).tasks == []
    assert process_meeting_by_version(
        auto, pipeline_version="v1", meeting_context_mode="assist"
    ).tasks == []


def test_explicit_transcript_cancellation_overrides_positive_human_note() -> None:
    meeting = MeetingInput(
        "M-HUMAN-CANCEL",
        "Dashboard",
        "2026-08-03",
        "Lan: Hủy task cập nhật dashboard doanh thu.",
        meeting_note=MeetingNoteInput(
            '- Task "Cập nhật dashboard doanh thu", owner Lan, deadline thứ Sáu.'
        ),
    )

    result = process_meeting_by_version(
        meeting, pipeline_version="v1", meeting_context_mode="assist"
    )

    assert result.tasks == []


def test_positive_human_note_can_supply_explicit_task_start_date() -> None:
    meeting = MeetingInput(
        "M-HUMAN-START",
        "Triển khai",
        "2026-01-17",
        "Lan: Em xác nhận nội dung biên bản.",
        meeting_note=MeetingNoteInput(
            "- Lan triển khai API từ ngày 20/01/2026, deadline 25/01/2026.",
            author="Thư ký",
        ),
    )

    result = process_meeting_by_version(
        meeting, pipeline_version="v1", meeting_context_mode="assist"
    )

    assert result.tasks[0].start_date == "2026-01-20"
    assert result.tasks[0].due_date == "2026-01-25"
    assert result.diagnostics.explicit_task_start_date_count == 1


def test_cue_index_enriches_routing_without_changing_transcript_annotation_fields() -> None:
    meeting = MeetingInput(
        "M-INDEX", "Dashboard", "2026-07-20",
        "Lan: Em đang cập nhật dashboard doanh thu.",
        meeting_note=MeetingNoteInput("- cập nhật dashboard doanh thu"),
    )
    stages = preprocess_meeting(meeting)
    clauses = stages["clauses"]
    annotations = {
        clauses[0].clause_id: ClauseAnnotation(
            clauses[0].clause_id,
            {"ACTION_VERB", "PROGRESS_UPDATE"},
            0.57,
            -2,
        )
    }
    context = build_meeting_context(meeting, clauses, annotations, {})
    cues = build_note_cue_index(context)
    enriched = apply_note_cues_to_annotations(annotations, cues)

    assert "NOTE_GROUNDED_ACTION" in enriched[clauses[0].clause_id].flags
    assert annotations[clauses[0].clause_id].flags == {"ACTION_VERB", "PROGRESS_UPDATE"}
    assert enriched[clauses[0].clause_id].score == annotations[clauses[0].clause_id].score
    assert enriched[clauses[0].clause_id].rule_confidence == annotations[clauses[0].clause_id].rule_confidence
