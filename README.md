# Meeting Intelligent

Pipeline **Python-first, AI-last** dùng để chuyển transcript cuộc họp tiếng Việt/Anh thành các **đề xuất công việc (task proposals)** có thể kiểm tra và truy vết lại nguồn.

Backend hỗ trợ transcript định dạng `.txt`, `.vtt`, `.srt` và trả về:

- tiêu đề và tóm tắt cuộc họp;
- danh sách task còn hiệu lực;
- người được giao việc, ngày bắt đầu và deadline;
- cụm từ deadline gốc và evidence tương ứng;
- diagnostics và các vùng chưa thể xử lý chắc chắn.

> **Trạng thái: experimental / pre-production.**  
> Rule-only hiện là baseline chính để validation. AI chỉ đóng vai trò **optional mutation resolver** và chưa nên bật mặc định trong Power Automate cho tới khi paired evaluation chứng minh được cải thiện thực sự trên final output.

---

## Kiến trúc

Nguyên tắc chính của project là **Python-first, AI-last**.

```text
Meeting input
  -> parse TXT / VTT / SRT hoặc self-contained meeting package
  -> chuẩn hóa caption, speaker, turn, sentence, clause
  -> annotate semantic cues và date mentions
  -> build MeetingContext và Meeting Note cues nếu có
  -> tạo candidate windows
  -> local rule extraction
  -> khôi phục task references / commitments
  -> optional bounded AI mutation resolution
  -> deduplicate và áp dụng event authority
  -> reduce toàn bộ events qua global Task Ledger
  -> reconcile final active state
  -> resolve start date và deadline
  -> build evidence, summary, diagnostics
  -> PipelineResult
```

Python chịu trách nhiệm cho:

- task creation;
- stable task identity;
- chronology;
- date arithmetic;
- evidence;
- reducer state;
- final serialization.

AI chỉ được dùng để xử lý một số **mutation mơ hồ** dựa trên các **task candidate đã tồn tại**.

AI **không** được:

- tạo task identity mới;
- tính calendar date;
- tạo clause ID hoặc task ID;
- trực tiếp quyết định final task list;
- trực tiếp tạo summary hoặc evidence.

Public output giữ contract cấp cao sau:

```text
meeting_title
summary
tasks
diagnostics
unresolved_window_ids
```

Task do pipeline sinh ra chỉ là **proposal**. Hệ thống không tự động phê duyệt hoặc giao việc thật cho người dùng.

---

## Trạng thái hiện tại

| Thành phần | Trạng thái |
| --- | --- |
| Pipeline | `v1` |
| Meeting context | `assist` |
| Backend | FastAPI — `backend.app.main:app` |
| Chế độ validation chính | Local deterministic / rule-only |
| Validation corpus | 86 reviewed cases, W1–W5 |
| Automated tests | 236 |
| OpenAI model mặc định khi bật | `gpt-5-mini` |
| Vai trò AI | Optional mutation resolver |
| Async job storage | In-memory |
| Power Automate | Đã có contract; rollout đang tạm dừng để hoàn thiện local quality |

Các rule-only report gần nhất:

```text
evaluation/runtime/logic-fix-full-without-notes.json
evaluation/runtime/logic-fix-full-with-notes.json
```

| Mode | Pass | Precision | Recall | Field accuracy |
| --- | ---: | ---: | ---: | ---: |
| Without Meeting Note | 16/86 | 0.4074 | 0.5560 | 0.8604 |
| With Meeting Note | 15/86 | 0.4271 | 0.6029 | 0.8573 |

Các chỉ số trên **chưa đạt mức production**.

Những nhóm lỗi chính hiện tại gồm:

- false task creation;
- missed task creation;
- task identity;
- owner/date mutation;
- long-distance state;
- recap scope và mutation target trong transcript dài.

Chi tiết implementation, evaluation semantics và error analysis nên được lưu ở:

```text
docs/project-context.md
```

---

## Cấu trúc repository

