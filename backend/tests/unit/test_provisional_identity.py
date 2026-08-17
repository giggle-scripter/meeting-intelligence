from backend.app.models import Clause, TaskEvent
from backend.app.reduction import (
    TaskLedger,
    extract_provisional_task_references,
    reduce_task_events_to_ledger,
)


def _clause(text: str, order: int = 0) -> Clause:
    return Clause(
        clause_id=f"CLAUSE-{order + 1:06d}",
        sentence_id=f"SENTENCE-{order + 1:06d}",
        speaker_id="SPEAKER-NAM",
        speaker_name="Nam",
        start_ms=None,
        end_ms=None,
        text_raw=text,
        text_normalized=text,
        order_index=order,
    )


def _event(event_id: str, event_type: str, **kwargs) -> TaskEvent:
    return TaskEvent(
        event_id=event_id,
        event_type=event_type,
        source_clause_ids=kwargs.pop("source_clause_ids", ["CLAUSE-000001"]),
        order_index=kwargs.pop("order_index", int(event_id.split("-")[-1])),
        **kwargs,
    )


def test_extracts_quoted_and_concrete_unquoted_task_references() -> None:
    events = extract_provisional_task_references(
        [
            _clause('Task "Viết tài liệu API" đang ở trạng thái Open.'),
            _clause("Về task tích hợp module thanh toán, mình cần xác nhận.", 1),
        ]
    )

    assert [event.action_text for event in events] == [
        "Viết tài liệu API",
        "Tích hợp module thanh toán",
    ]
    assert events[0].extraction_source == "RULE_REFERENCE_ACTIVE"
    assert events[1].extraction_source == "RULE_REFERENCE"


def test_rejects_pronoun_and_ambiguous_task_labels() -> None:
    events = extract_provisional_task_references(
        [
            _clause("Task đó để sau."),
            _clause("Có thể làm riêng, review nhé.", 1),
            _clause("Mình chưa tạo task chính thức.", 2),
        ]
    )

    assert events == []


def test_reference_is_candidate_but_not_active_until_deadline_promotes_it() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                action_text="Tích hợp module thanh toán",
                extraction_source="RULE_REFERENCE",
            ),
            _event(
                "E-2",
                "DEADLINE_REPLACE",
                related_task_hint="Tích hợp module thanh toán",
                deadline_mention_id="DATE-NEW",
            ),
        ]
    )

    assert len(ledger.candidate_tasks()) == 1
    assert len(ledger.active_tasks()) == 1
    assert ledger.tasks["TASK-000001"].status == "CONFIRMED"
    assert ledger.tasks["TASK-000001"].deadline_mention_id == "DATE-NEW"
    assert ledger.diagnostics["provisional_task_promoted_count"] == 1


def test_cancellation_of_provisional_task_never_exports_active_state() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                action_text="Viết tài liệu API",
                extraction_source="RULE_REFERENCE",
            ),
            _event(
                "E-2",
                "TASK_CANCEL",
                related_task_hint="Viết tài liệu API",
            ),
        ]
    )

    assert ledger.tasks["TASK-000001"].status == "CANCELLED"
    assert ledger.active_tasks() == []
    assert ledger.candidate_tasks() == []


def test_reference_cannot_reopen_terminal_task() -> None:
    ledger = TaskLedger()
    ledger.apply(
        _event(
            "E-1",
            "TASK_REFERENCE",
            action_text="Viết tài liệu API",
            extraction_source="RULE_REFERENCE",
        )
    )
    ledger.apply(
        _event("E-2", "TASK_CANCEL", related_task_hint="Viết tài liệu API")
    )
    result = ledger.apply(
        _event(
            "E-3",
            "TASK_REFERENCE",
            action_text="Viết tài liệu API",
            extraction_source="RULE_REFERENCE",
        )
    )

    assert result.status == "TERMINAL_REPLAY"
    assert len(ledger.tasks) == 1
    assert ledger.tasks["TASK-000001"].status == "CANCELLED"


def test_reassignment_promotes_reference_across_long_distance() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                action_text="Viết tài liệu API",
                extraction_source="RULE_REFERENCE",
            ),
            _event(
                "E-2",
                "OWNER_REASSIGN",
                action_text="Viết tài liệu API",
                assignee="Minh",
                order_index=700,
            ),
        ]
    )

    assert ledger.tasks["TASK-000001"].status == "REASSIGNED"
    assert ledger.tasks["TASK-000001"].assignees == {"Minh"}
