from backend.app.models import TaskEvent
from backend.app.reduction import (
    ReconciliationOperation,
    TaskLedger,
    build_deterministic_reconciliation_operations,
    deduplicate_events,
    is_promotable_task_reference,
    reconcile_ledger,
    reduce_task_events_to_ledger,
)
from backend.app.reduction.task_ledger import (
    LedgerTask,
    candidate_aliases_conflict,
)


def test_provisional_promotion_label_guard() -> None:
    assert is_promotable_task_reference("Viết migration script")
    assert is_promotable_task_reference("API draft cho CRM")
    assert not is_promotable_task_reference("Phụ thuộc vào nhau")
    assert not is_promotable_task_reference("Đều tracking trên board nhé")
    assert not is_promotable_task_reference(
        "Phân tích thanh toán có overlap về thời gian không"
    )
    assert not is_promotable_task_reference("Sandbox hoàn thành trước")


def test_ai_deadline_does_not_promote_vague_provisional_reference() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                "Phụ thuộc vào nhau",
                extraction_source="RULE_REFERENCE",
                confidence=0.88,
            ),
            _event(
                "E-2",
                "DEADLINE_REPLACE",
                "Dời deadline",
                related_task_id="TASK-000001",
                deadline_mention_id="D-1",
                extraction_source="AI",
            ),
        ]
    )

    assert ledger.tasks["TASK-000001"].status == "PROVISIONAL"
    assert ledger.active_tasks() == []
    assert ledger.diagnostics["provisional_promotion_blocked_count"] == 1


def test_ai_deadline_does_not_promote_concrete_provisional_reference() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                "Viết migration script",
                extraction_source="RULE_REFERENCE",
                confidence=0.88,
            ),
            _event(
                "E-2",
                "DEADLINE_REPLACE",
                "Dời deadline migration script",
                related_task_id="TASK-000001",
                deadline_mention_id="D-1",
                extraction_source="AI",
            ),
        ]
    )

    task = ledger.tasks["TASK-000001"]
    assert task.status == "PROVISIONAL"
    assert task.deadline_mention_id == "D-1"
    assert ledger.active_tasks() == []
    assert ledger.diagnostics["provisional_task_promoted_count"] == 0
    assert ledger.diagnostics["provisional_promotion_blocked_count"] == 1


def test_rule_corroborated_ai_deadline_promotes_concrete_reference() -> None:
    events = deduplicate_events(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                "Tích hợp module thanh toán",
                extraction_source="RULE_REFERENCE",
                confidence=0.92,
                order_index=1,
            ),
            _event(
                "E-2",
                "TASK_REFERENCE",
                "Tích hợp module thanh toán",
                extraction_source="RULE_REFERENCE",
                confidence=0.92,
                order_index=2,
                source_clause_ids=["C-2"],
            ),
            _event(
                "E-3",
                "DEADLINE_REPLACE",
                related_task_hint="deadline mới là 5 tháng 3",
                deadline_mention_id="D-NEW",
                extraction_source="RULE",
                anchor_clause_id="C-9",
                order_index=9,
            ),
            _event(
                "E-4",
                "DEADLINE_REPLACE",
                "Tích hợp module thanh toán",
                related_task_id="TASK-000001",
                deadline_mention_id="D-NEW",
                extraction_source="AI",
                anchor_clause_id="C-9",
                order_index=9,
            ),
        ]
    )

    ledger = reduce_task_events_to_ledger(events)

    assert len(events) == 3
    assert events[-1].extraction_source == "AI"
    assert events[-1].corroborated_by_rule is True
    assert ledger.tasks["TASK-000001"].status == "CONFIRMED"
    assert ledger.tasks["TASK-000001"].deadline_mention_id == "D-NEW"
    assert ledger.diagnostics["provisional_task_promoted_count"] == 1