```text
meeting-intelligent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI, auth, providers, async jobs
│   │   ├── pipeline.py          # V1 orchestration
│   │   ├── config.py
│   │   ├── jobs.py
│   │   ├── ingestion/           # TXT/VTT/SRT + meeting package
│   │   ├── preprocessing/       # captions -> turns -> sentences -> clauses
│   │   ├── annotation/          # semantic cues + date mentions
│   │   ├── candidate/           # routing, windows, compaction, batching
│   │   ├── ai/                  # rule extractors + provider clients
│   │   ├── reduction/           # linking, Task Ledger, reducer, reconciliation
│   │   ├── dates/               # start/deadline resolution
│   │   ├── output/              # evidence, summary, serializer
│   │   └── models/
│   ├── tests/
│   ├── function_app.py          # Azure Functions ASGI entrypoint
│   └── requirements.txt
├── data/
│   ├── validation/              # reviewed ground truth
│   ├── fixtures/
│   └── power_automate_uploads/
├── evaluation/
├── scripts/
├── power-automate/
├── sp365/
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

Đọc `docs/project-context.md` trước khi thay đổi:

- kiến trúc;
- rule/reducer behavior;
- evaluation;
- AI boundary;
- Power Automate integration.

---

# Bắt đầu nhanh

Các lệnh bên dưới giả định đang dùng **Windows PowerShell** và chạy từ thư mục root của repository.

## 1. Tạo virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
```

---

## 2. Cấu hình local rule-only

```powershell
$env:POWER_AUTOMATE_API_KEY = "mi-demo-secret"
$env:PIPELINE_VERSION = "v1"
$env:MEETING_CONTEXT_MODE = "assist"
$env:AI_TIMEOUT_SECONDS = "3600"
$env:JOB_TIMEOUT_SECONDS = "3600"
```

Nếu muốn chắc chắn pipeline chạy **deterministic-only**, không đặt các biến:

```text
OPENAI_API_KEY
AZURE_AI_FOUNDRY_CHAT_ENDPOINT
AI_FALLBACK_ENDPOINT
```

Trong local development, có thể để `POWER_AUTOMATE_API_KEY` rỗng. Khi đó authentication ở processing endpoints sẽ được bỏ qua.

---

# Chạy backend bằng Uvicorn

Khởi động FastAPI:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app `
  --host 127.0.0.1 `
  --port 8010
```

Sau khi chạy thành công:

```text
Health:  http://127.0.0.1:8010/health
Swagger: http://127.0.0.1:8010/docs
```

Kiểm tra health endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
```

Giữ terminal này chạy trong suốt quá trình test.

---

# Mở backend local qua tunnel

Tunnel cần thiết khi **Power Automate hoặc một service bên ngoài cần gọi vào backend đang chạy trên máy local**.

Luồng kết nối:

```text
Power Automate / External client
            |
            v
    Public HTTPS tunnel
            |
            v
http://127.0.0.1:8010
            |
            v
        FastAPI
```

## Cloudflare Quick Tunnel

Trước tiên phải đảm bảo Uvicorn đang chạy tại:

```text
http://127.0.0.1:8010
```

Sau đó mở **terminal thứ hai** và chạy:

```powershell
cloudflared tunnel --url http://127.0.0.1:8010
```

`cloudflared` sẽ trả về một public HTTPS URL dạng:

```text
https://<random-name>.trycloudflare.com
```

Ví dụ:

```text
https://meeting-task-example.trycloudflare.com
```

Kiểm tra tunnel:

```powershell
Invoke-RestMethod https://meeting-task-example.trycloudflare.com/health
```

Nếu health endpoint trả response bình thường thì backend local đã có thể được gọi từ Internet.

---

## Cách chạy khi test Power Automate

Nên dùng hai terminal song song.

### Terminal 1 — Uvicorn

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app `
  --host 127.0.0.1 `
  --port 8010
