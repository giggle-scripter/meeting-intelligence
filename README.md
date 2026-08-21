# Meeting Task Pipeline

PoC chuyển transcript cuộc họp `.txt`, `.vtt` hoặc `.srt` thành meeting summary
và task proposal. Kiến trúc **Python-first, AI-last**: Python xử lý dữ liệu và
quy tắc xác định; AI chỉ hỗ trợ các đoạn thực sự mơ hồ.

Tài liệu onboarding đầy đủ cho developer hoặc phiên chat mới:
[docs/project-context.md](docs/project-context.md).

## Flow Tổng Quát

```text
Người dùng upload transcript
  -> OneDrive / SharePoint trigger
  -> Power Automate lấy file content
  -> HTTP POST /meetings/jobs/process-file
  -> FastAPI trả 202 + job_id ngay
  -> Python chạy nền: parse, chuẩn hóa, deduplicate và tách clause
  -> Rule engine trích task, liên kết trạng thái và resolve ngày
  -> Gom candidate window mơ hồ theo batch giới hạn kích thước
  -> AI fallback chỉ cho các batch mơ hồ (nếu được cấu hình)
  -> Power Automate poll GET /meetings/jobs/{job_id}
  -> Job succeeded trả JSON meeting + tasks
  -> Power Automate tạo item trong MI Meetings
  -> Power Automate lặp tasks, tạo item trong MI Task Proposals
```

Người dùng chỉ cần upload file; không cần copy/paste transcript để chạy flow.

## Quy Tắc Trích Task

Pipeline chỉ tạo task khi có bằng chứng cho một hành động cụ thể:

- Người nói tự cam kết thực hiện công việc.
- Người phụ trách được giao việc rõ ràng và việc giao đó còn hiệu lực.
- Một công việc bị thay deadline, đổi người phụ trách, từ chối hoặc hủy.
- Ý tưởng, khả năng, thảo luận cho phase/cuộc họp sau và công việc đã hoàn thành
  không tạo task.
- Khi một việc bị sửa hoặc giao lại, thông tin rõ ràng xuất hiện sau cùng được ưu
  tiên. Task bị hủy hoặc bị từ chối không được giữ lại trong output cuối.
- Một câu có thể chứa nhiều action độc lập; mỗi action tạo một task riêng.
- `evidence` mặc định lấy từ clause gốc trong transcript. Một Meeting Note tích
  cực, cụ thể từ `SECRETARY`, `PARTICIPANT` hoặc `MANUAL` là trusted additive
  evidence và có thể tạo `HUMAN_NOTE` task; evidence sẽ ghi rõ nguồn Meeting
  Note. Question, uncertainty và `AUTO_OVERVIEW` không tạo task.

## Quy Tắc Ngày

- `start_date` mặc định là ngày họp.
- Với flow upload file thiếu meeting date, backend tìm meeting-context date đầy
  đủ trong transcript rồi Meeting Note; nếu không có hoặc mâu thuẫn mới dùng
  ngày xử lý/upload.
- Khi nguồn có metadata chuẩn, có thể gửi `X-Meeting-Date: YYYY-MM-DD` để dùng
  ngày họp thực tế.
- Các mốc tuyệt đối không có năm được resolve theo ngày họp. Nếu mốc đó đã qua,
  pipeline hiểu là năm kế tiếp.
- `start_date` ưu tiên ngày bắt đầu explicit của task; nếu không có thì dùng
  effective meeting date và không bao giờ để rỗng.
- `trước ngày X` lưu `due_date` là ngày trước X; mốc có giờ như `trước 18h ngày X`
  vẫn có `due_date` là ngày X.
- Deadline phụ thuộc sự kiện, ví dụ `hai ngày sau khi nhận API spec`, không tự bịa
  ngày; pipeline giữ `due_date` trống và lưu nguyên `due_date_text`.

## Vai Trò AI Fallback

Prompt tại
[power-automate/prompts/ambiguous-event-extractor.txt](power-automate/prompts/ambiguous-event-extractor.txt)
không tạo final task. AI chỉ nhận các candidate window mơ hồ đã được gộp vào batch
giới hạn kích thước và trả event có schema cố định, ví dụ
`TASK_COMMITMENT`, `OWNER_REASSIGN`, `DEADLINE_REPLACE` hoặc
`TASK_CANCEL`.

