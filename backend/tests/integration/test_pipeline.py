from backend.app.ai.schemas import AiEvent, AiEventResponse
import httpx
import json
from pathlib import Path

from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


VALIDATION_ROOT = Path(__file__).resolve().parents[3] / "data" / "validation"


def _run_validation_case(case_id: str):
    case_dir = VALIDATION_ROOT / case_id
    metadata = json.loads(
        (case_dir / "metadata.json").read_text(encoding="utf-8")
    )
    return process_meeting(
        MeetingInput(
            metadata["meeting_id"],
            metadata["meeting_title"],
            metadata["meeting_date"],
            (case_dir / "transcript.txt").read_text(encoding="utf-8"),
        )
    )


def test_python_first_pipeline_extracts_clear_bilingual_tasks() -> None:
    transcript = """Phương: Em sẽ chuẩn bị hồ sơ trước thứ Sáu.
Linh: I will update the access matrix by next Tuesday.
Nam: Dashboard có thể để phase sau.
"""
    result = process_meeting(MeetingInput("M-1", "Weekly Sync", "2026-07-20", transcript))
    assert len(result.tasks) == 2
    assert {task.assignee for task in result.tasks} == {"Phương", "Linh"}
    assert {task.due_date for task in result.tasks} == {"2026-07-23", "2026-07-28"}
    assert all(task.evidence for task in result.tasks)


def test_ambiguous_window_is_exposed_for_review_when_ai_disabled() -> None:
    result = process_meeting(MeetingInput("M-2", "API Review", "2026-07-20", "Nam: Phần API chắc Minh xử lý nhé."))
    assert result.tasks == []
    assert result.diagnostics.ai_window_count == 0
    assert result.diagnostics.unresolved_window_count == 0
    assert result.diagnostics.ai_provider_call_count == 0
    assert result.diagnostics.ai_context_clause_count == 0
    assert result.diagnostics.ai_clause_coverage == 0.0


class FailingAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        raise httpx.ConnectError("AI fallback is unavailable")


def test_ai_fallback_failure_keeps_rule_pipeline_available() -> None:
    result = process_meeting(
        MeetingInput("M-2B", "API Review", "2026-07-20", "Nam: Phần API chắc Minh xử lý nhé."),
        FailingAiClient(),
    )
    assert result.tasks == []
    assert result.diagnostics.ai_fallback_error_count == 0
    assert result.diagnostics.ai_provider_call_count == 0
    assert result.diagnostics.unresolved_window_count == 0
    assert result.diagnostics.ai_provider_call_count == 0


class FakeAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        raise AssertionError("mutation resolver must not run without candidate_tasks")


def test_ai_does_not_create_task_for_generic_ambiguous_window() -> None:
    result = process_meeting(MeetingInput("M-3", "API Review", "2026-07-20", "Nam: Phần API chắc Minh xử lý nhé."), FakeAiClient())
    assert result.tasks == []
    assert result.diagnostics.ai_event_count == 0
    assert result.diagnostics.ai_provider_call_count == 0


class ProvisionalDeadlineAiClient:
    enabled = True

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def extract_events(self, payload: dict) -> AiEventResponse:
        self.payloads.append(payload)
        candidate = next(
            item
            for item in payload["candidate_tasks"]
            if "tích hợp module thanh toán" in item["canonical_action"].casefold()
        )
        mention = next(
            item
            for item in payload["date_mentions"]
            if "5 tháng 3" in item["raw_text"]
        )
        anchor = payload["primary_clause_ids"][0]
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="DEADLINE_REPLACE",
                    action_text=candidate["canonical_action"],
                    anchor_clause_id=anchor,
                    source_clause_ids=[anchor],
                    deadline_mention_id=mention["date_mention_id"],
                    related_task_id=candidate["task_id"],
                    related_task_hint=candidate["canonical_action"],
                    confidence="HIGH",
                )
            ]
        )