def test_same_anchor_local_recap_makes_ai_deadline_redundant() -> None:
    events = deduplicate_events(
        [
            _event(
                "E-1",
                "DEADLINE_REPLACE",
                related_task_hint="setup môi trường staging",
                deadline_mention_id="D-NEW",
                extraction_source="RULE",
                anchor_clause_id="C-9",
                order_index=9,
            ),
            _event(
                "E-2",
                "OWNER_ASSIGN",
                "Setup môi trường staging",
                "Hùng",
                deadline_mention_id="D-NEW",
                extraction_source="RULE_RECAP",
                anchor_clause_id="C-9",
                order_index=9,
            ),
            _event(
                "E-3",
                "DEADLINE_REPLACE",
                "Chốt Hùng setup môi trường staging",
                related_task_id="TASK-000001",
                deadline_mention_id="D-NEW",
                extraction_source="AI",
                anchor_clause_id="C-9",
                order_index=9,
            ),
        ]
    )

    assert len(events) == 2
    assert {event.extraction_source for event in events} == {
        "RULE",
        "RULE_RECAP",
    }


def test_ai_discourse_transition_cancel_is_dropped_but_task_pause_remains() -> None:
    events = deduplicate_events(
        [
            _event(
                "E-1",
                "TASK_CANCEL",
                "Vậy chúng ta tạm dừng phần tài liệu ở đây",
                related_task_id="TASK-000001",
                extraction_source="AI",
            ),
            _event(
                "E-2",
                "TASK_CANCEL",
                "Tạm dừng task cập nhật tài liệu",
                related_task_id="TASK-000002",
                extraction_source="AI",
            ),
        ]
    )

    assert [event.event_id for event in events] == ["E-2"]


def test_ai_deadline_is_blocked_when_task_id_contains_conflicting_aliases() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_COMMITMENT",
                "Cập nhật kịch bản kiểm thử triển khai",
                "Hoa",
            )
        ]
    )
    ledger.add_alias("TASK-000001", "Cập nhật tài liệu triển khai")
    ledger.tasks["TASK-000001"].identity_aliases.add(
        "Cập nhật tài liệu triển khai"
    )

    result = ledger.apply(
        _event(
            "E-2",
            "DEADLINE_REPLACE",
            "Deadline mới",
            related_task_id="TASK-000001",
            deadline_mention_id="D-NEW",
            extraction_source="AI",
        )
    )

    assert result.status == "AMBIGUOUS_IDENTITY"
    assert ledger.tasks["TASK-000001"].deadline_mention_id == ""
    assert ledger.diagnostics["ambiguous_identity_mutation_blocked_count"] == 1
    assert ledger.diagnostics["unresolved_mutation_count"] == 1


def test_ai_deadline_updates_confirmed_task_with_consistent_aliases() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_COMMITMENT",
                "Cập nhật tài liệu triển khai",
                "Minh",
            )
        ]
    )
    ledger.add_alias("TASK-000001", "Cập nhật tài liệu deploy")

    result = ledger.apply(
        _event(
            "E-2",
            "DEADLINE_REPLACE",
            "Dời deadline tài liệu triển khai",
            related_task_id="TASK-000001",
            deadline_mention_id="D-NEW",
            extraction_source="AI",
        )
    )

    assert result.status == "EXACT_ID"
    assert ledger.tasks["TASK-000001"].deadline_mention_id == "D-NEW"
    assert ledger.diagnostics["ambiguous_identity_mutation_blocked_count"] == 0


def test_candidate_aliases_exclude_generic_or_conflicting_identity_labels() -> None:
    task = LedgerTask(
        task_id="TASK-000001",
        canonical_action="Hoàn thiện tài liệu hướng dẫn triển khai staging",
        aliases={
            "Hoàn thiện tài liệu hướng dẫn triển khai staging – chị Hà",
            "Soạn tài liệu",
            "Cập nhật dashboard monitoring",
        },
        identity_aliases={
            "Hoàn thiện tài liệu hướng dẫn triển khai staging",
            "Soạn tài liệu",
        },
    )

    assert task.candidate_aliases() == {
        "Hoàn thiện tài liệu hướng dẫn triển khai staging",
        "Hoàn thiện tài liệu hướng dẫn triển khai staging – chị Hà",
    }
    assert not candidate_aliases_conflict(
        task.canonical_action,
        task.candidate_aliases(),
    )
    assert candidate_aliases_conflict(
        task.canonical_action,
        {"Cập nhật dashboard monitoring"},
    )


