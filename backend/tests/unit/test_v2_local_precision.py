from backend.app.models import MeetingInput
from backend.app.v2.pipeline import process_meeting_v2


def _active_actions(transcript: str) -> list[str]:
    result = process_meeting_v2(
        MeetingInput("M-RULE", "Rule", "2026-08-03", transcript),
        meeting_context_mode="assist",
    )
    return [entity.canonical_action for entity in result.active_entities]


def test_vague_vietnamese_commitments_do_not_create_tasks() -> None:
    assert _active_actions("Lan: Em sẽ làm theo.\nAn: Tôi sẽ cập nhật lại.") == []


def test_question_and_past_work_do_not_create_tasks() -> None:
    assert _active_actions(
        "Alice: Should we update the dashboard?\nBob: I already sent the report."
    ) == []


def test_concrete_bilingual_commitments_still_create_tasks() -> None:
    actions = _active_actions(
        "Lan: Em sẽ cập nhật dashboard doanh thu.\nAlice: I will prepare the release checklist."
    )

    assert len(actions) == 2
    assert any("dashboard" in action.casefold() for action in actions)
    assert any("release checklist" in action.casefold() for action in actions)
