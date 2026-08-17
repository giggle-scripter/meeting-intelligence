# Project context: Meeting Task Pipeline

Tài liệu này mô tả trạng thái đang sử dụng của project tại ngày **2026-08-17**.
Một developer hoặc phiên chat mới có thể dùng riêng tài liệu này để hiểu runtime,
pipeline, API, evaluation và Power Automate trước khi thay đổi code.

Khi có thông tin mâu thuẫn, dùng thứ tự ưu tiên sau:

1. Code và automated tests trong `backend/`.
2. Ground truth đã review trong `data/validation/`.
3. Report của đúng lần chạy đang được phân tích.
4. Tài liệu này và `README.md`.

## 1. Mục tiêu sản phẩm

Project nhận transcript cuộc họp tiếng Việt/Anh và trả về:

- tiêu đề và summary cuộc họp;
- danh sách task còn hiệu lực;
- assignee, ngày bắt đầu và deadline;
- raw deadline phrase và evidence để kiểm tra;
- diagnostics và các vùng chưa giải quyết được.

Input hỗ trợ `.txt`, `.vtt`, `.srt`. FastAPI là application chính. Cùng app có
thể chạy bằng Uvicorn, Docker hoặc Azure Functions.

Public output luôn giữ contract:

```text
meeting_title
summary
tasks
diagnostics
unresolved_window_ids
```

Task do hệ thống tạo chỉ là proposal. Pipeline không tự phê duyệt hoặc giao việc
thật cho người dùng.

## 2. Trạng thái vận hành hiện tại

| Hạng mục | Trạng thái đang dùng |
| --- | --- |
| Pipeline | `v1` |
| Meeting context | `assist` |
| Backend | FastAPI `backend.app.main:app` |
| Chế độ kiểm chứng chính | Local deterministic/rule-only |
| Corpus | 86 reviewed cases, W1-W5 |
| Automated tests | 253 tests |
| OpenAI model mặc định khi bật | `gpt-5-mini` |
| AI role | Optional mutation resolver |
| Job storage | In-memory |
| Power Automate | Contract đã có; rollout đang tạm dừng để hoàn thiện local quality và AI strategy |

Hai report rule-only mới nhất sau logic fix:

```text
evaluation/runtime/logic-fix-full-without-notes.json
evaluation/runtime/logic-fix-full-with-notes.json
```

| Mode | Pass | Precision | Recall | Field accuracy |
| --- | ---: | ---: | ---: | ---: |
| Without Meeting Note | 16/86 | 0.4074 | 0.5560 | 0.8604 |
| With Meeting Note | 15/86 | 0.4271 | 0.6029 | 0.8573 |

Các số trên không dùng provider. `with notes` có precision/recall cao hơn nhưng
không có nghĩa Meeting Note luôn cải thiện mọi field hoặc mọi case.

Chất lượng hiện chưa đạt production. False create, missed create, task identity,
owner/date mutation và long-distance state vẫn là nhóm lỗi chính.

## 3. Nguyên tắc kiến trúc

Kiến trúc là **Python-first, AI-last**:

- Python xử lý parsing, normalization, cue/date annotation, task creation,
  stable identity, reducer, date arithmetic, evidence, summary và output.
- AI chỉ xử lý mutation mơ hồ đối với task identity đã có.
- AI không trả final task list, summary hoặc evidence.
- AI không tính calendar date, tạo clause ID hoặc task ID.
- Provider không khả dụng không làm fail toàn meeting; pipeline trả kết quả
  deterministic và ghi unresolved windows.
- Raw transcript không bị rewrite hoặc loại khỏi rule engine.
- Mọi event được reduce theo chronology toàn cục.

AI boundary hiện rất bảo thủ. Đây là safety property, nhưng cũng giới hạn mức
uplift: AI không thể sửa một missed task khi Python chưa tạo được identity. Vì
chưa có full paired AI-backed run trên code mới nhất, không được tuyên bố
AI-backed tốt hơn rule-only ở thời điểm hiện tại.

## 4. Thành phần đang dùng