```

### Terminal 2 — Cloudflare Tunnel

```powershell
cloudflared tunnel --url http://127.0.0.1:8010
```

Cả hai terminal phải được giữ mở trong suốt quá trình test.

> Quick Tunnel chỉ phù hợp cho development/testing. Public URL có thể thay đổi mỗi lần restart tunnel.

---

## API Base URL trong Power Automate

Giả sử tunnel sinh URL:

```text
https://abc-example.trycloudflare.com
```

thì dùng chính URL này làm API base URL:

```text
https://abc-example.trycloudflare.com
```

**Không thêm dấu `/` ở cuối.**

Ví dụ `status_url` backend trả về:

```text
/api/v1/meetings/jobs/job-123
```

URL đúng:

```text
https://abc-example.trycloudflare.com/api/v1/meetings/jobs/job-123
```

URL sai:

```text
https://abc-example.trycloudflare.com//api/v1/meetings/jobs/job-123
```

---

# API

| Method | Endpoint | Chức năng |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/transcripts/preprocess` | Kiểm tra parser và clauses |
| `POST` | `/api/v1/meetings/process` | Xử lý JSON đồng bộ |
| `POST` | `/api/v1/meetings/process-file` | Xử lý binary đồng bộ |
| `POST` | `/api/v1/meetings/jobs/process` | Submit async JSON job |
| `POST` | `/api/v1/meetings/jobs/process-file` | Submit async binary job |
| `GET` | `/api/v1/meetings/jobs/{job_id}` | Poll trạng thái async job |

Khi `POWER_AUTOMATE_API_KEY` có giá trị, các processing endpoint yêu cầu:

```text
X-API-Key: <secret>
```

---

## Ví dụ JSON input

```json
{
  "meeting_id": "meeting-001",
  "meeting_title": "Weekly Release Sync",
  "meeting_date": "2026-08-17",
  "file_name": "meeting.txt",
  "transcript": "[09:00:00] Lan: Em sẽ cập nhật dashboard trước thứ Sáu.",
  "speaker_aliases": {},
  "meeting_note": {
    "content": "- Lan cập nhật dashboard trước thứ Sáu.",
    "author": "Thư ký",
    "source": "SECRETARY"
  }
}
```

---

## Header khi gửi binary file

```text
Content-Type: application/octet-stream
X-API-Key: <secret>
X-File-Name-Base64: <Base64 UTF-8 file name>
X-Meeting-Id: <optional override>
X-Meeting-Title-Base64: <optional override>
X-Meeting-Date: YYYY-MM-DD <optional override>
```

Nên dùng Base64 cho filename/title khi cần truyền Unicode an toàn qua Power Automate.

---

## Ví dụ output

```json
{
  "meeting_title": "Weekly Release Sync",
  "summary": "Cuộc họp tập trung vào Weekly Release Sync...",
  "tasks": [
    {
      "task_name": "Cập nhật dashboard",
      "assignee": "Lan",
      "start_date": "2026-08-17",
      "due_date": "2026-08-20",
      "due_date_text": "trước thứ Sáu",
      "evidence": "Lan: Em sẽ cập nhật dashboard trước thứ Sáu.",
      "status": "Proposed"
    }
  ],
  "diagnostics": {},
  "unresolved_window_ids": []
}
```

Quy ước:

- `start_date` luôn phải có giá trị;
- `due_date` có thể rỗng nếu pipeline không thể resolve deadline một cách an toàn;
- `due_date_text` giữ lại cụm deadline gốc để audit;
- nhiều assignee được serialize bằng `; `.

---

# Async jobs

Async submit endpoint trả HTTP `202`:

```json
{
  "job_id": "job-...",
  "status": "queued",
  "created": true,
  "status_url": "/api/v1/meetings/jobs/job-..."
}
```

Lifecycle:

```text
queued -> running -> succeeded
                  -> failed
```

Khi `succeeded`, final output nằm trong:

```text
result
```

Khi `failed`, đọc:

```text
error
```

và **không tạo downstream task proposal**.

Hiện tại job store là **in-memory**.

Do đó:

```text
restart backend
    -> mất các job đang lưu
    -> poll job cũ có thể trả 404
```

---

# Self-contained upload package

Fixture dùng cho Power Automate hoặc evaluator có thể chứa metadata, Meeting Note và transcript trong cùng một file:

```text
=== MEETING METADATA ===
meeting_id: <id>
meeting_title: <title>
meeting_date: YYYY-MM-DD
=== END MEETING METADATA ===
=== MEETING NOTE ===
<optional human note>
=== END MEETING NOTE ===
=== TRANSCRIPT ===
<transcript>
```

Nếu không có Meeting Note thì bỏ toàn bộ section đó.

