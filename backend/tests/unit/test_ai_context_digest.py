from backend.app.ai.event_extractor import extract_events_by_ai
from backend.app.ai.schemas import AiEvent, AiEventResponse
from backend.app.models import (
    CandidateWindow,
    Clause,
    ClauseAnnotation,
    MeetingInput,
    MeetingNoteInput,
    TaskEvent,
)
from backend.app.pipeline import _build_ai_task_memory, process_meeting


class CapturingAiClient:
    enabled = True

    def __init__(self) -> None:
        self.payload = None

    def extract_events(self, payload: dict) -> AiEventResponse:
        self.payload = payload
        return AiEventResponse(events=[])


class AnchoredAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="DEADLINE_REPLACE",
                    anchor_clause_id="CLAUSE-000009",
                    source_clause_ids=["CLAUSE-000001", "CLAUSE-000009"],
                    related_task_hint="API spec",
                    related_task_id="TASK-000001",
                    confidence="HIGH",
                )
            ]
        )


def test_ai_event_uses_primary_anchor_not_earlier_evidence_for_order() -> None:
    clauses = {
        "CLAUSE-000001": Clause(
            "CLAUSE-000001", "S-1", "SPK-1", "Lan", None, None,
            "Viết API spec", "viet api spec", order_index=1,
        ),
        "CLAUSE-000009": Clause(
            "CLAUSE-000009", "S-9", "SPK-2", "Minh", None, None,
            "Dời phần đó sang thứ Sáu", "doi phan do sang thu sau", order_index=9,
        ),
    }
    annotations = {
        clause_id: ClauseAnnotation(clause_id) for clause_id in clauses
    }
    events = extract_events_by_ai(
        CandidateWindow(
            "WIN-1",
            ["CLAUSE-000009"],
            ["CLAUSE-000001", "CLAUSE-000009"],
            4,
            "AI",
        ),
        clauses,
        annotations,
        {},
        AnchoredAiClient(),
        task_memory=[
            {
                "task_id": "TASK-000001",
                "canonical_action": "Viết API spec",
                "aliases": [],
                "assignees": ["Lan"],
                "status": "CONFIRMED",
            }
        ],
    )

    assert len(events) == 1
    assert events[0].order_index == 9


def test_ai_task_memory_contains_only_active_tasks_before_window() -> None:
    clauses = {
        "CLAUSE-000001": Clause(
            "CLAUSE-000001", "S-1", "SPK-1", "Lan", None, None,
            "Viết API spec", "viet api spec", order_index=1,
        ),
        "CLAUSE-000002": Clause(
            "CLAUSE-000002", "S-2", "SPK-2", "Minh", None, None,
            "Chuẩn bị UAT", "chuan bi uat", order_index=2,
        ),
        "CLAUSE-000009": Clause(
            "CLAUSE-000009", "S-9", "SPK-2", "Minh", None, None,
            "Dời phần API sang thứ Sáu", "doi phan api sang thu sau", order_index=9,
        ),
        "CLAUSE-000010": Clause(
            "CLAUSE-000010", "S-10", "SPK-3", "Hoa", None, None,
            "Viết release note", "viet release note", order_index=10,
        ),
    }
    events = [
        TaskEvent("E-1", "TASK_COMMITMENT", ["CLAUSE-000001"], "Viết API spec", "Lan", order_index=1),
        TaskEvent("E-2", "TASK_COMMITMENT", ["CLAUSE-000002"], "Chuẩn bị UAT", "Minh", order_index=2),
        TaskEvent("E-3", "TASK_CANCEL", ["CLAUSE-000003"], related_task_hint="Chuẩn bị UAT", order_index=3),
        TaskEvent("E-4", "TASK_COMMITMENT", ["CLAUSE-000010"], "Viết release note", "Hoa", order_index=10),
    ]

    memory = _build_ai_task_memory(
        events,
        CandidateWindow(
            "WIN-1", ["CLAUSE-000009"], ["CLAUSE-000009"], 4, "AI"
        ),
        clauses,
    )

    assert [item["canonical_action"] for item in memory] == ["Viết API spec"]


