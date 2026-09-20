from backend.app.models import Clause, ClauseAnnotation, MeetingInput, MeetingNoteInput
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.v2.context import ground_note_claims, parse_note_claims, parse_meeting_note
from backend.app.v2.models import NoteGroundingLevel
from backend.app.pipeline import process_meeting_by_version


def _clause(identifier: str, text: str, order: int) -> Clause:
    return Clause(identifier, identifier, "S", "Lan", None, None, text, normalize_for_match(text), [], order)


def _claims(text: str):
    meeting = MeetingInput("M-GROUND", "Dashboard", "2026-08-18", "", meeting_note=MeetingNoteInput(text))
    return parse_note_claims(meeting, parse_meeting_note(meeting.meeting_note))


def test_grounding_levels_cover_full_partial_note_only_and_later_contradiction() -> None:
    full = ground_note_claims(
        _claims("- Lan cập nhật dashboard deadline thứ Sáu"),
        [_clause("C-1", "Lan cập nhật dashboard deadline thứ Sáu.", 1)], {"C-1": ClauseAnnotation("C-1")}, {},
    )[0]
    partial = ground_note_claims(
        _claims("- Lan cập nhật dashboard deadline thứ Sáu"),
        [_clause("C-1", "Lan cập nhật dashboard.", 1)], {"C-1": ClauseAnnotation("C-1")}, {},
    )[0]
    note_only = ground_note_claims(
        _claims("- Lan xóa dữ liệu production"),
        [_clause("C-1", "Lan cập nhật dashboard.", 1)], {"C-1": ClauseAnnotation("C-1")}, {},
    )[0]
    contradicted = ground_note_claims(
        _claims("- Lan cập nhật dashboard"),
        [_clause("C-1", "Lan hủy cập nhật dashboard.", 2)], {"C-1": ClauseAnnotation("C-1", {"CANCELLATION"})}, {},
    )[0]

    assert full.level is NoteGroundingLevel.FULL_GROUNDED
    assert partial.level is NoteGroundingLevel.PARTIAL_GROUNDED
    assert note_only.level is NoteGroundingLevel.NOTE_ONLY
    assert contradicted.level is NoteGroundingLevel.CONTRADICTED


def test_dual_view_assist_suppresses_note_direct_task_authority() -> None:
    meeting = MeetingInput(
        "M-DUAL-ASSIST", "Dashboard", "2026-08-03", "Lan: Chúng ta chuyển sang phần khác.",
        meeting_note=MeetingNoteInput('- Task "Cập nhật dashboard doanh thu", owner Lan, deadline thứ Sáu.'),
    )

    legacy = process_meeting_by_version(meeting, pipeline_version="v1", meeting_context_mode="assist")
    dual = process_meeting_by_version(
        meeting, pipeline_version="v1", meeting_context_mode="assist", note_dual_view_mode="assist"
    )

    assert len(legacy.tasks) == 1
    assert dual.tasks == []
    assert dual.diagnostics.note_direct_event_suppressed_count == 1


def test_dual_view_shadow_keeps_note_output_authoritative_path_unchanged() -> None:
    meeting = MeetingInput(
        "M-DUAL-SHADOW", "Dashboard", "2026-08-03", "Lan: Em đang cập nhật dashboard doanh thu.",
        meeting_note=MeetingNoteInput("- Lan cập nhật dashboard doanh thu"),
    )

    baseline = process_meeting_by_version(meeting, pipeline_version="v1", meeting_context_mode="assist")
    shadow = process_meeting_by_version(
        meeting, pipeline_version="v1", meeting_context_mode="assist", note_dual_view_mode="shadow"
    )

    assert shadow.tasks == baseline.tasks
    assert shadow.diagnostics.note_claim_count == 1
    assert shadow.diagnostics.note_direct_event_suppressed_count == 0