def test_preexisting_task_reference_supplies_ai_deadline_target() -> None:
    case_dir = VALIDATION_ROOT / "W2-SHORT-C2-N0-IT-DREP-030"
    metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
    client = ProvisionalDeadlineAiClient()

    result = process_meeting(
        MeetingInput(
            metadata["meeting_id"],
            metadata["meeting_title"],
            metadata["meeting_date"],
            (case_dir / "transcript.txt").read_text(encoding="utf-8"),
        ),
        client,
    )

    payment = next(
        task for task in result.tasks
        if task.task_name == "Tích hợp module thanh toán"
    )
    assert client.payloads
    assert any(payload["candidate_tasks"] for payload in client.payloads)
    assert payment.due_date == "2026-03-05"
    assert payment.due_date_text == "ngày 5 tháng 3"
    assert result.diagnostics.ai_provider_call_count >= 1
    assert result.diagnostics.ai_unknown_task_id_rejection_count == 0


def test_explicit_task_discussion_remains_internal_provisional_state() -> None:
    transcript = (
        "Nam: Về task tích hợp CRM, hôm nay chỉ thảo luận phạm vi. "
        "Chưa có owner, deadline hay xác nhận active.\n"
    )

    result = process_meeting(
        MeetingInput("M-PROVISIONAL", "CRM", "2026-08-10", transcript)
    )

    assert result.tasks == []
    assert result.diagnostics.provisional_task_created_count == 1
    assert result.diagnostics.provisional_task_promoted_count == 0


def test_vague_provisional_reference_is_not_sent_as_ai_candidate() -> None:
    result = process_meeting(
        MeetingInput(
            "M-VAGUE-CANDIDATE",
            "Dependencies",
            "2026-08-10",
            "Nam: Về task phụ thuộc vào nhau, hôm nay chỉ thảo luận.\n"
            "Nam: Correction: dời task đó sang ngày 15/08.\n",
        ),
        FakeAiClient(),
    )

    assert result.tasks == []
    assert result.diagnostics.provisional_task_created_count == 1
    assert result.diagnostics.ai_provider_call_count == 0


def test_pronoun_task_reference_does_not_create_provisional_identity() -> None:
    result = process_meeting(
        MeetingInput(
            "M-VAGUE-REFERENCE",
            "CRM",
            "2026-08-10",
            "Nam: Task đó để sau, cái đó chưa cần quyết định.\n",
        )
    )

    assert result.tasks == []
    assert result.diagnostics.provisional_task_created_count == 0


def test_explicit_open_state_promotes_existing_task_reference() -> None:
    result = process_meeting(
        MeetingInput(
            "M-ACTIVE-REFERENCE",
            "API",
            "2026-08-10",
            'Nam: Task "Viết tài liệu API" đang ở trạng thái Open.\n',
        )
    )

    assert [task.task_name for task in result.tasks] == ["Viết tài liệu API"]
    assert result.diagnostics.provisional_task_created_count == 1


class CoreferenceAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="OWNER_ASSIGN",
                    action_text="Còn phần kiểm tra phân quyền trước release?",
                    assignee="Minh",
                    source_clause_ids=payload["clauses"],
                    confidence="HIGH",
                )
            ]
        )


def test_ai_coreference_cannot_create_task_without_ledger_target() -> None:
    transcript = """Nam: Còn phần kiểm tra phân quyền trước release?
Minh: Em nhận phần này.
Nam: Chốt Minh phụ trách nhé.
"""
    result = process_meeting(
        MeetingInput("M-3C", "Access Review", "2026-07-29", transcript),
        CoreferenceAiClient(),
    )
    assert result.tasks == []
    assert result.diagnostics.ai_event_count == 0


class HallucinatedReferenceAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="OWNER_ASSIGN",
                    action_text="Xử lý phần API",
                    assignee="Minh",
                    source_clause_ids=["CLAUSE-NOT-PROVIDED"],
                    deadline_mention_id="DATE-NOT-PROVIDED",
                    confidence="HIGH",
                )
            ]
        )


def test_ai_fallback_rejects_references_not_supplied_by_python() -> None:
    result = process_meeting(
        MeetingInput(
            "M-3B",
            "API Review",
            "2026-07-20",
            "Nam: Phần API chắc Minh xử lý nhé.",
        ),
        HallucinatedReferenceAiClient(),
    )
    assert result.tasks == []
    assert result.diagnostics.ai_provider_call_count == 0
    assert result.diagnostics.unresolved_window_count == 0


class TaskMemoryResolvingAiClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        memory = payload["supplemental_context"]["task_memory"]
        assert memory
        primary_id = payload["primary_clause_ids"][0]
        mention = next(
            item for item in payload["date_mentions"]
            if item["clause_id"] == primary_id
        )
        return AiEventResponse(
            events=[
                AiEvent(
                    event_type="DEADLINE_REPLACE",
                    anchor_clause_id=primary_id,
                    source_clause_ids=[primary_id],
                    deadline_mention_id=mention["date_mention_id"],
                    related_task_hint=memory[0]["canonical_action"],
                    related_task_id=memory[0]["task_id"],
                    confidence="HIGH",
                )
            ]
        )


def test_ai_task_memory_resolves_long_distance_deadline_mutation() -> None:
    filler = "\n".join(
        f"Người {index}: Trao đổi thông tin vận hành thông thường."
        for index in range(10)
    )
    transcript = (
        "Lan: Em sẽ viết API spec, deadline 20/02/2026.\n"
        f"{filler}\n"
        "Minh: Deadline cũ không còn hiệu lực. Deadline mới cho phần của Lan là 25/02/2026."
    )

    result = process_meeting(
        MeetingInput("M-LONG-MEMORY", "API", "2026-02-10", transcript),
        TaskMemoryResolvingAiClient(),
    )

    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Viết API spec"
    assert result.tasks[0].due_date == "2026-02-25"
    assert result.diagnostics.ledger_unknown_task_id_rejection_count == 0


def test_rule_assignment_uses_vocative_name_and_cleans_question() -> None:
    transcript = """Anh Minh: Tuấn, anh giao em viết báo cáo kiểm thử, em làm thế nào?
Anh Tuấn: Em mới bắt đầu thu thập số liệu.
"""
    result = process_meeting(MeetingInput("M-4", "Review", "2026-01-17", transcript))
    assert [(task.task_name, task.assignee) for task in result.tasks] == [
        ("Viết báo cáo kiểm thử", "Tuấn")
    ]


def test_rule_extractor_rejects_vague_confirmations_and_temporal_only_action() -> None:
    transcript = """Lan: Dạ em sẽ làm đúng như vậy.
Minh: Anh sẽ kiểm tra trong hôm nay.
Tuấn: Em sẽ cố gắng.
"""
    result = process_meeting(MeetingInput("M-5", "Review", "2026-01-17", transcript))
    assert result.tasks == []