def test_ai_task_memory_preserves_similarly_named_sibling_work_items() -> None:
    clauses = {
        "CLAUSE-000009": Clause(
            "CLAUSE-000009", "S-9", "SPK-1", "Lan", None, None,
            "Deadline mới của tài liệu triển khai là 26/02.",
            "deadline moi cua tai lieu trien khai la 26/02",
            order_index=9,
        )
    }
    events = [
        TaskEvent(
            "E-1", "TASK_CREATE", ["NOTE-CLAUSE-001"],
            "Cập nhật kịch bản kiểm thử triển khai",
            extraction_source="HUMAN_NOTE", order_index=-2,
        ),
        TaskEvent(
            "E-2", "TASK_CREATE", ["NOTE-CLAUSE-002"],
            "Cập nhật tài liệu triển khai",
            extraction_source="HUMAN_NOTE", order_index=-1,
        ),
        TaskEvent(
            "E-3", "TASK_CREATE", ["NOTE-CLAUSE-003"],
            "Cập nhật tài liệu triển khai",
            extraction_source="HUMAN_NOTE", order_index=0,
        ),
    ]

    memory = _build_ai_task_memory(
        events,
        CandidateWindow(
            "WIN-SIBLINGS", ["CLAUSE-000009"], ["CLAUSE-000009"], 4, "AI"
        ),
        clauses,
    )

    assert len(memory) == 2
    by_action = {item["canonical_action"]: item for item in memory}
    assert set(by_action) == {
        "Cập nhật kịch bản kiểm thử triển khai",
        "Cập nhật tài liệu triển khai",
    }
    assert (
        by_action["Cập nhật kịch bản kiểm thử triển khai"]["task_id"]
        != by_action["Cập nhật tài liệu triển khai"]["task_id"]
    )
    assert "Cập nhật tài liệu triển khai" not in set(
        by_action["Cập nhật kịch bản kiểm thử triển khai"]["aliases"]
    )


def test_ai_task_memory_hides_loose_context_aliases_from_identity() -> None:
    clauses = {
        "CLAUSE-000009": Clause(
            "CLAUSE-000009", "S-9", "SPK-1", "Lan", None, None,
            "Dời deadline môi trường test.",
            "doi deadline moi truong test",
            order_index=9,
        )
    }
    events = [
        TaskEvent(
            "E-1", "TASK_COMMITMENT", ["CLAUSE-000001"],
            "Chuẩn bị môi trường test", "Lan", order_index=1,
        ),
        TaskEvent(
            "E-2", "TASK_REFERENCE", ["CLAUSE-000002"],
            "Chuẩn bị dữ liệu mẫu test", extraction_source="RULE_REFERENCE",
            order_index=2,
        ),
    ]

    memory = _build_ai_task_memory(
        events,
        CandidateWindow(
            "WIN-SAFE-ALIASES", ["CLAUSE-000009"], ["CLAUSE-000009"], 4, "AI"
        ),
        clauses,
    )

    task = next(
        item for item in memory
        if item["canonical_action"] == "Chuẩn bị môi trường test"
    )
    assert "Chuẩn bị dữ liệu mẫu test" not in task["aliases"]


def test_ai_payload_marks_task_memory_as_target_resolution_only() -> None:
    client = CapturingAiClient()
    clause = Clause(
        "CLAUSE-000009", "S-9", "SPK-2", "Minh", None, None,
        "Dời phần đó sang thứ Sáu", "doi phan do sang thu sau", order_index=9,
    )
    extract_events_by_ai(
        CandidateWindow("WIN-1", [clause.clause_id], [clause.clause_id], 4, "AI"),
        {clause.clause_id: clause},
        {clause.clause_id: ClauseAnnotation(clause.clause_id)},
        {},
        client,
        task_memory=[
            {
                "task_id": "TASK-000001",
                "canonical_action": "Viết API spec",
                "assignee": "Lan",
                "status": "CONFIRMED",
                "last_order_index": 1,
                "source_clause_ids": ["CLAUSE-000001"],
            }
        ],
    )

    supplemental = client.payload["supplemental_context"]
    assert supplemental["task_memory_authority"] == "TARGET_RESOLUTION_ONLY"
    assert supplemental["task_memory"][0]["canonical_action"] == "Viết API spec"


def test_ai_without_candidate_task_is_not_called() -> None:
    client = CapturingAiClient()

    process_meeting(
        MeetingInput(
            "M-DIGEST",
            "API Review",
            "2026-08-03",
            "Nam: Phần API chắc Minh xử lý nhé.",
        ),
        client,
    )

    assert client.payload is None