```text
meeting-intelligent/
├── backend/
│   ├── app/
│   │   ├── main.py                  API, auth, provider selection, jobs
│   │   ├── pipeline.py              V1 orchestration
│   │   ├── config.py                Environment settings
│   │   ├── jobs.py                  In-memory async job store
│   │   ├── trace.py                 Opt-in JSON trace
│   │   ├── evaluation.py            Deterministic comparator
│   │   ├── ingestion/               TXT/VTT/SRT và meeting package
│   │   ├── preprocessing/           Caption, speaker, turn, sentence, clause
│   │   ├── annotation/              Cue flags và date mentions
│   │   ├── candidate/               Candidate routing, compaction, batching
│   │   ├── ml/                      Embedding interface/registry; chưa nối V1
│   │   ├── ai/                      Rule extractors và provider clients
│   │   ├── reduction/               Task ledger, linking, reducer, reconciliation
│   │   ├── dates/                   Meeting/start/deadline resolution
│   │   ├── output/                  Evidence, summary, serializer
│   │   ├── models/                  Domain contracts
│   │   ├── v2/context/              Shared meeting-context code used by V1
│   │   └── utils/                   IDs, normalization, similarity
│   ├── tests/                       Unit, integration, API, end-to-end tests
│   ├── function_app.py              Azure Functions ASGI entrypoint
│   ├── host.json
│   └── requirements.txt
├── data/
│   ├── validation/                  Ground truth của 86 cases
│   ├── fixtures/                    Automated-test fixtures
│   ├── ml/action-classifier/        Generated clause dataset và grouped folds
│   └── power_automate_uploads/      Self-contained A/B upload packages
├── evaluation/
│   ├── runtime/                     Reports/traces của các lần chạy hiện tại
│   ├── ground-truth-audit.json
│   └── meeting-note-audit.json
├── scripts/
│   ├── evaluate_dataset.py
│   ├── run_validation_batches.ps1
│   ├── run_local_openai.ps1
│   ├── run_pipeline.py
│   ├── preprocess_transcript.py
│   ├── generate_candidates.py
│   ├── audit_ground_truth.py
│   ├── audit_ai_candidate_identity.py
│   ├── prepare_power_automate_uploads.py
│   ├── smoke_upload_package.py
│   └── summarize_openai_usage.py
├── power-automate/                  Flow contract và JSON schemas
├── sp365/                           Microsoft Lists mapping
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

## 5. Setup và chạy backend

### 5.1 Cài môi trường

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
```

### 5.2 Environment tối thiểu cho rule-only

```powershell
$env:POWER_AUTOMATE_API_KEY = "mi-demo-secret"
$env:PIPELINE_VERSION = "v1"
$env:MEETING_CONTEXT_MODE = "assist"
$env:AI_TIMEOUT_SECONDS = "3600"
$env:JOB_TIMEOUT_SECONDS = "3600"
```

Không đặt `OPENAI_API_KEY`, `AZURE_AI_FOUNDRY_CHAT_ENDPOINT` hoặc
`AI_FALLBACK_ENDPOINT` nếu muốn chắc chắn chạy deterministic-only.

### 5.3 Uvicorn

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app `
  --host 127.0.0.1 `
  --port 8010
```

```text
GET http://127.0.0.1:8010/health
Swagger: http://127.0.0.1:8010/docs
```

### 5.4 Docker

```powershell
docker build -t meeting-task-pipeline .
docker run --rm -p 8000:8000 --env-file .env meeting-task-pipeline
```

### 5.5 Azure Functions

`backend/function_app.py` wrap cùng FastAPI app bằng `func.AsgiFunctionApp`.
Business logic không được copy sang Azure Function hoặc Power Automate.

## 6. Input contract

Domain input nội bộ là `MeetingInput`:

```text
meeting_id
meeting_title
meeting_date
meeting_date_source
transcript_raw
file_name
meeting_note
```

JSON API nhận:

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

Effective meeting date được chọn theo thứ tự:

1. request/header date;
2. date trong self-contained package;
3. một full date không mâu thuẫn trong transcript;
4. một full date không mâu thuẫn trong Meeting Note;
5. ngày xử lý hiện tại.

Nguồn ngày được ghi vào diagnostics để audit.

## 7. Self-contained upload package

File dùng cho Power Automate và evaluator có thể chứa metadata, Meeting Note và
transcript trong cùng payload:

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

Nếu không có Meeting Note, bỏ note section. Parser tách package trước
preprocessing nên marker không trở thành transcript clause.

Generated fixtures:

```text
data/power_automate_uploads/<case-id>.txt
data/power_automate_uploads/<case-id>__with-note.txt
```

Không sửa fixture bằng tay. Regenerate từ `data/validation`:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_power_automate_uploads.py
```