Prompt phải tuân thủ các nguyên tắc sau:

- Chỉ dùng clause ID và date mention ID có trong input; không tự tạo ID hoặc tính
  ngày.
- Chỉ emit event khi có bằng chứng cụ thể; không suy đoán từ câu mơ hồ.
- Liên kết câu xác nhận, sửa, giao lại, từ chối hoặc hủy với action cùng window.
- Giữ trạng thái hiệu lực cuối cùng trong window; thông tin sửa sau ghi đè thông
  tin cũ.
- Trả JSON thuần theo schema `events`; không trả summary, evidence, date hay final
  task object.

Python kiểm tra schema, liên kết event toàn transcript, tính ngày, tạo evidence và
serialize output. Vì vậy pipeline vẫn chạy rule-only khi AI Builder không có
capacity; các window chưa giải quyết được được trả trong `unresolved_window_ids`.

Meeting metadata và Meeting Note có thể được đóng gói cùng file upload bằng
marker `=== MEETING METADATA ===`, optional `=== MEETING NOTE ===` và
`=== TRANSCRIPT ===`. Note được
compact thành cue cho AI; mọi event do AI trả về vẫn phải có clause transcript
hỗ trợ. Chi tiết policy: [docs/meeting-note-policy.md](docs/meeting-note-policy.md).

## Chạy Local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