def test_similarly_named_human_note_sibling_tasks_get_distinct_ids() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_CREATE",
                "Cập nhật kịch bản kiểm thử triển khai",
                extraction_source="HUMAN_NOTE",
                deadline_mention_id="D-TEST",
            ),
            _event(
                "E-2",
                "TASK_CREATE",
                "Cập nhật tài liệu triển khai",
                extraction_source="HUMAN_NOTE",
            ),
            _event(
                "E-3",
                "TASK_CREATE",
                "Cập nhật tài liệu triển khai",
                extraction_source="HUMAN_NOTE",
            ),
        ]
    )

    assert len(ledger.tasks) == 2
    assert {
        task.canonical_action for task in ledger.tasks.values()
    } == {
        "Cập nhật kịch bản kiểm thử triển khai",
        "Cập nhật tài liệu triển khai",
    }
    assert ledger.diagnostics["sibling_identity_split_count"] == 1
    assert ledger.diagnostics["exact_alias_link_count"] == 1


def test_close_owner_assignment_paraphrase_updates_existing_identity() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                "Viết unit test cho module authentication nhé",
                extraction_source="RULE_REFERENCE",
            ),
            _event(
                "E-2",
                "OWNER_ASSIGN",
                "Thêm task viết unit test cho module authentication",
                "Minh",
            ),
        ]
    )

    assert len(ledger.tasks) == 1
    assert ledger.tasks["TASK-000001"].assignees == {"Minh"}
    assert ledger.tasks["TASK-000001"].status == "CONFIRMED"


def test_numbered_reference_rows_remain_distinct_sibling_candidates() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "TASK_REFERENCE",
                "3: Dũng viết tài liệu staging",
                extraction_source="RULE_REFERENCE",
                source_clause_ids=["C-LIST"],
            ),
            _event(
                "E-2",
                "TASK_REFERENCE",
                "4: Dũng viết tài liệu production",
                extraction_source="RULE_REFERENCE",
                source_clause_ids=["C-LIST"],
            ),
            _event(
                "E-3",
                "TASK_REFERENCE",
                "3: Dũng viết tài liệu staging",
                extraction_source="RULE_REFERENCE",
                source_clause_ids=["C-RECAP"],
            ),
        ]
    )

    assert len(ledger.tasks) == 2
    assert {
        task.canonical_action for task in ledger.tasks.values()
    } == {
        "3: Dũng viết tài liệu staging",
        "4: Dũng viết tài liệu production",
    }
    assert ledger.diagnostics["exact_alias_link_count"] == 1


def _event(event_id: str, event_type: str, action: str = "", assignee: str = "", **kwargs) -> TaskEvent:
    return TaskEvent(
        event_id,
        event_type,
        kwargs.pop("source_clause_ids", [event_id.replace("E", "C")]),
        action,
        assignee,
        order_index=kwargs.pop("order_index", int(event_id.split("-")[-1])),
        **kwargs,
    )


def test_create_task_gets_stable_id() -> None:
    ledger = reduce_task_events_to_ledger([_event("E-1", "TASK_COMMITMENT", "Viết API spec", "Lan")])
    assert list(ledger.tasks) == ["TASK-000001"]


def test_exact_alias_updates_existing_task() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Viết API spec", "Lan"),
            _event("E-2", "OWNER_ASSIGN", "Viết API spec", "Minh"),
        ]
    )
    assert len(ledger.tasks) == 1
    assert ledger.diagnostics["exact_alias_link_count"] == 1