Lệnh này đồng bộ toàn bộ output directory và xóa file không thuộc generated
fixture. Không lưu tài liệu thủ công trong `data/power_automate_uploads`.

## 8. Pipeline V1

```text
MeetingInput
  -> parse meeting package
  -> select TXT/VTT/SRT parser
  -> deduplicate caption updates
  -> normalize speaker
  -> build turns, sentences and clauses
  -> assign stable IDs and global order_index
  -> extract date mentions and purpose
  -> annotate semantic cues
  -> build MeetingContext and ground optional note cues
  -> build and merge candidate windows
  -> local rule extraction
  -> create provisional references for explicit existing-task labels
  -> contextual commitment/reference restoration
  -> extract recap and authoritative snapshots
  -> build bounded task memory for eligible AI mutations
  -> compact and batch optional provider context
  -> apply event authority and deduplication
  -> reduce all events through global Task Ledger by chronology
  -> deterministic reconciliation and final active-state filter
  -> resolve start dates and deadlines
  -> build evidence, summary and diagnostics
  -> PipelineResult
```

### 8.1 Candidate routing

Candidate cues gồm commitment, assignment, confirmation, correction,
cancellation, rejection, concrete action, date mention và grounded note cue.

- Clear positive assignment/commitment: local rule.
- Mutation có explicit stable target: local rule.
- Mutation/coreference thiếu unique target: AI-eligible.
- Brainstorm, hypothetical, question, suggestion, past completed, future
  discussion, progress-only và administrative follow-up: context/no create.

Window ban đầu lấy ba clause trước và năm clause sau focus. Window gần hoặc
overlap được merge. Rule engine vẫn nhận toàn bộ clauses; compaction chỉ giới
hạn provider input.

### 8.2 Event contract

```text
TASK_CREATE
TASK_COMMITMENT
OWNER_ASSIGN
OWNER_REASSIGN
DEADLINE_SET
DEADLINE_REPLACE
TASK_CANCEL
TASK_REJECT
```

Python còn dùng internal `TASK_REFERENCE` cho task được nhắc bằng label cụ thể
nhưng chưa đủ authority để xuất public task.

### 8.3 Task Ledger

Task Ledger giữ stable ID, canonical action, aliases, ordered assignees,
deadline history, source/event history, status và unresolved mutations.

Nguyên tắc reducer:

- owner assignment thêm owner;
- owner reassignment thay owner;
- deadline replacement ghi đè deadline của đúng identity;
- cancellation/rejection giữ audit state nhưng loại task khỏi final output;
- task terminal không tự reopen;
- update-only event không link được sẽ unresolved, không tạo task mới;
- similarly named sibling tasks chỉ merge khi reconciliation có evidence và
  unique margin đủ mạnh.

### 8.4 Recap authority

Recap chỉ prune active snapshot khi cấu trúc và scope cho thấy đây là danh sách
toàn bộ trạng thái cuối. Partial recap chỉ bổ sung/update task được link.

Pipeline hỗ trợ numbered rows, owner-action rows, inline bullets, multi-owner
bằng `và`/`and`/`&`, cancellation/completion footer và mutation sau recap.

Post-recap deadline mơ hồ chỉ được link khi có đúng một task phù hợp. Nếu có
nhiều target, reducer không đoán.

## 9. Meeting Note

Meeting Note là additive context, không phải complete snapshot.

