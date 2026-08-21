from backend.app.candidate import OwnerEvidenceType, build_owner_evidence
from backend.app.models import Clause, TaskEvent


def _clause(text: str, speaker: str = "Lan") -> Clause:
    return Clause(
        clause_id="C-1", sentence_id="S-1", speaker_id="P-1",
        speaker_name=speaker, start_ms=None, end_ms=None, text_raw=text,
        text_normalized=text, order_index=3,
    )


def test_owner_evidence_uses_named_assignment_span() -> None:
    clause = _clause("Minh sẽ hoàn thiện tài liệu API.", "Lan")
    event = TaskEvent("E-1", "OWNER_ASSIGN", ["C-1"], "Hoàn thiện tài liệu API", "Minh")

    evidence = build_owner_evidence(event, {"C-1": clause})

    assert evidence[0].span == "Minh"
    assert evidence[0].evidence_type is OwnerEvidenceType.DIRECT_ASSIGNMENT


def test_owner_evidence_resolves_first_person_to_clause_speaker() -> None:
    clause = _clause("Em sẽ hoàn thiện tài liệu API.", "Minh")
    event = TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Hoàn thiện tài liệu API", "Minh")

    evidence = build_owner_evidence(event, {"C-1": clause})

    assert evidence[0].span == "Minh"
    assert evidence[0].evidence_type is OwnerEvidenceType.SELF_COMMITMENT


def test_owner_evidence_never_uses_previous_speaker_without_span() -> None:
    clause = _clause("Em hãy hoàn thiện tài liệu API.", "Lan")
    event = TaskEvent("E-1", "OWNER_ASSIGN", ["C-1"], "Hoàn thiện tài liệu API", "Minh")

    assert build_owner_evidence(event, {"C-1": clause}) == ()
