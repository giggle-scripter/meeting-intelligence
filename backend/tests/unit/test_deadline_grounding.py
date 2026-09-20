from backend.app.candidate import DeadlineAttachmentType, build_deadline_attachment_evidence
from backend.app.models import Clause, DateMention, TaskEvent


def _clause(clause_id: str, order: int, text: str) -> Clause:
    return Clause(clause_id, f"S-{order}", "P-1", "Lan", None, None, text, text, order)


def test_deadline_attachment_prefers_same_action_source() -> None:
    event = TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết tài liệu", deadline_mention_id="D-1")
    mention = DateMention("D-1", "C-1", "ngày mai", "ON_DATE", "RELATIVE_DAY")

    evidence = build_deadline_attachment_evidence(event, {"D-1": mention}, {"C-1": _clause("C-1", 1, "Lan sẽ viết tài liệu ngày mai.")})

    assert evidence and evidence.attachment_type is DeadlineAttachmentType.SAME_ACTION_SOURCE


def test_deadline_attachment_uses_only_adjacent_support_not_nearest_task() -> None:
    event = TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết tài liệu", deadline_mention_id="D-1")
    mention = DateMention("D-1", "C-3", "ngày mai", "ON_DATE", "RELATIVE_DAY")
    clauses = {"C-1": _clause("C-1", 1, "Lan sẽ viết tài liệu."), "C-3": _clause("C-3", 3, "Deadline là ngày mai.")}

    evidence = build_deadline_attachment_evidence(event, {"D-1": mention}, clauses)

    assert evidence and evidence.attachment_type is DeadlineAttachmentType.UNRESOLVED