| Source | Local task authority | AI cue | Summary |
| --- | ---: | ---: | ---: |
| `SECRETARY` | Có, nếu assertion tích cực và cụ thể | Có | Có |
| `PARTICIPANT` | Có, nếu assertion tích cực và cụ thể | Có | Có |
| `MANUAL` | Có, nếu assertion tích cực và cụ thể | Có | Có |
| `AUTO_OVERVIEW` | Không | Context-only | Có |

Human note chỉ tạo event khi có concrete action và đủ owner/commitment evidence.
Question, uncertainty, suggestion, noise hoặc note thiếu action không tạo task.

Transcript vẫn là nguồn chronology. Correction, reassignment, cancellation và
rejection trong transcript được áp dụng lên task do note tạo. Note thiếu một
task không có nghĩa task đó bị xóa.

AI không được phát event chỉ dựa trên note cue. Mọi AI event phải anchor tới
primary transcript clause.

## 10. Date và start-date semantics

Date mention có purpose `START_DATE`, `MEETING_DATE` hoặc `DEADLINE`.

`start_date` không để trống. Thứ tự chọn:

1. start date explicit của task trong transcript hoặc trusted human note;
2. effective meeting date;
3. processing date nếu không có nguồn đáng tin cậy hơn.

Deadline rules:

- `trước/before D` không có giờ: due date là `D - 1 ngày`;
- `trước 18h D`, `trước cuối ngày D`: due date vẫn là `D`;
- `vào/on/by/chậm nhất D`: due date là `D`;
- ngày/tháng thiếu năm dùng năm meeting và rollover nếu mốc đã qua;
- calendar duration cộng từ resolved task start date;
- working-day duration chưa có calendar implementation thì để `due_date` rỗng;
- event-dependent hoặc ambiguous deadline để `due_date` rỗng;
- `due_date_text` giữ phrase từ evidence để audit.

Handoff shift dạng `... đến thứ X` và `... từ thứ Y` được serialize để receiving
shift bắt đầu sau ngày shift trước kết thúc.

## 11. AI boundary hiện tại

Provider priority:

1. `OPENAI_API_KEY` → OpenAI Responses API;
2. `AZURE_AI_FOUNDRY_CHAT_ENDPOINT` → Azure Foundry chat completions;
3. `AI_FALLBACK_ENDPOINT` → generic HTTP extractor;
4. không có provider → `DisabledAiClient`.

OpenAI và Foundry dùng `backend/app/ai/prompt.txt`.

AI schema chỉ cho phép:

```text
OWNER_ASSIGN
OWNER_REASSIGN
DEADLINE_SET
DEADLINE_REPLACE
TASK_CANCEL
TASK_REJECT
```

Provider nhận bounded clauses, primary IDs, cues, date mention IDs, context
digest, note cues và tối đa 10 existing task candidates.

Mỗi accepted event phải:

- anchor vào primary transcript clause;
- dùng source/date IDs có trong input;
- chọn `related_task_id` từ supplied candidates;
- có explicit transcript evidence;
- qua strict schema và Python semantic guards.

Nếu window không có task candidate, pipeline không gọi provider. Nếu không có
unique target, AI trả unresolved. AI không có quyền tạo identity mới.

AI path chỉ nên xem là experimental cho tới khi full paired run trên cùng code
chứng minh quality uplift. Power Automate không nên bật OpenAI mặc định.

AI evaluation phải đo provider calls, accepted events/call, unresolved
reduction, case improvements/regressions, quality, latency và token cost.

## 12. API

| Method | Endpoint | Vai trò |
| --- | --- | --- |
| GET | `/health` | Health check |
| POST | `/api/v1/transcripts/preprocess` | Inspect parser và clauses |
| POST | `/api/v1/meetings/process` | Sync JSON processing |
| POST | `/api/v1/meetings/process-file` | Sync binary processing |
| POST | `/api/v1/meetings/jobs/process` | Async JSON submit |
| POST | `/api/v1/meetings/jobs/process-file` | Async binary submit |
| GET | `/api/v1/meetings/jobs/{job_id}` | Poll job |