def test_dataset_alias_cancel_targets_existing_task() -> None:
    ledger = TaskLedger()
    ledger.apply(_event("E-1", "TASK_COMMITMENT", "Tạo bộ dữ liệu test cho module đăng nhập", "Tuấn"))
    ledger.add_alias("TASK-000001", "Tạo dataset test đăng nhập")
    ledger.apply(
        _event(
            "E-2",
            "TASK_CANCEL",
            related_task_hint="Tạo dataset test đăng nhập",
        )
    )
    assert ledger.tasks["TASK-000001"].status == "CANCELLED"


def test_owner_assign_adds_second_owner() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "OWNER_ASSIGN", "Viết tài liệu triển khai chi tiết", "Tuấn"),
            _event("E-2", "OWNER_ASSIGN", "Viết tài liệu triển khai chi tiết", "Hà"),
        ]
    )
    assert ledger.tasks["TASK-000001"].assignees == {"Tuấn", "Hà"}


def test_task_commitment_does_not_erase_existing_owner() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Viết API spec", "Lan"),
            _event("E-2", "TASK_COMMITMENT", "Viết API spec", "Minh"),
        ]
    )
    assert ledger.tasks["TASK-000001"].assignees == {"Lan", "Minh"}


def test_rule_context_cannot_create_stable_identity() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1", "OWNER_ASSIGN", "Gửi bổ sung tài liệu", "Lan",
                extraction_source="RULE_CONTEXT",
            )
        ]
    )
    assert ledger.tasks == {}
    assert [event.event_id for event in ledger.unresolved_events] == ["E-1"]
    assert ledger.diagnostics["unauthorized_creation_blocked_count"] == 1


def test_unknown_supplied_task_id_is_rejected_without_creation() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1",
                "OWNER_ASSIGN",
                "Viết API spec",
                "Lan",
                related_task_id="TASK-UNKNOWN",
            )
        ]
    )

    assert ledger.tasks == {}
    assert [event.event_id for event in ledger.unresolved_events] == ["E-1"]
    assert ledger.diagnostics["ledger_unknown_task_id_rejection_count"] == 1


def test_rule_context_can_update_an_existing_identity() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Gửi tài liệu kỹ thuật", "Lan"),
            _event(
                "E-2", "OWNER_ASSIGN", "Gửi tài liệu kỹ thuật", "Minh",
                extraction_source="RULE_CONTEXT",
            ),
        ]
    )
    assert len(ledger.tasks) == 1
    assert ledger.tasks["TASK-000001"].assignees == {"Lan", "Minh"}


def test_deterministic_pending_confirmation_can_promote_identity() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event(
                "E-1", "TASK_COMMITMENT",
                "Hoàn thành tài liệu hướng dẫn sử dụng", "Lan",
                extraction_source="RULE_PENDING_CONFIRMATION",
            )
        ]
    )
    assert ledger.tasks["TASK-000001"].canonical_action == (
        "Hoàn thành tài liệu hướng dẫn sử dụng"
    )


def test_owner_reassign_replaces_owner() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "OWNER_ASSIGN", "Viết tài liệu", "Tuấn"),
            _event("E-2", "OWNER_REASSIGN", "Viết tài liệu", "Hà"),
        ]
    )
    assert ledger.tasks["TASK-000001"].assignees == {"Hà"}


def test_deadline_replace_overwrites_old_deadline() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Viết tài liệu", "Tuấn", deadline_mention_id="D-1"),
            _event("E-2", "DEADLINE_REPLACE", related_task_hint="Viết tài liệu", deadline_mention_id="D-2"),
        ]
    )
    assert ledger.tasks["TASK-000001"].deadline_mention_id == "D-2"
    assert ledger.tasks["TASK-000001"].deadline_event_ids == ["E-1", "E-2"]
    assert ledger.tasks["TASK-000001"].deadline_mention_history == ["D-1", "D-2"]


def test_cancelled_task_not_returned_as_active() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Viết tài liệu", "Tuấn"),
            _event("E-2", "TASK_CANCEL", related_task_hint="Viết tài liệu"),
        ]
    )
    assert ledger.active_tasks() == []