def test_note_cue_without_candidate_task_does_not_trigger_provider() -> None:
    client = CapturingAiClient()

    process_meeting(
        MeetingInput(
            "M-NOTE-DIGEST",
            "Dashboard",
            "2026-08-03",
            "Lan: Có nên cập nhật dashboard doanh thu không?",
            meeting_note=MeetingNoteInput(
                "- cập nhật dashboard doanh thu (@Minh), deadline: thứ Sáu"
            ),
        ),
        client,
        meeting_context_mode="assist",
    )

    assert client.payload is None


class UnknownTaskIdAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        primary_id = payload["primary_clause_ids"][0]
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="TASK_CANCEL",
                    anchor_clause_id=primary_id,
                    source_clause_ids=[primary_id],
                    related_task_id="TASK-999999",
                    confidence="HIGH",
                )
            ]
        )


def test_unknown_ai_task_id_is_rejected() -> None:
    clause = Clause(
        "CLAUSE-000009", "S-9", "SPK-2", "Minh", None, None,
        "Hủy task API", "huy task api", order_index=9,
    )
    diagnostics = {}
    events = extract_events_by_ai(
        CandidateWindow("WIN-1", [clause.clause_id], [clause.clause_id], 4, "AI"),
        {clause.clause_id: clause},
        {clause.clause_id: ClauseAnnotation(clause.clause_id)},
        {},
        UnknownTaskIdAiClient(),
        task_memory=[{"task_id": "TASK-000001", "canonical_action": "Viết API spec"}],
        contract_diagnostics=diagnostics,
    )
    assert events == []
    assert diagnostics["rejection_count"] == 1
    assert diagnostics["structural_rejection_count"] == 1
    assert diagnostics["unknown_task_id_rejection_count"] == 1


class NonConcreteOwnerAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        primary_id = payload["primary_clause_ids"][0]
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="OWNER_ASSIGN",
                    action_text="",
                    assignee="Minh",
                    anchor_clause_id=primary_id,
                    source_clause_ids=[primary_id],
                    related_task_id="TASK-000001",
                    confidence="HIGH",
                )
            ]
        )


def test_semantic_ai_rejection_is_reported_separately() -> None:
    clause = Clause(
        "CLAUSE-000009", "S-9", "SPK-2", "Minh", None, None,
        "Giao việc đó cho Minh", "giao viec do cho minh", order_index=9,
    )
    diagnostics = {}

    events = extract_events_by_ai(
        CandidateWindow("WIN-1", [clause.clause_id], [clause.clause_id], 4, "AI"),
        {clause.clause_id: clause},
        {clause.clause_id: ClauseAnnotation(clause.clause_id)},
        {},
        NonConcreteOwnerAiClient(),
        task_memory=[{"task_id": "TASK-000001", "canonical_action": "Viết API spec"}],
        contract_diagnostics=diagnostics,
    )

    assert events == []
    assert diagnostics["rejection_count"] == 1
    assert diagnostics["semantic_rejection_count"] == 1
    assert diagnostics["non_concrete_action_rejection_count"] == 1
    assert diagnostics.get("structural_rejection_count", 0) == 0


class DiscourseTransitionCancelAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        primary_id = payload["primary_clause_ids"][0]
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="TASK_CANCEL",
                    action_text="Vậy chúng ta tạm dừng phần tài liệu ở đây",
                    anchor_clause_id=primary_id,
                    source_clause_ids=[primary_id],
                    related_task_id="TASK-000001",
                    confidence="HIGH",
                )
            ]
        )


def test_ai_discourse_transition_is_not_accepted_as_task_cancel() -> None:
    clause = Clause(
        "CLAUSE-000009", "S-9", "SPK-2", "Lan", None, None,
        "Vậy chúng ta tạm dừng phần tài liệu ở đây.",
        "vay chung ta tam dung phan tai lieu o day", order_index=9,
    )
    diagnostics = {}

    events = extract_events_by_ai(
        CandidateWindow("WIN-1", [clause.clause_id], [clause.clause_id], 4, "AI"),
        {clause.clause_id: clause},
        {clause.clause_id: ClauseAnnotation(clause.clause_id)},
        {},
        DiscourseTransitionCancelAiClient(),
        task_memory=[{"task_id": "TASK-000001", "canonical_action": "Cập nhật tài liệu"}],
        contract_diagnostics=diagnostics,
    )

    assert events == []
    assert diagnostics["semantic_rejection_count"] == 1
    assert diagnostics["non_concrete_action_rejection_count"] == 1