Khi `POWER_AUTOMATE_API_KEY` có giá trị, processing endpoints yêu cầu:

```text
X-API-Key: <secret>
```

Khi setting rỗng, auth được bỏ qua cho local development.

### 12.1 File request

```text
Content-Type: application/octet-stream
X-API-Key: <secret>
X-File-Name-Base64: <Base64 UTF-8 file name>
X-Meeting-Id: <optional override>
X-Meeting-Title-Base64: <optional override>
X-Meeting-Date: YYYY-MM-DD <optional override>
```

Base64 headers truyền Unicode an toàn qua Power Automate. Plain `X-File-Name`
và `X-Meeting-Title` dùng được cho ASCII. Header metadata override package.

### 12.2 Async lifecycle

Submit trả HTTP 202:

```json
{
  "job_id": "job-...",
  "status": "queued",
  "created": true,
  "status_url": "/api/v1/meetings/jobs/job-..."
}
```

Poll status: `queued`, `running`, `succeeded`, `failed`. Khi `succeeded`, output
nằm trong `result`. Khi `failed`, đọc `error` và không tạo proposal.

Job idempotency bao gồm content, meeting ID/title/date, file name, speaker
aliases, pipeline version, prompt version, model và config liên quan.

Job store nằm trong memory. Restart backend làm mất job; poll sau restart có thể
trả 404.

## 13. Output contract

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

`due_date` có thể rỗng. `start_date` phải có giá trị. Assignee nhiều người được
serialize bằng `; `.

Diagnostics chính:

```text
caption_count
deduplicated_caption_count
turn_count
sentence_count
clause_count
candidate_window_count
rule_event_count
ai_event_count
ai_window_count
ai_provider_call_count
ai_context_clause_count
ai_fallback_error_count
unresolved_window_count
ai_call_rate
ai_clause_coverage
ai_batch_count
event_count_before_deduplication
event_count_after_deduplication
ledger_task_created_count
ledger_task_updated_count
exact_id_link_count
exact_alias_link_count
semantic_link_count
unresolved_mutation_count
terminal_replay_blocked_count
provisional_task_created_count
provisional_task_promoted_count
provisional_promotion_blocked_count
ambiguous_identity_mutation_blocked_count
sibling_identity_split_count
unauthorized_creation_blocked_count
ledger_unknown_task_id_rejection_count
ai_contract_rejection_count
ai_structural_contract_rejection_count
ai_semantic_rejection_count
meeting_date_source
effective_meeting_date
explicit_task_start_date_count
recap_scope
```

## 14. Power Automate và Microsoft Lists

### 14.1 Trạng thái hiện tại

Backend contract và schemas đã có. Việc triển khai flow được tạm dừng trong lúc
local pipeline và vai trò AI được đánh giá lại. Khi test lại, chạy rule-only
trước; không đặt OpenAI key trên backend dùng cho flow cho tới khi AI gate đạt.

Power Automate chỉ làm orchestration. Không port parser, rule, date resolver,
Task Ledger hoặc reducer vào flow.

### 14.2 Flow chuẩn

```text
SharePoint/OneDrive file trigger
  -> set source status = Processing
  -> Get file content
  -> POST /api/v1/meetings/jobs/process-file
  -> store job_id and status_url
  -> poll GET status_url until succeeded or failed
  -> on succeeded: upsert MI Meetings by MeetingId
  -> parse result.tasks
  -> upsert MI Task Proposals by ProposalKey
  -> set meeting/source status = Success
  -> on failed/timeout/404: store error and set Failed
```

`status_url` bắt đầu bằng `/`. API base URL phải không có trailing slash khi
concat. Không tạo URL dạng `//api/v1/...`.

Flow gửi binary content, `X-API-Key` và `X-File-Name-Base64`. Metadata A/B đã
nằm trong generated package; meeting headers chỉ dùng khi cần override.

### 14.3 Idempotent writes

Không create blindly khi retry:

- MI Meetings: unique `MeetingId`, Get items rồi Create/Update;
- MI Task Proposals: unique `ProposalKey`, Get items rồi Create/Update.