Regenerate Power Automate fixtures từ reviewed ground truth:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_power_automate_uploads.py
```

Không chỉnh sửa thủ công các generated file trong:

```text
data/power_automate_uploads/
```

---

# Chạy pipeline local và debug

Có thể chạy trực tiếp một transcript mà **không cần Uvicorn hoặc Power Automate**.

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py meeting.txt `
  --meeting-id local-001 `
  --title "Local Meeting" `
  --date 2026-08-17
```

---

## Inspect preprocessing

```powershell
.\.venv\Scripts\python.exe scripts\preprocess_transcript.py meeting.txt `
  --date 2026-08-17
```

---

## Inspect candidate generation

```powershell
.\.venv\Scripts\python.exe scripts\generate_candidates.py meeting.txt `
  --date 2026-08-17
```

---

# Test và evaluation

## Automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Trạng thái trong project context hiện tại:

```text
236 tests passed
```

---

## Chạy một validation case

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 `
  --context-mode assist `
  --without-meeting-notes `
  --case-id W3-MED-C4-N2-PROD-INT-007 `
  --report evaluation\runtime\targeted.json
```

---

## Full rule-only A/B

### Không dùng Meeting Note

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 `
  --context-mode assist `
  --without-meeting-notes `
  --report evaluation\runtime\full-without-notes.json
```

### Có Meeting Note

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 `
  --context-mode assist `
  --report evaluation\runtime\full-with-notes.json
```

Evaluator ghi report trước khi trả exit code.

Vì vậy, exit code khác `0` có thể chỉ có nghĩa là vẫn còn **quality mismatch**, không nhất thiết là runtime execution failure.

Luôn đọc report được sinh ra trước khi kết luận run bị lỗi.

---

# Optional OpenAI path

AI hiện vẫn là experimental.

Khi đánh giá AI-backed và rule-only, hai run phải dùng cùng:

- code revision;
- selected cases;
- meeting metadata;
- Meeting Note mode;
- context mode;
- prompt version;
- evaluator configuration.

Ví dụ local OpenAI smoke:

```powershell
$env:OPENAI_API_KEY = "<secret>"
$env:OPENAI_MODEL = "gpt-5-mini"
$env:OPENAI_REASONING_EFFORT = "medium"
$env:AI_TIMEOUT_SECONDS = "3600"

.\scripts\run_local_openai.ps1 `
  -CaseId W4-LONG-C5-N1-SW-STATE-009
```

Nếu đang chạy diagnostic và chấp nhận expected-output mismatch:

```powershell
.\scripts\run_local_openai.ps1 `
  -CaseId W4-LONG-C5-N1-SW-STATE-009 `
  -AllowQualityFailures
```

Provider/schema/contract error vẫn phải làm run fail.

AI-backed chỉ được coi là có giá trị khi **accepted AI events cải thiện final output**.

Provider call thành công nhưng không tạo accepted event không được tính là quality improvement.

---

# Tích hợp Power Automate

Power Automate chỉ đóng vai trò **orchestration layer**.

Không port các phần sau sang Power Automate:

- parser;
- preprocessing;
- rule engine;
- Task Ledger;
- reducer;
- date resolver;
- task reconciliation.

Flow chuẩn:

```text
SharePoint / OneDrive file trigger
  -> đánh dấu source = Processing
  -> Get file content
  -> POST /api/v1/meetings/jobs/process-file
  -> lưu job_id + status_url
  -> poll GET status_url
  -> nếu succeeded:
       upsert MI Meetings bằng MeetingId
       parse result.tasks
       upsert MI Task Proposals bằng ProposalKey
       đánh dấu Success
  -> nếu failed / timeout / 404:
       lưu error
       đánh dấu Failed
```

Các nguyên tắc quan trọng:

- gửi binary file content;
- gửi `X-API-Key` nếu authentication đang bật;
- ưu tiên `X-File-Name-Base64` khi filename có Unicode;
- chỉ ghi proposal sau khi backend job ở trạng thái `succeeded`;
- dùng idempotent upsert thay vì blind create;
- API base URL không có trailing slash;
- test file package local trước khi test Power Automate;
- chưa bật OpenAI mặc định trong flow cho tới khi AI-backed evaluation chứng minh được final-output uplift.

Schemas:

```text
power-automate/schemas/job-submit.schema.json
power-automate/schemas/job-status.schema.json
power-automate/schemas/pipeline-output.schema.json
```