$env:POWER_AUTOMATE_API_KEY="mi-demo-secret"
$env:AI_FALLBACK_ENDPOINT=""
$env:OPENAI_API_KEY="<key-nhận-từ-lead>"
$env:OPENAI_MODEL="gpt-5-mini"
$env:OPENAI_REASONING_EFFORT="medium"
$env:AI_TIMEOUT_SECONDS="3600"
$env:AI_MAX_BATCH_CONTEXT_CLAUSES="56"
uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
```

`OPENAI_REASONING_EFFORT` nhận `minimal`, `low`, `medium` hoặc `high`. Mặc định
`medium`; chỉ đặt `high` khi cần ưu tiên xử lý window mơ hồ phức tạp hơn latency
và chi phí. `OPENAI_API_KEY` chỉ đặt trong terminal, `.env` local hoặc Application Settings;
không dán vào source code, Power Automate hay Git. Khi có key, backend gọi OpenAI
Responses API cho window mơ hồ; không cần AI Builder capacity.

`AI_MAX_BATCH_CONTEXT_CLAUSES` mặc định là `56`: chỉ các window mơ hồ cách nhau
không quá 3 clause mới được gộp khi tổng context không vượt giới hạn. Window xa
nhau luôn thành request riêng để tránh coreference xuyên đoạn; đổi lại transcript
dài có thể cần nhiều provider call hơn. `AI_TIMEOUT_SECONDS=3600` chỉ là timeout **giữa backend và
OpenAI**; Power Automate phải dùng job API bên dưới để không chờ quá giới hạn HTTP.

Hạ tầng embedding local nằm trong `backend/app/ml/` và load model lazily, tối đa
một lần cho mỗi cấu hình trong một process. Có thể đặt `EMBEDDING_MODEL_NAME`,
`EMBEDDING_DEVICE` và `EMBEDDING_FALLBACK_DIMENSION`; nếu backend semantic không
khả dụng, registry đánh dấu rõ và dùng hashing embedding deterministic. Hạ tầng
này mặc định `off` nên không thay đổi output hiện tại. Create-proposal assist chỉ
được chạy cho uncertain band khi classifier/router cùng ở `assist` và cả
`TASK_CREATE_PROPOSAL_ENABLED=true`, `AI_CREATE_PROPOSAL_ENABLED=true`. Provider
chỉ đề xuất grounded spans; Python validator mới được promote `TASK_CREATE`, tối
đa `AI_CREATE_MAX_PROPOSALS_PER_MEETING` (mặc định `3`) mỗi meeting. Model artifact
lớn phải đặt trong `artifacts/models/`, không commit vào Git.

Semantic task identity retrieval có thể chạy telemetry-only bằng
`TASK_SEMANTIC_LINKER_MODE=shadow`. Index biểu diễn action, aliases, owners,
topic và entities; embedding cache nằm ngoài ledger domain object. Retrieval
luôn ưu tiên exact task ID rồi exact alias, sau đó mới tính lexical/semantic,
owner/topic và recency. Top-1 chỉ được đánh dấu direct khi đạt cả
`TASK_LINK_STRONG_THRESHOLD` và `TASK_LINK_MIN_MARGIN`; sibling gần nhau không
được merge. Shadow mode không thay target của reducer hay public output.

Bounded mutation context chạy telemetry-only bằng
`CONTEXT_RETRIEVAL_MODE=shadow` và yêu cầu semantic linker cũng ở `shadow`.

### Bounded AI mutation router

`AI_MUTATION_ROUTER_MODE=off` là mặc định và giữ nguyên output/legacy fallback.
`shadow` chỉ tạo top-k task, `ContextBundle` và trace (không provider call hay
event). `assist` yêu cầu candidate router ở `assist` và semantic/context
retrieval ở `shadow`; AI chỉ chọn trong tối đa 5 task và context tối đa 30 clause
/ 12.000 ký tự. Python kiểm tra task ID, clause/anchor, owner span, deadline
mention, chronology và confidence trước khi thêm `TaskEvent`.

### Meeting Note dual view

`NOTE_DUAL_VIEW_MODE=off` giữ policy note hiện tại. `shadow` tạo claim có ID ổn
định, retrieved transcript clauses và một trong bốn mức grounding:
`FULL_GROUNDED`, `PARTIAL_GROUNDED`, `NOTE_ONLY`, `CONTRADICTED`. Trong
`assist`, note direct-event path bị tắt; transcript chronology luôn là authority.
Topic index phân đoạn theo turn centroid đã smoothing cùng discourse marker,
sau đó tìm nearest topic trước nearest clause. Bundle luôn ưu tiên local
`-3/+5`, source evidence của top-k task, same-topic clauses, mutation history và
grounded note cues; không gửi full transcript. Hard caps không thể tăng quá
`CONTEXT_MAX_CLAUSES=30`, `CONTEXT_MAX_CHARACTERS=12000` và
`CONTEXT_MAX_TASKS=5`. Shadow chỉ ghi diagnostics/trace, không thay AI payload,
ledger, reducer hay public output.

Khi debug fallback trên máy local, đặt `AI_FALLBACK_DEBUG=true`. Terminal sẽ log
JSON event thô từ model và lý do event bị Python từ chối. Không bật cờ này ở môi
trường có transcript thật vì log có thể chứa nội dung meeting.

### Deterministic temporal semantics

`TEMPORAL_SEMANTICS_MODE=off` giữ resolver cũ. `shadow` parse mỗi date mention
thành AST và so sánh kết quả nhưng không đổi output. `assist` chỉ bổ sung due
date cho duration đã được grammar hỗ trợ mà legacy resolver không giải được;
không bao giờ ghi đè ngày exact của legacy. Date được tính hoàn toàn trong
Python, anchor event phải là ID exact do caller cung cấp, và working day hiện
tại chỉ bỏ thứ Bảy/CN (`TEMPORAL_WORKING_DAY_POLICY=weekdays-only-v1`).

### Quality attribution (Q0)

Để phân tích regression corpus mà không thay đổi output, chạy:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_quality_errors.py data\validation `
  --output evaluation\runtime\quality-attribution.json `
  --csv evaluation\runtime\quality-attribution.csv
.\.venv\Scripts\python.exe scripts\build_evidence_review_queue.py `
  evaluation\runtime\quality-attribution.json `
  --output evaluation\runtime\quality-review-queue.json `
  --csv evaluation\runtime\quality-review-queue.csv
```

Các taxonomy/evidence trong report là gợi ý deterministic có trạng thái
`NEEDS_REVIEW`; chúng không được dùng làm ground truth hoặc input production.
`evaluate_dataset.py` hiện report task identity F1 cùng expected/actual/matched,
missing và unexpected task counts.

- Health check: `http://127.0.0.1:8010/health`
- Swagger UI: `http://127.0.0.1:8010/docs`
- Khi dùng Cloudflare Quick Tunnel hoặc Azure Function, Power Automate gọi endpoint
  công khai tương ứng đến `/api/v1/meetings/jobs/process-file`. Endpoint đồng bộ
  `/api/v1/meetings/process-file` chỉ phù hợp test ngắn từ local/Swagger; không
  dùng cho transcript dài qua Power Automate.

## API Chính