Nếu lưu đồng thời transcript-only và with-note của cùng case, variant phải tham
gia storage key.

### 14.4 MI Meetings mapping

| Column | Source |
| --- | --- |
| `Title` | `result.meeting_title` |
| `MeetingId` | request/package `meeting_id` |
| `MeetingDate` | effective meeting date |
| `Summary` | `result.summary` |
| `ProcessingStatus` | `Writing`, `Success`, `Failed` |
| `UnresolvedWindowCount` | `length(result.unresolved_window_ids)` |
| `NeedsReview` | true khi có unresolved hoặc task proposal |
| `ProcessedAt` | `utcNow()` |

### 14.5 MI Task Proposals mapping

| Column | Source |
| --- | --- |
| `ProposalKey` | `<MeetingId>|<TaskSequence>` |
| `Meeting` | Numeric SharePoint item ID của MI Meeting |
| `MeetingIdText` | meeting ID |
| `TaskSequence` | index trong `result.tasks` |
| `Title` | `task_name` |
| `AssigneeText` | `assignee` |
| `StartDate` | `start_date` |
| `DueDate` | `due_date`; để trống nếu `""` |
| `DueDateText` | `due_date_text` |
| `Evidence` | `evidence` |
| `Status` | `Proposed` |

`Meeting` là lookup và phải nhận numeric SharePoint item ID.

Schemas:

```text
power-automate/schemas/job-submit.schema.json
power-automate/schemas/job-status.schema.json
power-automate/schemas/pipeline-output.schema.json
```

### 14.6 Flow safety

- API base URL và key là solution environment variables.
- Bật Secure inputs/outputs cho submit, poll và Parse JSON.
- Chỉ map output khi job `succeeded`.
- Timeout, backend `failed` và 404 phải đi Catch path.
- Không tạo proposal khi chưa có final result.
- Luôn lưu unresolved count và `NeedsReview`.

## 15. Validation corpus

```text
data/validation/<case-id>/
├── metadata.json
├── transcript.txt
├── meeting_note.txt
└── expected_output.json
```

| Wave | Cases | Mục tiêu |
| --- | ---: | --- |
| W1 | 20 | Short baseline và negative cases |
| W2 | 30 | Feature isolation |
| W3 | 20 | Medium interactions và recap |
| W4 | 12 | Long-distance state mutation |
| W5 | 4 | Extra-long stress |

`data/validation` là source of truth. Không sửa `expected_output.json` chỉ để
rule pass. Ground truth chỉ thay đổi sau khi review transcript, final state và
lý do audit.

## 16. Test và evaluation workflow

### 16.1 Automated suite

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Trạng thái hiện tại: **253 tests passed**.

### 16.2 Action-classifier dataset

```powershell
.\.venv\Scripts\python.exe scripts\build_action_classifier_dataset.py `
  data\validation `
  --output-dir data\ml\action-classifier
```

Builder không sửa `data/validation`. Positive mapping ưu tiên reviewed evidence;
vì corpus hiện chưa lưu `tasks[*].evidence`, task-name fallback chỉ được nhận khi
vượt threshold và unique margin cùng owner/date/cue support. Mapping chưa chắc
chắn có `manual_review_required=true`, `label=null` và không eligible để train.
`folds.json` group toàn bộ record theo `meeting_id` để tránh leakage.

### 16.3 Một transcript local, rule-only

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py meeting.txt `
  --meeting-id local-001 `
  --title "Local Meeting" `
  --date 2026-08-17
```

### 16.4 Inspect preprocessing và candidates

```powershell
.\.venv\Scripts\python.exe scripts\preprocess_transcript.py meeting.txt `
  --date 2026-08-17

.\.venv\Scripts\python.exe scripts\generate_candidates.py meeting.txt `
  --date 2026-08-17
```

### 16.5 Targeted case

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 `
  --context-mode assist `
  --without-meeting-notes `
  --case-id W3-MED-C4-N2-PROD-INT-007 `
  --report evaluation\runtime\targeted.json