def test_rule_extractor_keeps_concrete_completion() -> None:
    transcript = "Anh Minh: Em xác nhận sẽ hoàn thành bản thảo trước 15/02/2026."
    result = process_meeting(MeetingInput("M-6", "Review", "2026-02-02", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Hoàn thành bản thảo"
    assert result.tasks[0].assignee == "Minh"


def test_rule_extractor_normalizes_task_names_and_rejects_pronoun_only_actions() -> None:
    transcript = """Huy: Có điều mấy cái biểu đồ em sẽ gửi bổ sung sáng mai nhé.
Minh: Anh sẽ lo phần đó.
Minh: Anh sẽ cập nhật mẫu chung rồi gửi lại, em cứ tạm dùng bản hiện tại.
Minh: Ừm, vậy mình sẽ cam kết hoàn thành draft báo cáo trước ngày 15/03.
"""
    result = process_meeting(MeetingInput("M-NAME", "Review", "2026-07-29", transcript))
    identities = {(task.task_name, task.assignee) for task in result.tasks}
    assert ("Gửi bổ sung biểu đồ", "Huy") in identities
    assert ("Cập nhật mẫu chung rồi gửi lại", "Minh") in identities
    assert ("Hoàn thành draft báo cáo", "Minh") in identities
    assert all(task.task_name != "Lo phần đó" for task in result.tasks)


def test_deadline_questions_and_group_reminders_do_not_leak_to_tasks() -> None:
    transcript = """Huy: Em sẽ gửi bổ sung biểu đồ sáng mai.
Huy: Ok, thế 15/03 mình sẽ có bản draft cuối?
Minh: Em sẽ cập nhật mẫu chung rồi gửi lại.
Minh: Mỗi người cố gắng hoàn thành phần mình trước 15/03, mình sẽ tổng hợp.
"""
    result = process_meeting(MeetingInput("M-DATE", "Review", "2026-07-29", transcript))
    tasks = {task.task_name: task for task in result.tasks}
    assert tasks["Gửi bổ sung biểu đồ"].due_date == "2026-07-30"
    assert tasks["Gửi bổ sung biểu đồ"].due_date_text == "sáng mai"
    assert tasks["Cập nhật mẫu chung rồi gửi lại"].due_date == ""


def test_final_recap_keeps_time_qualifiers_out_of_task_name() -> None:
    transcript = """Linh: OK, vậy chốt lại: Hoa làm mockup, cố gắng thứ Hai hoặc thứ Ba;
Minh làm frontend với mock data, chưa có deadline cứng, báo tiến độ hàng ngày.
"""
    result = process_meeting(MeetingInput("M-RECAP", "Demo", "2026-07-29", transcript))
    tasks = {task.task_name: task for task in result.tasks}
    assert tasks["Làm mockup"].assignee == "Hoa"
    assert tasks["Làm mockup"].due_date == ""
    assert tasks["Làm mockup"].due_date_text == "thứ Hai hoặc thứ Ba"
    assert tasks["Làm frontend với mock data"].assignee == "Minh"


def test_later_deadline_directive_updates_existing_task_for_same_owner() -> None:
    transcript = """Minh: Tuấn, anh giao em viết báo cáo kiểm thử, em làm thế nào?
Tuấn: Em mới bắt đầu thu thập số liệu.
Minh: Em cần hoàn thành báo cáo vào ngày 20/01/2026.
"""
    result = process_meeting(MeetingInput("M-7", "Review", "2026-01-17", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Viết báo cáo kiểm thử"
    assert result.tasks[0].assignee == "Tuấn"
    assert result.tasks[0].due_date == "2026-01-20"


def test_task_linking_does_not_merge_different_assignees() -> None:
    transcript = """Lan: Em sẽ hoàn thành tài liệu.
Minh: Tuấn, anh giao em viết báo cáo kiểm thử.
Tuấn: Em nhận.
Minh: Em cần hoàn thành báo cáo vào ngày 20/01/2026.
"""
    result = process_meeting(MeetingInput("M-9", "Review", "2026-01-17", transcript))
    tasks = {task.assignee: task for task in result.tasks}
    assert tasks["Lan"].task_name == "Hoàn thành tài liệu"
    assert tasks["Tuấn"].task_name == "Viết báo cáo kiểm thử"
    assert tasks["Tuấn"].due_date == "2026-01-20"


def test_vietnamese_written_month_date_is_resolved() -> None:
    transcript = "Lan: Em sẽ gửi tài liệu trước ngày 25 tháng 1 năm 2026."
    result = process_meeting(MeetingInput("M-8", "Review", "2026-01-17", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].due_date == "2026-01-24"
    assert result.tasks[0].due_date_text == "trước ngày 25 tháng 1 năm 2026"


def test_final_recap_extracts_plain_name_action_pairs() -> None:
    transcript = """Linh: Hoa đang làm mockup và Minh đang đợi thiết kế.
Hoa: Em sẽ tiếp tục.
Minh: Em đang đợi mockup.
Linh: OK, vậy chốt lại: Hoa làm mockup, chưa có deadline; Minh làm frontend với mock data, báo tiến độ hàng ngày.
"""
    result = process_meeting(MeetingInput("M-10", "Demo", "2026-01-17", transcript))
    identities = {(task.task_name, task.assignee) for task in result.tasks}
    assert ("Làm mockup", "Hoa") in identities
    assert ("Làm frontend với mock data", "Minh") in identities


def test_ambiguous_weekday_range_preserves_text_without_inventing_date() -> None:
    transcript = """Hoa: Em đang làm mockup.
Linh: Chốt lại: Hoa làm mockup, cố gắng thứ Hai hoặc thứ Ba.
"""
    result = process_meeting(MeetingInput("M-11", "Demo", "2026-01-31", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].due_date == ""
    assert result.tasks[0].due_date_text == "thứ Hai hoặc thứ Ba"


def test_recap_supports_action_before_owner() -> None:
    transcript = """An: Em đang xử lý cổng thanh toán.
Minh: Quay lại recap chính: task TASK-567 - tích hợp payment gateway, An đang làm, deadline 20/02/2026.
"""
    result = process_meeting(MeetingInput("M-12", "Payment", "2026-02-10", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Tích hợp payment gateway"
    assert result.tasks[0].assignee == "An"
    assert result.tasks[0].due_date == "2026-02-20"


def test_compound_preparation_uses_final_deliverable_as_task_name() -> None:
    transcript = "Minh: Em sẽ tập hợp các thay đổi và viết release note trước 13/02/2026."
    result = process_meeting(MeetingInput("M-13", "Release", "2026-02-10", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Viết release note"


def test_named_participant_directive_without_vocative_comma() -> None:
    transcript = """Hoàng: Em đang kiểm tra các module.
Minh: Hoàng cần xác nhận việc kiểm tra module liên quan.
"""
    result = process_meeting(MeetingInput("M-14", "Incident", "2026-02-10", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Kiểm tra module liên quan"
    assert result.tasks[0].assignee == "Hoàng"


def test_object_fronted_commitment_reconstructs_action_object_order() -> None:
    transcript = "Mai: Còn tài liệu em sẽ soạn trước tối thứ Năm."
    result = process_meeting(MeetingInput("M-15", "Build", "2026-02-10", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Soạn tài liệu"


def test_deadline_replacement_updates_existing_task() -> None:
    transcript = """Minh: Em sẽ viết release note.
Lan: Deadline cũ không còn hiệu lực. Deadline mới là ngày 13 tháng 2 năm 2026.
"""
    result = process_meeting(MeetingInput("M-16", "Release", "2026-02-10", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].due_date == "2026-02-13"
    assert result.tasks[0].due_date_text == "ngày 13 tháng 2 năm 2026"


def test_date_only_confirmation_updates_single_owned_task() -> None:
    transcript = """Minh: Hoàng cần xác nhận việc kiểm tra module liên quan.
Hoàng: Tôi sẽ cố gắng xong trong sáng nay.
"""
    result = process_meeting(MeetingInput("M-17", "Incident", "2026-02-09", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].due_date == "2026-02-09"
    assert result.tasks[0].due_date_text == "trong sáng nay"


def test_weekday_and_written_date_are_preserved_as_one_mention() -> None:
    transcript = """Mai: Em sẽ chuẩn bị build nội bộ.
Tuan: Chốt lại: Mai chuẩn bị build nội bộ, deadline thứ Sáu 13 tháng 2.
"""
    result = process_meeting(MeetingInput("M-18", "Build", "2026-02-10", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].due_date_text == "thứ Sáu 13 tháng 2"


def test_pending_owner_deadline_can_attach_to_later_formal_task() -> None:
    transcript = """Hoàng: Tôi sẽ cố gắng xong trong sáng nay.
Minh: Hoàng cần xác nhận việc kiểm tra module liên quan.
"""
    result = process_meeting(MeetingInput("M-19", "Incident", "2026-02-09", transcript))
    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Kiểm tra module liên quan"
    assert result.tasks[0].due_date == "2026-02-09"


def test_recap_companion_document_becomes_separate_deliverable() -> None:
    transcript = """Mai: Còn tài liệu em sẽ soạn trước tối thứ Năm.
Tuan: Chốt lại: Mai chuẩn bị build nội bộ, thứ Sáu 13/02/2026, kèm tài liệu hướng dẫn.
"""
    result = process_meeting(MeetingInput("M-20", "Build", "2026-02-10", transcript))
    identities = {(task.task_name, task.assignee) for task in result.tasks}
    assert result.diagnostics.recap_scope == "PARTIAL"
    assert ("Chuẩn bị build nội bộ", "Mai") in identities
    assert ("Soạn tài liệu hướng dẫn", "Mai") in identities


def test_unassigned_followup_message_is_not_a_separate_task() -> None:
    transcript = """Hoàng: Tôi sẽ gửi email kết quả kiểm tra ngay sau khi xong.
Minh: Hoàng cần xác nhận việc kiểm tra module liên quan.
"""
    result = process_meeting(MeetingInput("M-21", "Incident", "2026-02-09", transcript))
    assert [(task.task_name, task.assignee) for task in result.tasks] == [
        ("Kiểm tra module liên quan", "Hoàng")
    ]


def test_pending_candidate_is_promoted_only_after_explicit_confirmation() -> None:
    transcript = """Minh: Lan, phần tài liệu hướng dẫn sử dụng em làm đến đâu rồi?
Lan: Em đã làm được phần lớn.
Minh: Em cố gắng hoàn thành trước ngày 25 tháng 1 nhé.
Lan: Dạ, em sẽ hoàn thành đúng hạn.
"""
    result = process_meeting(
        MeetingInput("M-22", "Tài liệu", "2026-01-17", transcript)
    )
    assert [(task.task_name, task.assignee) for task in result.tasks] == [
        ("Hoàn thành tài liệu hướng dẫn sử dụng", "Lan")
    ]
    assert result.tasks[0].due_date == "2026-01-24"


def test_recap_date_does_not_leak_to_companion_task() -> None:
    transcript = """Mai: Còn tài liệu em sẽ soạn trước tối thứ Năm.
Linh: Em sẵn sàng test khi có build.
Tuan: Chốt lại: Mai chuẩn bị build nội bộ, deadline thứ Sáu 13 tháng 2, kèm tài liệu hướng dẫn. Linh test hồi quy, deadline 16 tháng 2.
"""
    result = process_meeting(
        MeetingInput("M-23", "Build", "2026-02-10", transcript)
    )
    tasks = {task.task_name: task for task in result.tasks}
    assert tasks["Soạn tài liệu hướng dẫn"].due_date == "2026-02-12"
    assert tasks["Test hồi quy"].due_date == "2026-02-16"


def test_explicit_task_start_date_overrides_meeting_date() -> None:
    transcript = (
        "Lan: Em sẽ triển khai API từ ngày 20/01/2026 và hoàn thành "
        "trước ngày 25/01/2026."
    )

    result = process_meeting(
        MeetingInput("M-EXPLICIT-START", "Triển khai", "2026-01-17", transcript)
    )

    assert len(result.tasks) == 1
    assert result.tasks[0].start_date == "2026-01-20"
    assert result.tasks[0].due_date == "2026-01-24"
    assert result.tasks[0].due_date_text == "trước ngày 25/01/2026"
    assert result.diagnostics.explicit_task_start_date_count == 1


def test_final_recap_task_labels_create_distinct_tasks() -> None:
    transcript = """Linh: Task A - “Chuẩn bị môi trường test”, do Minh, deadline 02/03.
Linh: Task B - “Chuẩn bị dữ liệu test”, do Huy, deadline 04/03.
Linh: Em muốn recap lại tổng thể.
Linh: Tổng cộng các task hiện tại: A (Minh, deadline 02/03), B (Huy, deadline 04/03).
"""
    result = process_meeting(
        MeetingInput("M-24", "Triển khai", "2026-02-26", transcript)
    )
    assert {(task.task_name, task.assignee) for task in result.tasks} == {
        ("Chuẩn bị môi trường test", "Minh"),
        ("Chuẩn bị dữ liệu test", "Huy"),
    }


def test_explicit_no_active_task_closure_suppresses_later_minutes_followup() -> None:
    transcript = """Alice: Task tài liệu API đã hủy. Còn lỗi mobile chỉ ghi backlog, không tạo task.
Bob: Như vậy không có task mới nào được tạo hôm nay, chỉ có hủy task cũ.
Alice: Chính xác, meeting hôm nay không có output nào, chỉ có hủy task.
Carol: Tôi sẽ update log và gửi biên bản ngắn.
"""
    result = process_meeting(
        MeetingInput("M-CLOSED", "Theo dõi lỗi", "2026-07-29", transcript)
    )
    assert result.tasks == []


def test_generic_owner_section_recap_does_not_prune_prior_active_tasks() -> None:
    result = _run_validation_case("W3-MED-C3-N1-OPS-INT-020")
    tasks = {(task.task_name, task.assignee): task for task in result.tasks}

    assert len(tasks) >= 8
    assert tasks[("Update version các service (trừ logging)", "Minh")].due_date == "2026-02-27"
    assert tasks[("Fix bug #4521", "Minh")].due_date == "2026-02-26"
    assert tasks[("Setup metric giám sát", "Hùng")].due_date == "2026-03-03"
    assert tasks[("Giám sát release từ thứ Năm", "Minh")].start_date == (
        "2026-02-26"
    )
    assert tasks[("Giám sát release đến thứ Tư", "Hùng")].due_date == (
        "2026-02-25"
    )
    assert tasks[("Gửi log, chuẩn bị test case", "Lan")].due_date_text == ""
    assert ("Review báo cáo giám sát", "PL") in tasks
    assert any("xung đột" in task.task_name.casefold() for task in result.tasks)


def test_explicit_final_snapshot_can_prune_superseded_tasks() -> None:
    transcript = """Lan: Em sẽ viết API spec.
Minh: Em sẽ chuẩn bị UAT.
Hoa: Final recap:
Hoa: Tasks active: Viết API spec.
Hoa: Hủy: Chuẩn bị UAT.
"""
    result = process_meeting(
        MeetingInput("M-EXPLICIT-SNAPSHOT", "Release", "2026-08-10", transcript)
    )
    assert [task.task_name for task in result.tasks] == ["Viết API spec"]


def test_long_numbered_explicit_full_recap_is_authoritative() -> None:
    result = _run_validation_case("W4-LONG-C4-N1-IT-STATE-006")
    tasks = {task.task_name: task for task in result.tasks}

    assert result.diagnostics.recap_scope == "AUTHORITATIVE"
    assert len(tasks) == 6
    assert tasks["Thiết lập môi trường staging"].assignee == "Bình"
    assert tasks["Thiết lập môi trường staging"].due_date == "2026-03-05"
    assert tasks["Soạn tài liệu hướng dẫn triển khai"].assignee == "Cường"
    assert tasks["Soạn tài liệu hướng dẫn triển khai"].due_date == "2026-03-10"
    assert tasks["Xử lý lỗi đăng nhập LDAP"].due_date == "2026-02-27"
    assert tasks["Soạn hướng dẫn cấu hình LDAP"].due_date == "2026-02-24"
    assert tasks["Ẩn danh dữ liệu test"].due_date == "2026-02-28"
    assert tasks["Cấu hình Monitoring"].due_date == ""
    assert tasks["Cấu hình Monitoring"].due_date_text == (
        "trước release dự kiến cuối tháng 3"
    )
    assert "Gửi template cho em cuối giờ" not in tasks


def test_long_shorthand_explicit_full_recap_is_authoritative() -> None:
    result = _run_validation_case("W4-LONG-C4-N2-OPS-STATE-004")
    tasks = {(task.task_name.casefold(), task.assignee): task for task in result.tasks}

    assert result.diagnostics.recap_scope == "AUTHORITATIVE"
    assert len(tasks) == 5
    assert ("update compose file", "Minh") not in tasks
    assert tasks[("viết tài liệu", "Tuấn")].due_date == "2026-03-05"
    assert tasks[("fix lỗi login", "Minh")].due_date == "2026-02-25"
    assert tasks[("chuẩn bị data test", "Hoa")].due_date == "2026-02-20"
    assert tasks[("cấu hình monitoring", "Tuấn")].due_date == "2026-02-28"
    assert tasks[("lập release plan", "Minh")].due_date == "2026-02-28"


def test_inline_bullet_final_snapshot_prunes_incidental_commitments() -> None:
    transcript = """Tuấn: Lan sẽ chuẩn bị tài liệu nháp.
Minh: Em sẽ thêm mục rollback.
Hùng: Em sẽ tạo channel riêng.
Tuấn: Tổng kết cuối cùng: tiến độ chung ổn.
Minh: Em nghĩ nên recap nhanh.
Tuấn: Tôi điểm qua: - Tài liệu triển khai hệ thống: Hùng, deadline 25/2. - Tài liệu triển khai test: Lan, deadline 27/2. - Kiểm tra config sandbox: Hùng & Minh, deadline 27/2. - Monitoring alert: Hùng, deadline 28/2. - Các task khác hủy hoặc hoàn thành.
Lan: Đầy đủ.
"""

    result = process_meeting(
        MeetingInput("M-INLINE-FINAL", "Release", "2026-02-17", transcript)
    )
    tasks = {task.task_name: task for task in result.tasks}

    assert result.diagnostics.recap_scope == "AUTHORITATIVE"
    assert set(tasks) == {
        "Viết tài liệu triển khai hệ thống",
        "Viết tài liệu triển khai test",
        "Kiểm tra config sandbox",
        "Cấu hình Monitoring alert",
    }
    assert tasks["Viết tài liệu triển khai hệ thống"].assignee == "Hùng"
    assert tasks["Viết tài liệu triển khai test"].assignee == "Lan"
    assert tasks["Kiểm tra config sandbox"].assignee == "Hùng; Minh"
    assert tasks["Kiểm tra config sandbox"].due_date == "2026-02-27"
    assert tasks["Cấu hình Monitoring alert"].due_date == "2026-02-28"


def test_inline_bullet_partial_recap_does_not_prune_unlisted_task() -> None:
    transcript = """Lan: Em sẽ viết API spec trước 20/2.
Minh: Em sẽ chuẩn bị UAT trước 21/2.
Hoa: Tôi điểm qua: - API spec: Lan, deadline 20/2. - Các phần khác vẫn giữ như cũ.
"""

    result = process_meeting(
        MeetingInput("M-INLINE-PARTIAL", "Release", "2026-02-17", transcript)
    )

    assert {task.assignee for task in result.tasks} == {"Lan", "Minh"}


def test_post_recap_relative_deadline_updates_unique_owned_task() -> None:
    transcript = """Minh: Tổng kết toàn bộ công việc: - Phân tích lỗi timestamp: Hoa & Tuấn, chưa có deadline. - Viết API spec: Lan, deadline 20/2.
Hoa: Em sẽ test xong trong ngày mai và báo cáo kết quả, sau đó mới assign.
"""

    result = process_meeting(
        MeetingInput("M-POST-RECAP-DATE", "Release", "2026-02-17", transcript)
    )
    tasks = {task.task_name: task for task in result.tasks}

    assert set(tasks) == {"Phân tích lỗi timestamp", "Viết API spec"}
    assert tasks["Phân tích lỗi timestamp"].assignee == "Hoa; Tuấn"
    assert tasks["Phân tích lỗi timestamp"].due_date == "2026-02-18"
    assert tasks["Phân tích lỗi timestamp"].due_date_text == "trong ngày mai"


def test_post_recap_vague_deadline_does_not_guess_between_same_owner_tasks() -> None:
    transcript = """Minh: Tổng kết toàn bộ công việc: - Viết API spec: Hoa, chưa có deadline. - Chuẩn bị UAT: Hoa, chưa có deadline.
Hoa: Em sẽ test xong trong ngày mai.
"""

    result = process_meeting(
        MeetingInput("M-POST-RECAP-AMBIGUOUS", "Release", "2026-02-17", transcript)
    )

    assert {task.task_name for task in result.tasks} == {
        "Viết API spec",
        "Chuẩn bị UAT",
    }
    assert all(task.due_date == "" for task in result.tasks)


def test_active_existing_task_can_be_restored_by_post_recap_reference() -> None:
    transcript = """Hoa: Parser transcript hiện tại vẫn chưa xử lý được file lạ. Task này em nhận từ tuần trước, deadline 15/02.
Minh: Tổng kết toàn bộ công việc: - Viết API spec: Lan, deadline 20/2. - Chuẩn bị UAT: Tuấn, deadline 21/2.
Hoa: Em nhắc lại task parser vẫn deadline 18/02.
"""

    result = process_meeting(
        MeetingInput("M-RESTORE-EXISTING", "Release", "2026-02-11", transcript)
    )
    tasks = {task.task_name: task for task in result.tasks}

    assert tasks["Xử lý Parser transcript"].assignee == "Hoa"
    assert tasks["Xử lý Parser transcript"].due_date == "2026-02-18"


def test_completed_existing_task_reference_is_not_restored() -> None:
    transcript = """Hoa: Parser transcript đã xử lý xong từ tuần trước. Task này đã hoàn thành rồi.
Minh: Tổng kết toàn bộ công việc: - Viết API spec: Lan, deadline 20/2. - Chuẩn bị UAT: Tuấn, deadline 21/2.
Hoa: Task parser chỉ được nhắc để tránh nhầm, không tạo lại.
"""

    result = process_meeting(
        MeetingInput("M-NO-RESTORE-COMPLETED", "Release", "2026-02-11", transcript)
    )

    assert {task.task_name for task in result.tasks} == {
        "Viết API spec",
        "Chuẩn bị UAT",
    }


def test_real_cancel_only_case_has_no_active_tasks() -> None:
    result = _run_validation_case("W2-SHORT-C3-N0-PROD-CANC-027")
    assert result.tasks == []
    assert result.diagnostics.provisional_task_created_count >= 1