def test_positive_mention_does_not_reopen_cancelled_task() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Viết tài liệu", "Tuấn"),
            _event("E-2", "TASK_CANCEL", related_task_hint="Viết tài liệu"),
            _event("E-3", "TASK_COMMITMENT", "Viết tài liệu", "Hà"),
        ]
    )
    assert len(ledger.tasks) == 1
    assert ledger.tasks["TASK-000001"].status == "CANCELLED"
    assert ledger.diagnostics["terminal_replay_blocked_count"] == 1


def test_ambiguous_mutation_is_unresolved() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            _event("E-1", "TASK_COMMITMENT", "Viết API spec", "Lan"),
            _event("E-2", "TASK_COMMITMENT", "Viết API test", "Minh"),
            _event("E-3", "TASK_CANCEL", related_task_hint="Viết API"),
        ]
    )
    assert [item.event_id for item in ledger.unresolved_events] == ["E-3"]
    assert ledger.diagnostics["unresolved_mutation_count"] == 1


def test_same_target_same_anchor_rule_beats_ai_and_unions_sources() -> None:
    events = deduplicate_events(
        [
            _event(
                "E-1", "TASK_CANCEL", related_task_id="TASK-000001",
                anchor_clause_id="C-9", source_clause_ids=["C-8", "C-9"],
                extraction_source="AI", confidence=0.9,
            ),
            _event(
                "E-2", "TASK_CANCEL", related_task_id="TASK-000001",
                anchor_clause_id="C-9", source_clause_ids=["C-9", "C-10"],
                extraction_source="RULE", confidence=0.8,
            ),
        ]
    )
    assert len(events) == 1
    assert events[0].extraction_source == "RULE"
    assert events[0].source_clause_ids == ["C-8", "C-9", "C-10"]


def test_same_action_different_task_ids_not_merged() -> None:
    events = deduplicate_events(
        [
            _event("E-1", "TASK_CANCEL", related_task_id="TASK-000001", anchor_clause_id="C-9"),
            _event("E-2", "TASK_CANCEL", related_task_id="TASK-000002", anchor_clause_id="C-9"),
        ]
    )
    assert len(events) == 2


def test_targeted_ai_mutation_supersedes_unbound_rule_at_same_anchor() -> None:
    events = deduplicate_events(
        [
            _event(
                "E-1", "TASK_CANCEL", related_task_hint="task đó",
                source_clause_ids=["C-9"], extraction_source="RULE",
                order_index=9,
            ),
            _event(
                "E-2", "TASK_CANCEL", related_task_id="TASK-000001",
                anchor_clause_id="C-9", source_clause_ids=["C-8", "C-9"],
                extraction_source="AI", order_index=9,
            ),
        ]
    )

    assert len(events) == 1
    assert events[0].related_task_id == "TASK-000001"
    assert events[0].source_clause_ids == ["C-8", "C-9"]


def test_unbound_rule_is_preserved_when_ai_targets_are_not_unique() -> None:
    events = deduplicate_events(
        [
            _event(
                "E-1", "TASK_CANCEL", related_task_hint="task đó",
                source_clause_ids=["C-9"], extraction_source="RULE",
                order_index=9,
            ),
            _event(
                "E-2", "TASK_CANCEL", related_task_id="TASK-000001",
                anchor_clause_id="C-9", extraction_source="AI", order_index=9,
            ),
            _event(
                "E-3", "TASK_CANCEL", related_task_id="TASK-000002",
                anchor_clause_id="C-9", extraction_source="AI", order_index=9,
            ),
        ]
    )

    assert len(events) == 3


def test_explicit_final_list_sibling_tasks_not_merged() -> None:
    events = deduplicate_events(
        [
            _event("E-1", "OWNER_ASSIGN", "Kiểm tra API", "Lan", extraction_source="RULE_FINAL_RECAP"),
            _event("E-2", "OWNER_ASSIGN", "Kiểm tra API", "Minh", extraction_source="RULE_FINAL_RECAP"),
        ]
    )
    assert len(events) == 2