```

### 16.6 Full rule-only A/B

```powershell
# Without notes
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 `
  --context-mode assist `
  --without-meeting-notes `
  --report evaluation\runtime\full-without-notes.json

# With notes
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 `
  --context-mode assist `
  --report evaluation\runtime\full-with-notes.json
```

Evaluator ghi report trước khi trả exit code. Exit code khác 0 là bình thường
khi còn case mismatch; phải đọc report thay vì coi đó là execution failure.

### 16.7 Local OpenAI smoke

Không cần Uvicorn hoặc Power Automate:

```powershell
$env:OPENAI_API_KEY = "<secret>"
$env:OPENAI_MODEL = "gpt-5-mini"
$env:OPENAI_REASONING_EFFORT = "medium"
$env:AI_TIMEOUT_SECONDS = "3600"

.\scripts\run_local_openai.ps1 `
  -CaseId W4-LONG-C5-N1-SW-STATE-009
```

Script chạy automated tests, sau đó A/B without/with notes bằng
`evaluate_dataset.py --local-openai`. Nếu mục tiêu là diagnostic dù case chưa
đạt expected output, dùng `-AllowQualityFailures`; provider/contract errors vẫn
phải fail.

### 16.8 File-package smoke trước Power Automate

Chỉ chạy sau khi backend local đã sẵn sàng:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_upload_package.py `
  data\power_automate_uploads\W1-SHORT-C1-N0-IT-DASG-ABS-002.txt `
  --expect-task-count 2 `
  --output evaluation\runtime\local-package-smoke.json
```

Luôn test local package trước khi upload cùng file lên flow.

## 17. Evaluation interpretation

Deterministic comparator là blocking source cho missing, unexpected, task
precision/recall, field accuracy và case pass/fail.

Semantic similarity hoặc AI review chỉ gợi ý cho con người; không được tự sửa
expected output hoặc biến mismatch thành pass.

Khi so rule-only và AI-backed, hai run phải cùng code revision, selected cases,
note/context mode, meeting metadata, prompt version và evaluator mode.

AI-backed chỉ có giá trị nếu accepted AI events cải thiện final output. Provider
call thành công nhưng không tạo accepted event không phải quality improvement.

## 18. Environment settings

| Setting | Current default/meaning |
| --- | --- |
| `POWER_AUTOMATE_API_KEY` | Shared secret; empty disables auth locally |
| `MAX_TRANSCRIPT_CHARACTERS` | `500000` |
| `PIPELINE_VERSION` | `v1` |
| `MEETING_CONTEXT_MODE` | `assist` |
| `MEETING_NOTE_MAX_CHARACTERS` | `50000` |
| `AI_MAX_BATCH_CONTEXT_CLAUSES` | `56` |
| `AI_TIMEOUT_SECONDS` | `3600` |
| `JOB_TIMEOUT_SECONDS` | `3600` |
| `PIPELINE_TRACE_ENABLED` | `false` |
| `PIPELINE_TRACE_DIRECTORY` | `evaluation/traces` |
| `OPENAI_API_KEY` | Enables OpenAI client |
| `OPENAI_MODEL` | `gpt-5-mini` |
| `OPENAI_REASONING_EFFORT` | `medium` |
| `AZURE_AI_FOUNDRY_CHAT_ENDPOINT` | Enables Foundry when OpenAI key is absent |
| `AI_FALLBACK_ENDPOINT` | Enables generic HTTP provider |
| `NOTE_GROUNDING_THRESHOLD` | `0.72` |
| `NOTE_GROUNDING_MARGIN` | `0.12` |
| `TOPIC_LIKELY_THRESHOLD` | `0.45` |
| `MAX_MEETING_TOPICS` | `12` |
| `MAX_TOPIC_KEYWORDS` | `8` |
| `EMBEDDING_MODEL_NAME` | Multilingual model name; infrastructure only |
| `EMBEDDING_DEVICE` | `cpu` |
| `EMBEDDING_FALLBACK_ENABLED` | `true`; deterministic hashing fallback |
| `EMBEDDING_FALLBACK_DIMENSION` | `384` |
| `ACTION_CLASSIFIER_MODEL_PATH` | Empty; classifier not active yet |

Optional pricing inputs only estimate trace cost:

```text
OPENAI_INPUT_USD_PER_1M
OPENAI_CACHED_INPUT_USD_PER_1M
OPENAI_OUTPUT_USD_PER_1M
```

Không commit `.env`, API keys hoặc transcript thật.

## 19. Trace và dữ liệu nhạy cảm

```powershell
$env:PIPELINE_TRACE_ENABLED = "true"
$env:PIPELINE_TRACE_DIRECTORY = "evaluation/traces"
```

Trace chứa raw clauses, annotations, MeetingContext, note cues, events, ledger,
evidence và final tasks. Dù không chứa credential, đây vẫn là dữ liệu nhạy cảm.

```powershell
.\.venv\Scripts\python.exe scripts\summarize_openai_usage.py `
  --trace-dir evaluation\traces
```