- `GET /health`: kiểm tra service.
- `POST /api/v1/transcripts/preprocess`: xem caption, turn, sentence, clause và
  số liệu deduplication.
- `POST /api/v1/meetings/process`: nhận JSON đầy đủ từ client.
- `POST /api/v1/meetings/process-file`: nhận binary transcript và chờ xử lý xong;
  chỉ dùng cho test ngắn từ local/Swagger.
- `POST /api/v1/meetings/jobs/process`: nhận JSON và trả `202`/`job_id` ngay.
- `POST /api/v1/meetings/jobs/process-file`: nhận file và trả `202`/`job_id` ngay;
  dùng cho transcript dài có AI fallback.
- `GET /api/v1/meetings/jobs/{job_id}`: poll trạng thái job; khi `succeeded`, field
  `result` chứa cùng JSON như `process-file`.

Main flow phải dùng job endpoint. `POST` chỉ submit file và luôn kết thúc nhanh;
Power Automate poll mỗi 15 giây đến khi job `succeeded` hoặc `failed`, sau đó Parse
JSON field `result` trước khi ghi Lists. Job store hiện là in-memory cho PoC local:
nếu restart Uvicorn thì các job `queued`/`running` bị mất, cần upload lại file.

Upload endpoint cần:

```text
Content-Type: application/octet-stream
X-API-Key: <secret>
X-File-Name-Base64: <Base64 UTF-8 dynamic file name>
```

Generated Power Automate fixtures là self-contained package, nên flow không cần
gửi ID/title/date ở header. `X-Meeting-Id`, `X-Meeting-Title-Base64` và
`X-Meeting-Date` vẫn là optional override cho raw client cũ. Header Base64 hỗ
trợ Unicode mà không vi phạm giới hạn ASCII của HTTP client. Nếu cả package lẫn
header đều thiếu date, API mới dùng transcript/note context rồi processing date.

Test đúng file package qua local job API trước khi đưa vào flow:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_upload_package.py `
  data\power_automate_uploads\W1-SHORT-C1-N0-IT-DASG-ABS-002.txt `
  --expect-task-count 2 `
  --output evaluation\runtime\local-package-smoke.json
```

Response có dạng:

```json
{
  "meeting_title": "Weekly Sync",
  "summary": "Cuộc họp thống nhất ...",
  "tasks": [
    {
      "task_name": "Chuẩn bị hồ sơ",
      "assignee": "Phương",
      "start_date": "2026-07-29",
      "due_date": "2026-07-30",
      "due_date_text": "trước thứ Sáu",
      "evidence": "Phương: Em sẽ chuẩn bị hồ sơ trước thứ Sáu.",
      "status": "Proposed"
    }
  ],
  "diagnostics": {},
  "unresolved_window_ids": []
}
```

Chi tiết request/response: [docs/api.md](docs/api.md). Hướng dẫn Power Automate,
Azure và fallback: [docs/azure-power-automate.md](docs/azure-power-automate.md).

## Test Và Đánh Giá

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
python scripts\evaluate_dataset.py data\validation --report evaluation\latest-validation-report.json
```

Test local trực tiếp với OpenAI, không cần Uvicorn và không đi qua Power Automate:

```powershell
$env:OPENAI_API_KEY="<key>"
$env:OPENAI_MODEL="gpt-5-mini"
$env:OPENAI_REASONING_EFFORT="medium"
$env:AI_TIMEOUT_SECONDS="3600"