def test_final_reconciliation_cannot_create_task() -> None:
    ledger = reduce_task_events_to_ledger([_event("E-1", "TASK_COMMITMENT", "Viết API", "Lan")])
    result = reconcile_ledger(
        ledger,
        [
            ReconciliationOperation(
                "MERGE_TASKS",
                primary_task_id="TASK-000001",
                duplicate_task_ids=("TASK-999999",),
            )
        ],
    )
    assert list(result.ledger.tasks) == ["TASK-000001"]
    assert result.rejected_operations[0]["reason"] == "INVALID_OR_UNSAFE_MERGE"


def test_deterministic_reconciliation_merges_one_unique_compatible_duplicate() -> None:
    ledger = TaskLedger()
    ledger.create_task(_event("E-1", "TASK_COMMITMENT", "Chuẩn bị test dataset", "Lan"))
    ledger.create_task(_event("E-2", "TASK_COMMITMENT", "Chuẩn bị bộ dữ liệu test", "Lan"))

    operations = build_deterministic_reconciliation_operations(ledger)
    result = reconcile_ledger(ledger, operations)

    assert len(operations) == 1
    assert len(result.ledger.tasks) == 1
    assert result.ledger.diagnostics["duplicate_task_merge_count"] == 1


def test_deterministic_reconciliation_rejects_owner_conflict() -> None:
    ledger = TaskLedger()
    ledger.create_task(_event("E-1", "TASK_COMMITMENT", "Chuẩn bị test dataset", "Lan"))
    ledger.create_task(_event("E-2", "TASK_COMMITMENT", "Chuẩn bị bộ dữ liệu test", "Minh"))

    operations = build_deterministic_reconciliation_operations(ledger)

    assert operations == []


def test_extra_long_checkpoint_resume() -> None:
    ledger = reduce_task_events_to_ledger([_event("E-1", "TASK_COMMITMENT", "Viết API", "Lan")])
    checkpoint = ledger.to_checkpoint("meeting-1", 500, {"input_tokens": 100})
    resumed = TaskLedger.from_checkpoint(checkpoint)
    resumed.apply(_event("E-2", "TASK_CANCEL", related_task_hint="Viết API", order_index=501))
    assert resumed.tasks["TASK-000001"].status == "CANCELLED"
    assert resumed.tasks["TASK-000001"].terminal_order_index == 501

    terminal_checkpoint = resumed.to_checkpoint("meeting-1", 600)
    terminal_resumed = TaskLedger.from_checkpoint(terminal_checkpoint)
    assert terminal_resumed.tasks["TASK-000001"].terminal_order_index == 501


def test_cancellation_links_across_distant_chunks() -> None:
    ledger = TaskLedger()
    ledger.apply(_event("E-1", "TASK_COMMITMENT", "Tạo bộ dữ liệu test đăng nhập", "Tuấn"))
    ledger.add_alias("TASK-000001", "Tạo dataset test đăng nhập")
    ledger.apply(
        _event(
            "E-2", "TASK_CANCEL", related_task_hint="Tạo dataset test đăng nhập",
            order_index=900,
        )
    )
    assert ledger.tasks["TASK-000001"].status == "CANCELLED"


def test_reassignment_links_across_distant_chunks() -> None:
    ledger = TaskLedger()
    ledger.apply(_event("E-1", "OWNER_ASSIGN", "Viết tài liệu triển khai", "Lan"))
    ledger.apply(
        _event(
            "E-2", "OWNER_REASSIGN", "Viết tài liệu triển khai", "Hà",
            order_index=700,
        )
    )
    assert ledger.tasks["TASK-000001"].assignees == {"Hà"}


def test_deadline_replace_uses_ledger_candidate() -> None:
    ledger = TaskLedger()
    ledger.apply(_event("E-1", "TASK_COMMITMENT", "Cấu hình monitoring", "Tuấn"))
    ledger.apply(
        _event(
            "E-2", "DEADLINE_REPLACE", related_task_id="TASK-000001",
            deadline_mention_id="D-NEW", order_index=1200,
        )
    )
    assert ledger.tasks["TASK-000001"].deadline_mention_id == "D-NEW"