---

# Local file-package smoke test

Chỉ chạy khi backend local đã sẵn sàng.

```powershell
.\.venv\Scripts\python.exe scripts\smoke_upload_package.py `
  data\power_automate_uploads\W1-SHORT-C1-N0-IT-DASG-ABS-002.txt `
  --expect-task-count 2 `
  --output evaluation\runtime\local-package-smoke.json
```

Nên chạy bước này trước khi upload cùng file vào Power Automate flow.

---

# Docker

Build image:

```powershell
docker build -t meeting-task-pipeline .
```

Run:

```powershell
docker run --rm -p 8000:8000 --env-file .env meeting-task-pipeline
```

---

# Azure Functions

Azure Functions dùng cùng FastAPI application qua:

```text
backend/function_app.py
```

`backend/function_app.py` wrap FastAPI app bằng `func.AsgiFunctionApp`.

Business logic không được duplicate sang Azure Functions hoặc Power Automate.

---

# Biến môi trường quan trọng

| Variable | Ý nghĩa / giá trị mặc định hiện tại |
| --- | --- |
| `POWER_AUTOMATE_API_KEY` | Shared secret; để rỗng sẽ disable auth ở local |
| `PIPELINE_VERSION` | `v1` |
| `MEETING_CONTEXT_MODE` | `assist` |
| `MAX_TRANSCRIPT_CHARACTERS` | `500000` |
| `MEETING_NOTE_MAX_CHARACTERS` | `50000` |
| `AI_MAX_BATCH_CONTEXT_CLAUSES` | `56` |
| `AI_TIMEOUT_SECONDS` | `3600` |
| `JOB_TIMEOUT_SECONDS` | `3600` |
| `PIPELINE_TRACE_ENABLED` | `false` |
| `PIPELINE_TRACE_DIRECTORY` | `evaluation/traces` |
| `OPENAI_MODEL` | `gpt-5-mini` |
| `OPENAI_REASONING_EFFORT` | `medium` |

Không commit:

```text
.env
API keys
real meeting transcripts
sensitive trace output
```

---

# Hạn chế hiện tại

- Exact case pass của rule-only trên reviewed corpus vẫn thấp.
- AI không thể sửa một missed task identity nếu Python chưa tạo được identity đó.
- Chưa có full paired run mới nhất chứng minh chắc chắn AI tốt hơn rule-only sau các logic fix hiện tại.
- Async jobs chưa persistent qua backend restart.
- Vẫn cần blind corpus từ meeting thực tế để đánh giá generalization.
- Transcript dài vẫn khó ở task identity, recap scope và mutation target.
- Trace có thể chứa nội dung cuộc họp nhạy cảm.
- Working-day duration chưa có đầy đủ calendar implementation thì `due_date` có thể để rỗng.

---

# Workflow phát triển

Trước khi thay đổi runtime behavior:

1. Xem code/tests và reviewed ground truth trong `data/validation` là source of truth chính.
2. Không hardcode case ID hoặc nguyên văn transcript cụ thể vào runtime rules.
3. Mỗi positive rule nên có negative regression test tương ứng.
4. Không coi Meeting Note là complete final snapshot.
5. Không cho AI tính ngày, tạo ID, tạo task identity hoặc quyết định final state.
6. Giữ public output backward compatible.
7. Test local trước khi test transport hoặc Power Automate.
8. Không sửa `expected_output.json` chỉ để rule pass.

Thứ tự validation khuyến nghị:

```text
pytest
  -> targeted positive + negative regressions
  -> full rule-only without Meeting Notes
  -> full rule-only with Meeting Notes
  -> paired AI evaluation nếu AI boundary thay đổi
  -> local file-package smoke
  -> Power Automate submit / poll / upsert test
```

---

# Source of truth

Khi các nguồn thông tin mâu thuẫn, ưu tiên theo thứ tự:

1. Code và automated tests trong `backend/`.
2. Reviewed ground truth trong `data/validation/`.
3. Report của đúng lần chạy đang được phân tích.
4. `docs/project-context.md` và README này.

Đọc `docs/project-context.md` trước khi thay đổi kiến trúc, evaluation logic hoặc Power Automate integration.