.\scripts\run_local_openai.ps1
```

Script chạy automated tests trong môi trường test cô lập trước (không dùng API
key và không phát sinh provider call), sau đó chạy cùng case A/B không note rồi
có note bằng `--local-openai`. Hai lượt A/B này mới gọi OpenAI. Report nằm dưới
`evaluation/runtime/local-openai-*`.

Vì pipeline là AI-last, một variant rõ ràng có thể kết thúc bằng route
`rule_only` hoặc `ai_fallback_skipped_no_candidate` và không phát sinh provider
call. Script coi đây là skip hợp lệ; các route khác mà không gọi provider vẫn
làm smoke fail. Tổng số OpenAI call thực tế luôn được in ở dòng cuối.

Mặc định mismatch với ground truth vẫn trả exit code fail. Khi chỉ khảo sát một
case đã biết chưa pass và vẫn muốn hoàn tất A/B, thêm `-AllowQualityFailures`;
report vẫn ghi đầy đủ mismatch và terminal vẫn hiển thị warning.

Chạy regression theo từng đợt qua API local:

```powershell
.\scripts\run_validation_batches.ps1 -Batch targeted
.\scripts\run_validation_batches.ps1 -Batch w1
.\scripts\run_validation_batches.ps1 -Batch w2
.\scripts\run_validation_batches.ps1 -Batch w3
.\scripts\run_validation_batches.ps1 -Batch w4
.\scripts\run_validation_batches.ps1 -Batch w5
```

Đối với W3/W4/W5, thêm `-JobApi` để dùng submit/poll thay vì request đồng bộ:

```powershell
.\scripts\run_validation_batches.ps1 -Batch w4 -JobApi -TimeoutSeconds 3600
```

Với case dài (W3 trở lên), dùng job API thay cho endpoint đồng bộ `/process`.
Script submit file, nhận `job_id`, rồi poll kết quả nên không giữ một HTTP request
mở trong toàn bộ thời gian OpenAI xử lý:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --job-endpoint "http://127.0.0.1:8010/api/v1/meetings/jobs/process-file" `
  --api-key "mi-demo-secret" `
  --timeout 3600 `
  --poll-interval 15 `
  --case-id "W4-LONG-C5-N2-SW-STATE-001" `
  --report "evaluation\validation-w4-job.json"
```

Các lệnh trên gọi trực tiếp FastAPI và ghi report vào `evaluation`; chúng không
trigger Power Automate và không ghi Microsoft Lists. Dùng `-RuleOnly` để chạy
baseline không gọi OpenAI, hoặc `-Batch all` để chạy toàn bộ corpus sau khi các
đợt nhỏ đã ổn định.

`data/validation` là corpus chuẩn đã review. Chỉ dùng metric từ các case đã được
review làm số liệu chính thức. Thư mục `data/power_automate_uploads` chứa 172 file
phẳng: 86 transcript-only giữ tên `<case-id>.txt` và 86 bản A/B có tên
`<case-id>__with-note.txt`; upload bản copy có tên mới để trigger lại flow.

Report có hai nhóm metric độc lập:

- **Chất lượng trích xuất:** `task_precision`, `task_recall`, `field_accuracy`,
  `case_pass_rate`. Chỉ công bố số liệu từ reviewed ground truth.
- **Tuyến xử lý:** mỗi case được gắn một `route` trong `case_execution`:
  `rule_only` (không cần AI), `ai_fallback_not_configured` (cần AI nhưng provider
  chưa cấu hình), `ai_fallback_resolved` (AI xử lý được window mơ hồ),
  `ai_fallback_unresolved` (AI không tạo được event có bằng chứng), hoặc
  `ai_fallback_failed` (lỗi provider/network). `fallback_metrics` đếm số case
  theo từng tuyến; `ai_window_count` là số window cần AI, còn
  `ai_provider_call_count` là số batch thực sự gọi OpenAI/provider. Vì vậy một
  transcript dài có thể có nhiều `ai_window_count` nhưng ít provider call hơn.

Khi sửa rule, prompt hoặc model, chạy lại toàn bộ suite. Không chỉnh prompt theo
từng transcript; chỉ thay đổi khi một **nhóm lỗi lặp lại** xuất hiện trong report.

### Live V1 gates qua API: smoke, W4/W5, full corpus

Script sau khởi động V1 local, tự tạo rule-only baseline đúng cùng subset, upload
case qua Job API, đóng gói Meeting Note, bắt buộc có provider call và kiểm tra
precision/recall/field-accuracy, contract, call và token thresholds. Model,
reasoning effort và prompt version được pin cho cả baseline và live report. Mặc
định chỉ chạy contract smoke 4 case.

```powershell
$env:OPENAI_API_KEY="<key-nhận-từ-lead>"
.\scripts\test_v1_live_full.ps1 -Scope smoke

# Chỉ sau khi smoke pass:
.\scripts\test_v1_live_full.ps1 -Scope gate-b

