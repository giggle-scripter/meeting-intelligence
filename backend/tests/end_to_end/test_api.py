from base64 import b64encode
from datetime import date
from pathlib import Path
import time

from fastapi.testclient import TestClient

from backend.app.ingestion import build_meeting_package
from backend.app.main import app


client = TestClient(app)


def test_preprocess_and_process_endpoints() -> None:
    payload = {"meeting_id": "M-API", "meeting_title": "Release", "meeting_date": "2026-07-20", "file_name": "meeting.vtt", "transcript": """WEBVTT

00:00:01.000 --> 00:00:03.000
Phương: Em sẽ gửi release note trước 18h ngày 24/07/2026.
"""}
    preprocess = client.post("/api/v1/transcripts/preprocess", json=payload)
    assert preprocess.status_code == 200
    assert preprocess.json()["statistics"]["clauses"] == 1
    processed = client.post("/api/v1/meetings/process", json=payload)
    assert processed.status_code == 200
    assert processed.json()["tasks"][0]["due_date"] == "2026-07-24"


def test_json_job_endpoint_accepts_an_optional_meeting_note() -> None:
    payload = {
        "meeting_id": "M-API-NOTE",
        "meeting_title": "Release",
        "meeting_date": "2026-07-20",
        "transcript": "Phương: Em sẽ gửi release note trước 18h ngày 24/07/2026.",
        "meeting_note": {
            "content": "- Phương gửi release note trước 24/07",
            "author": "Thư ký",
            "source": "SECRETARY",
        },
    }

    response = client.post("/api/v1/meetings/jobs/process", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {"queued", "running", "succeeded"}
    assert body["created"] is True
    assert body["status_url"].startswith("/api/v1/meetings/jobs/job-")


def test_job_idempotency_includes_meeting_metadata() -> None:
    base = {
        "meeting_id": "M-IDEMPOTENCY-METADATA",
        "meeting_title": "Release planning",
        "meeting_date": "2026-08-17",
        "file_name": "same.txt",
        "transcript": "Lan: Em sẽ gửi release note.",
    }

    first = client.post("/api/v1/meetings/jobs/process", json=base)
    duplicate = client.post("/api/v1/meetings/jobs/process", json=base)
    changed_date = client.post(
        "/api/v1/meetings/jobs/process",
        json={**base, "meeting_date": "2026-08-18"},
    )

    assert first.status_code == duplicate.status_code == changed_date.status_code == 202
    assert first.json()["created"] is True
    assert duplicate.json()["created"] is False
    assert duplicate.json()["job_id"] == first.json()["job_id"]
    assert changed_date.json()["created"] is True
    assert changed_date.json()["job_id"] != first.json()["job_id"]


def test_rejects_unsupported_file_type() -> None:
    response = client.post("/api/v1/meetings/process", json={"meeting_id": "M", "meeting_title": "M", "meeting_date": "2026-07-20", "file_name": "meeting.docx", "transcript": "content"})
    assert response.status_code == 422


def test_process_file_endpoint_accepts_raw_utf8_transcript() -> None:
    transcript = """TITLE: Release readiness

Phương: Em sẽ gửi release note trước 18h ngày 24/07/2026."""
    response = client.post(
        "/api/v1/meetings/process-file",
        content=transcript.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": "meeting-upload.txt",
            "X-Meeting-Id": "meeting_01",
            "X-Meeting-Title": "Release readiness",
            "X-Meeting-Date": "2026-07-20",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["meeting_title"] == "Release readiness"
    assert result["summary"].startswith(
        "Cuộc họp tập trung vào release readiness. 1 công việc được chốt:"
    )
    assert result["tasks"][0]["assignee"] == "Phương"
    assert result["tasks"][0]["due_date"] == "2026-07-24"


def test_file_endpoint_infers_meeting_date_from_transcript_context() -> None:
    transcript = """Thư ký: Hôm nay là ngày 17/01/2026.
Lan: Em sẽ hoàn thành tài liệu trước ngày 25/01/2026.
"""

    response = client.post(
        "/api/v1/meetings/process-file",
        content=transcript.encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["tasks"][0]["start_date"] == "2026-01-17"
    assert result["tasks"][0]["due_date"] == "2026-01-24"
    assert result["diagnostics"]["meeting_date_source"] == "TRANSCRIPT_CONTEXT"
    assert result["diagnostics"]["effective_meeting_date"] == "2026-01-17"


def test_file_endpoint_infers_meeting_date_from_note_context() -> None:
    packaged = build_meeting_package(
        "Lan: Em sẽ hoàn thành tài liệu trước ngày 25/01/2026.",
        "Ngày họp: 18/01/2026\n- Lan hoàn thành tài liệu trước 25/01/2026.",
    )

    response = client.post(
        "/api/v1/meetings/process-file",
        content=packaged.encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["tasks"][0]["start_date"] == "2026-01-18"
    assert result["diagnostics"]["meeting_date_source"] == "MEETING_NOTE_CONTEXT"


def test_file_endpoint_reads_self_contained_package_metadata() -> None:
    packaged = build_meeting_package(
        "Lan: Em sẽ hoàn thành tài liệu trước ngày 25/01/2026.",
        None,
        meeting_id="M-PACKAGE",
        meeting_title="Theo dõi tài liệu",
        meeting_date="2026-01-17",
    )

    response = client.post(
        "/api/v1/meetings/process-file",
        content=packaged.encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["meeting_title"] == "Theo dõi tài liệu"
    assert result["tasks"][0]["start_date"] == "2026-01-17"
    assert result["tasks"][0]["due_date"] == "2026-01-24"
    assert result["diagnostics"]["meeting_date_source"] == "PACKAGE_METADATA"


def test_explicit_request_date_overrides_transcript_context() -> None:
    transcript = """Thư ký: Hôm nay là ngày 17/01/2026.
Lan: Em sẽ hoàn thành tài liệu trước ngày 25/01/2026.
"""

    response = client.post(
        "/api/v1/meetings/process-file",
        content=transcript.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-Meeting-Date": "2026-01-20",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["tasks"][0]["start_date"] == "2026-01-20"
    assert result["diagnostics"]["meeting_date_source"] == "REQUEST"


def test_file_endpoint_uses_processing_date_only_as_last_fallback() -> None:
    response = client.post(
        "/api/v1/meetings/process-file",
        content="Lan: Em sẽ hoàn thành tài liệu.".encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["tasks"][0]["start_date"] == date.today().isoformat()
    assert result["tasks"][0]["start_date"]
    assert result["diagnostics"]["meeting_date_source"] == "PROCESSING_DATE"


def test_file_job_accepts_unicode_metadata_through_base64_headers() -> None:
    transcript = "Lan: Em sẽ gửi tài liệu vào ngày mai."
    title = "Họp rà soát phát hành"
    file_name = "biên-bản-cuộc-họp.txt"

    response = client.post(
        "/api/v1/meetings/jobs/process-file",
        content=transcript.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name-Base64": b64encode(file_name.encode()).decode(),
            "X-Meeting-Id": "M-UNICODE-HEADER",
            "X-Meeting-Title-Base64": b64encode(title.encode()).decode(),
            "X-Meeting-Date": "2026-08-17",
        },
    )

    assert response.status_code == 202
    assert response.json()["created"] is True


def test_power_automate_upload_package_completes_through_job_polling() -> None:
    package = Path(
        "data/power_automate_uploads/W1-SHORT-C1-N0-IT-DASG-ABS-002__with-note.txt"
    )
    # The job store creates an asyncio task, so its client must keep one
    # application lifespan/event loop alive for both submit and polling.
    with TestClient(app) as job_client:
        response = job_client.post(
            "/api/v1/meetings/jobs/process-file",
            content=package.read_bytes(),
            headers={
                "Content-Type": "application/octet-stream",
                "X-File-Name-Base64": b64encode(package.name.encode()).decode(),
            },
        )

        assert response.status_code == 202
        status_url = response.json()["status_url"]
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            status = job_client.get(status_url)
            if status.json()["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.02)
        else:  # pragma: no cover - assertion explains a failed async contract
            raise AssertionError("Power Automate job did not reach a terminal state")

    body = status.json()
    assert body["status"] == "succeeded"
    assert body["result"]["meeting_title"] == "Theo dõi tiến độ tài liệu"
    assert body["result"]["diagnostics"]["meeting_date_source"] == "PACKAGE_METADATA"


def test_file_job_rejects_invalid_base64_metadata() -> None:
    response = client.post(
        "/api/v1/meetings/jobs/process-file",
        content=b"Lan: Em se gui tai lieu.",
        headers={"X-Meeting-Title-Base64": "not valid base64!"},
    )

    assert response.status_code == 422
    assert "Base64-encoded UTF-8" in response.json()["detail"]


def test_process_file_endpoint_splits_packaged_note_before_task_extraction() -> None:
    packaged = build_meeting_package(
        "Lan: Em sẽ cập nhật dashboard doanh thu.",
        "note nhanh\n- task giả không có trong transcript: xóa production",
    )

    response = client.post(
        "/api/v1/meetings/process-file",
        content=packaged.encode("utf-8"),
        headers={"Content-Type": "application/octet-stream", "X-File-Name": "meeting.txt"},
    )

    assert response.status_code == 200
    result = response.json()
    assert [task["task_name"] for task in result["tasks"]] == ["Cập nhật dashboard doanh thu"]
    assert "Không có Meeting Note" not in result["summary"]


def test_process_file_endpoint_uses_only_explicit_note_topic_for_summary() -> None:
    packaged = build_meeting_package(
        "Lan: Chưa có quyết định mới.",
        "Note vội - có thể thiếu\nChủ đề: Đồng bộ CRM và ERP\nKW: CRM, ERP",
    )

    response = client.post(
        "/api/v1/meetings/process-file",
        content=packaged.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": "W5-XL-C5-N2-IT-STRESS-002__with-note.txt",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["tasks"] == []
    assert result["summary"] == (
        "Cuộc họp tập trung vào đồng bộ CRM và ERP. Chưa chốt công việc mới."
    )


def test_process_file_endpoint_infers_topic_when_upload_has_no_title_metadata() -> None:
    transcript = (
        "[00:00:00] An: Hôm nay mình họp để cùng nhau nghĩ ra các hướng "
        "tích hợp hệ thống CRM với ERP nhé.\n"
        "[00:00:10] Bình: Chưa có quyết định nào."
    )
    response = client.post(
        "/api/v1/meetings/process-file",
        content=transcript.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": "W2-SHORT-C2-N0-IT-BRST-006.txt",
            "X-Meeting-Title": "W2-SHORT-C2-N0-IT-BRST-006",
            "X-Meeting-Date": "2026-07-29",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["meeting_title"] == "W2-SHORT-C2-N0-IT-BRST-006"
    assert result["summary"] == (
        "Cuộc họp tập trung vào tích hợp hệ thống CRM với ERP. "
        "Chưa chốt công việc mới. Không có Meeting Note; kết quả được trích xuất từ transcript."
    )


def test_process_file_endpoint_summarizes_cancellation_only_meeting() -> None:
    transcript = (
        "[00:00:05] Alice: Chào mọi người, hôm nay họp về tiến độ theo dõi "
        "lỗi và tài liệu.\n"
        "[00:00:20] Bob: Task viết unit test đã bị hủy rồi.\n"
        "[00:00:40] Alice: Meeting hôm nay không có output nào, chỉ có hủy task."
    )
    response = client.post(
        "/api/v1/meetings/process-file",
        content=transcript.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": "W2-SHORT-C3-N0-PROD-CANC-027.txt",
            "X-Meeting-Title": "W2-SHORT-C3-N0-PROD-CANC-027",
            "X-Meeting-Date": "2026-07-30",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["tasks"] == []
    assert result["summary"] == (
        "Cuộc họp tập trung vào tiến độ theo dõi lỗi và tài liệu. "
        "Các task cũ được đề cập đã bị hủy; không có task active mới. "
        "Không có Meeting Note; kết quả được trích xuất từ transcript."
    )


def test_process_file_endpoint_infers_topic_from_opening_agenda() -> None:
    transcript = (
        "[09:00:00] PL: Hôm nay chúng ta có cuộc họp phối hợp triển khai "
        "release. Mình điểm qua các đầu việc chính: chuẩn bị release, xử lý "
        "bug, giám sát và tài liệu.\n"
        "[09:00:15] PL: Meeting hôm nay không có output nào."
    )
    response = client.post(
        "/api/v1/meetings/process-file",
        content=transcript.encode("utf-8"),
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": "W3-MED-C3-N1-OPS-INT-020.txt",
            "X-Meeting-Title": "W3-MED-C3-N1-OPS-INT-020",
            "X-Meeting-Date": "2026-07-30",
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"] == (
        "Cuộc họp tập trung vào chuẩn bị release, xử lý bug, giám sát và "
        "tài liệu. Chưa chốt công việc mới. Không có Meeting Note; "
        "kết quả được trích xuất từ transcript."
    )