## 20. Current limitations

1. Full rule-only quality mới đạt 15-16/86 exact case pass.
2. AI boundary chưa xử lý được missed task identity và chưa có current full
   paired run chứng minh uplift sau logic fix mới nhất.
3. Job store không durable; restart làm mất jobs.
4. Chưa có blind corpus từ meeting thực tế để đo generalization.
5. Long transcript vẫn khó ở task identity, recap scope và mutation target.
6. Raw deadline phrase có thể khác expected formatting dù task, owner và
   resolved date đúng; không được bỏ raw evidence chỉ để tăng exact score.
7. Power Automate chưa nên bật AI hoặc rollout tiếp trước khi local gates đạt.
8. Trace có thể chứa dữ liệu cuộc họp nhạy cảm.

## 21. Quy tắc khi thay đổi project

- Không hardcode case ID hoặc nguyên văn transcript vào runtime rule.
- Chỉ thêm rule cho một semantic group có thể mô tả tổng quát.
- Mỗi positive rule phải có negative test tương ứng.
- Không dùng Meeting Note như complete snapshot.
- Không xóa raw clauses hoặc safety clauses để giảm false positive.
- Không cho AI tính ngày, tạo ID hoặc quyết định final state.
- Không cho update-only event mint task identity.
- Không merge sibling tasks chỉ vì tên gần giống.
- Không sửa expected output nếu chưa review transcript và final state.
- Giữ public output backward compatible.
- Test local trước transport và Power Automate.
- Không chạy paid AI full corpus trước targeted smoke và candidate preflight.
- Khi báo metric, ghi pipeline, context mode, note mode, provider/model/prompt
  và local/API/job mode.

## 22. Gate trước khi tiếp tục Power Automate

1. `pytest backend/tests -q` pass toàn bộ.
2. Targeted positive và negative cases pass theo mục tiêu thay đổi.
3. Full rule-only without/with notes không regression pass-set.
4. Nếu thay AI boundary: paired rule-only/AI-backed trên cùng subset.
5. AI run không có provider, structural contract hoặc unknown-ID errors.
6. AI-backed phải cải thiện final quality, không chỉ giảm unresolved.
7. File-package smoke local pass.
8. Sau đó mới test submit/poll/upsert trên Power Automate.

## 23. Prompt cho phiên làm việc mới

```text
Bạn đang làm việc trong repo meeting-intelligent.

Trước khi hành động, hãy đọc toàn bộ docs/project-context.md. Dùng code/tests,
data/validation và report của đúng lần chạy làm source of truth.

Runtime hiện tại là V1, context mode assist. Rule-only là baseline kiểm chứng;
AI chỉ là optional mutation resolver và chưa được coi là có uplift nếu chưa có
paired evaluation trên cùng code. Power Automate đang tạm dừng rollout; mọi logic
fix phải test local trước.

Kiểm tra worktree trước khi sửa. Không hardcode case wording. Mỗi positive rule
phải có negative test. Sau thay đổi, chạy automated tests, targeted regression
và full A/B with/without notes. Không sửa expected output chỉ để rule pass.

Nhiệm vụ: <điền yêu cầu ở đây>.
```