# Chỉ sau khi Gate B cải thiện trên matched baseline:
.\scripts\test_v1_live_full.ps1 -Scope full
```

`-RequireAllCasesPass` là điều kiện bổ sung; quantitative baseline gate luôn
được áp dụng. Mỗi lần chạy tạo thư mục
`evaluation/live-v1-<UTC timestamp>/`, gồm `v1-with-meeting-notes.json`,
`openai-usage.json` và trace riêng cho run đó. Pricing trong usage chỉ xuất hiện
khi cũng đặt các biến `OPENAI_*_USD_PER_1M`.

## Cấu Trúc Thư Mục

```text
backend/app/       API, pipeline, rule engine, AI adapter, date/output logic
backend/tests/     unit, integration và end-to-end tests
data/              fixtures, validation corpus và file upload cho Power Automate
scripts/           CLI preprocessing, evaluation và data preparation
power-automate/    prompt, schema và solution integration artifacts
sp365/             SharePoint List contracts
clients/csharp/    client C# gửi transcript file vào API
docs/              API, deployment và integration documentation
```

Tạo lại clause-level action-classifier dataset từ reviewed corpus:

```powershell
.\.venv\Scripts\python.exe scripts\build_action_classifier_dataset.py `
  data\validation `
  --output-dir data\ml\action-classifier
```

Builder khai thác task mapping chắc chắn, false-create và semantic hard
negatives; các mapping mơ hồ có `manual_review_required=true` và không đủ điều
kiện train. Fold luôn group theo `meeting_id` để tránh leakage giữa các clause.

Train model shadow nhẹ (scikit-learn chỉ cần ở môi trường train):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ml-train]"
.\.venv\Scripts\python.exe scripts\train_action_classifier.py
```

Linear head trong artifact JSON chạy thuần Python, nên API runtime không cần
sklearn; shadow mode vẫn cần optional `sentence-transformers` để tạo MiniLM
embedding. Model `action-clf-v1` chỉ được phép chạy `shadow`: nó ghi
prediction/version vào diagnostics nhưng không tham gia routing hay thay đổi
task output.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --reviewed-only `
  --action-classifier-mode shadow `
  --action-classifier-model-path data\ml\action-classifier\model\action-clf-v1.json `
  --report evaluation\action-classifier-shadow.json
```

Candidate evidence router cũng chỉ chạy shadow và yêu cầu classifier shadow:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --reviewed-only `
  --action-classifier-mode shadow `
  --candidate-router-mode shadow `
  --action-clear-threshold 0.82 `
  --action-ai-threshold 0.45 `
  --candidate-threshold-version candidate-router-thresholds-v1 `
  --report evaluation\candidate-router-shadow.json
```

### Span-grounded action candidates (Q1)

`ACTION_CANDIDATE_BUILDER_MODE=shadow` tạo `ActionCandidate` có action/owner
span, deadline mention ID, loại candidate và trạng thái evidence. Đây là telemetry
độc lập với candidate router cũ: không tạo event, không đổi task output và trace
ghi dưới `action_candidates_v2`. Dùng để đo candidate coverage trước khi bật
commitment routing ở PR sau:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --action-candidate-builder-mode shadow `
  --report evaluation\action-candidate-shadow.json
```

### Commitment authority router (Q2)

`COMMITMENT_ROUTER_MODE=assist` enforces the authority matrix before legacy
create events are reduced. Hard negatives, conditional work, and bounded
coordination follow-ups are retained in trace evidence but cannot mint a task.
The default active authorities are `DIRECT_ASSIGNMENT,SELF_COMMITMENT`; no AI
call is made by this router. Review `commitment_router_v2` in the opt-in trace
before changing `COMMITMENT_ROUTER_ACTIVE_TYPES`.

### Action canonicalization (Q3)

`ACTION_CANONICALIZATION_MODE=shadow` records a deterministic `ActionFrame`
with raw span, normalized verb/object, proposed canonical action and rejection
reason. Q3 does not change task names: its initial active experiment regressed
the Q2 F1 baseline, so promotion is deferred until the trace has reviewed
split/merge evidence.

### Recap lifecycle reconciliation (Q4)

`RECAP_RECONCILIATION_MODE=shadow` records generic recap rows whose proposed
action is actually recap metadata (for example an owner or deadline fragment).
It does not alter output yet: the first constrained active experiment improved
F1 only from `0.534` to `0.535`, below the Q4 slice gate.

Router hợp nhất rule/classifier/note evidence thành route có reasons, nhưng PR4
không execute bất kỳ route nào. `AI_CREATE_CHECK` chỉ tăng suppressed diagnostics;
AI create vẫn tắt và final task output vẫn do pipeline hiện tại quyết định.
