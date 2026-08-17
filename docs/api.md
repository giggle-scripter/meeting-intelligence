# API Contract

## Authentication

Khi `POWER_AUTOMATE_API_KEY` được cấu hình, mọi endpoint xử lý yêu cầu
`X-API-Key`. JSON endpoint dùng:

```text
X-API-Key: <secret>
Content-Type: application/json
```

File endpoint dùng `Content-Type: application/octet-stream` như mô tả bên dưới.

## Preprocess transcript

`POST /api/v1/transcripts/preprocess`

Trả captions sau dedup, turns, sentences, clauses và số liệu stage. Endpoint này
dùng để debug và trình bày khả năng xử lý transcript.

## Process meeting

`POST /api/v1/meetings/process`

Nhận JSON đầy đủ từ client có khả năng xây dựng request body.

`meeting_note` là optional và có schema:

```json
{
  "content": "- Lan cập nhật dashboard trước thứ Sáu.",
  "author": "Thư ký",
  "source": "SECRETARY"
}
```

`source` nhận một trong `SECRETARY`, `PARTICIPANT`, `MANUAL` hoặc
`AUTO_OVERVIEW`. Chỉ note khẳng định cụ thể từ nguồn human (`SECRETARY`,
`PARTICIPANT`, `MANUAL`) mới có thể tạo task bổ sung; `AUTO_OVERVIEW` chỉ là
context.

## Process uploaded transcript file

`POST /api/v1/meetings/process-file`

Nhận trực tiếp nội dung binary của file `.txt`, `.vtt` hoặc `.srt`. Endpoint này
dùng cho OneDrive/SharePoint connector và không yêu cầu Compose expression.

```text
Content-Type: application/octet-stream
X-API-Key: <secret>
X-File-Name: meeting_01.vtt
X-Meeting-Id: meeting_01       (optional)
X-Meeting-Title: Weekly Sync   (optional)
X-Meeting-Date: 2026-07-27     (optional)
```

Với title/file name Unicode, dùng header ASCII-safe:

```text
X-File-Name-Base64: <Base64 của UTF-8 file name>
X-Meeting-Title-Base64: <Base64 của UTF-8 meeting title>
```

Hai header Base64 được ưu tiên hơn header plain tương ứng. Với self-contained
package, Power Automate chỉ cần `X-File-Name-Base64`; các meeting header là
optional override.

Authority cho file là request header > embedded package metadata > inferred
transcript/note context > processing date. Nếu package và header đều thiếu,
API tự tạo meeting ID ổn định từ tên/nội dung file và lấy title từ `TITLE:`, câu
agenda mở đầu hoặc tên file. Với ngày họp, API tìm statement đầy đủ như `Hôm nay
là ngày 17/01/2026`, `Ngày họp:` hoặc `Meeting date:` trong transcript, sau đó
Meeting Note; chỉ khi không có context date duy nhất mới dùng ngày xử lý. Một
dòng đầu file theo dạng
`TITLE: <chủ đề cuộc họp>` có thể cung cấp display title/topic cho summary; nó
không thay đổi meeting date.

`start_date` của từng task ưu tiên ngày bắt đầu explicit như `từ ngày
20/01/2026`; nếu không có thì dùng effective meeting date ở trên. Trường này
không để rỗng. Request metadata vẫn có authority cao hơn context inference.

Preferred upload là self-contained package:

```text
=== MEETING METADATA ===
meeting_id: meeting-001
meeting_title: Weekly Sync
meeting_date: 2026-07-27
=== END MEETING METADATA ===
=== MEETING NOTE ===
<optional note>
=== END MEETING NOTE ===
=== TRANSCRIPT ===
<raw transcript>
```

API tách metadata/note trước preprocessing. Package được tạo bởi
`scripts/prepare_power_automate_uploads.py`; không sửa fixture bằng tay.

Response:

```json
{
  "meeting_title": "Weekly Sync",
  "summary": "Cuộc họp thống nhất ...",
  "tasks": [
    {
      "task_name": "Chuẩn bị hồ sơ",
      "assignee": "Phương",
      "start_date": "2026-07-27",
      "due_date": "2026-07-30",
      "due_date_text": "trước thứ Sáu",
      "evidence": "Phương: Em sẽ chuẩn bị hồ sơ trước thứ Sáu.",
      "status": "Proposed"
    }
  ],
  "diagnostics": {
    "caption_count": 100,
    "deduplicated_caption_count": 92,
    "candidate_window_count": 8,
    "ai_batch_count": 2,
    "terminal_replay_blocked_count": 0,
    "recap_scope": "NONE",
    "ai_call_rate": 0.25
  },
  "unresolved_window_ids": []
}
```

## Process uploaded transcript asynchronously

## Process meeting asynchronously from JSON

`POST /api/v1/meetings/jobs/process`

Nhận cùng JSON schema với `POST /api/v1/meetings/process`, gồm cả
`meeting_note` optional, rồi trả `202` với job envelope bên dưới. Dùng endpoint
này khi client đã có transcript trong JSON nhưng không thể chờ AI fallback.

`POST /api/v1/meetings/jobs/process-file`

Contract request và headers giống `process-file`, nhưng endpoint trả ngay để
Power Automate không chờ transcript dài hoặc nhiều AI batch hoàn thành:

```json
{
  "job_id": "job-...",
  "status": "queued",
  "created": true,
  "status_url": "/api/v1/meetings/jobs/job-..."
}
```

Gọi `GET /api/v1/meetings/jobs/{job_id}` với `X-API-Key`. Response có `status`
`queued`, `running`, `succeeded` hoặc `failed`. Khi `succeeded`, field `result`
chứa toàn bộ output `meeting_title`, `summary`, `tasks`, `diagnostics` và
`unresolved_window_ids`.

`idempotency_key` nội bộ được tạo từ file name và bytes file. Một retry cùng file
khi backend chưa restart trả lại cùng `job_id`, không tạo thêm job. Job store là
in-memory cho PoC local; restart backend làm mất các job đang chờ/running.
