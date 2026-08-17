# Azure và Power Automate

Tài liệu này triển khai luồng tự động từ transcript file đến hai SharePoint
Lists. Người dùng chỉ upload file; không copy/paste transcript vào flow.

## Luồng hoàn chỉnh

```text
SharePoint Transcript Inbox
  -> When a file is created (properties only)
  -> Get file content
  -> HTTP POST FastAPI job endpoint
  -> Do until poll trạng thái job
       -> parse/deduplicate/split transcript
       -> deterministic rules and date resolution
       -> AI fallback only for ambiguous windows
       -> final summary and tasks JSON
  -> Parse JSON
  -> Create MI Meetings item
  -> Apply to each task
       -> Create MI Task Proposals item
```

## 1. Test tự động

Mỗi test case là một thư mục:

```text
data/validation/<case-id>/
  metadata.json
  transcript.txt | transcript.vtt | transcript.srt
  expected_output.json
```

Chạy trực tiếp pipeline Python:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --report evaluation\local-report.json
```

Chạy cùng bộ case qua API local:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --endpoint http://127.0.0.1:8000/api/v1/meetings/process `
  --report evaluation\local-api-report.json
```

Lệnh trên phù hợp cho case ngắn. Với transcript dài hoặc có nhiều window cần AI,
dùng job API để submit/poll thay vì giữ một request đồng bộ:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --job-endpoint http://127.0.0.1:8010/api/v1/meetings/jobs/process-file `
  --api-key $env:POWER_AUTOMATE_API_KEY `
  --timeout 3600 `
  --poll-interval 15 `
  --report evaluation\local-job-api-report.json
```

Chạy qua Azure:

```powershell
$env:POWER_AUTOMATE_API_KEY="<secret>"
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --endpoint https://<function-app>.azurewebsites.net/api/v1/meetings/process `
  --report evaluation\azure-report.json
```

Runner trả exit code `1` nếu có case fail, nên có thể dùng trong GitHub Actions
hoặc Azure DevOps. Nó tính case pass rate, task precision, task recall và field
accuracy; đồng thời chỉ rõ task thiếu, task thừa và field sai.

Test Power Automate end-to-end không cần nhập tay: upload nhiều transcript vào
thư mục test của document library để trigger tự chạy. Tuy nhiên, nên dùng runner
API ở trên cho regression thường xuyên; Power Automate chỉ cần smoke test cho
connector, mapping và SharePoint write.

## 2. Deploy backend lên Azure Functions

Project root của Azure Functions là thư mục `backend`, không phải root repo.

### Tạo Function App lần đầu

1. Vào Azure Portal, chọn **Create a resource** → **Function App**.
2. Chọn subscription, resource group và tên app duy nhất.
3. Chọn runtime **Python** và version tương ứng môi trường local.
4. Chọn Linux Flex Consumption cho PoC hoặc plan phù hợp policy của công ty.
5. Bật Application Insights, tạo app và chờ deployment hoàn tất.

### Chạy Azure Functions local

```powershell
Copy-Item backend\local.settings.example.json backend\local.settings.json
Set-Location backend
func start
```

Kiểm tra:

```powershell
Invoke-RestMethod http://localhost:7071/health
```

### Publish

Đăng nhập Azure bằng Azure CLI hoặc VS Code, sau đó:

```powershell
Set-Location backend
func azure functionapp publish <function-app-name>
```

### Application settings

Trong Azure Portal:

```text
Function App
  -> Settings
  -> Environment variables
  -> App settings
```

Thêm:

```text
POWER_AUTOMATE_API_KEY=<random-secret>
MAX_TRANSCRIPT_CHARACTERS=500000
AI_FALLBACK_ENDPOINT=
AI_FALLBACK_API_KEY=
AI_TIMEOUT_SECONDS=3600
AI_MAX_BATCH_CONTEXT_CLAUSES=56

# Phương án PoC ưu tiên: backend gọi OpenAI trực tiếp cho window mơ hồ.
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-5-mini
OPENAI_REASONING_EFFORT=medium

# Phương án ưu tiên: backend gọi Foundry trực tiếp
AZURE_AI_FOUNDRY_CHAT_ENDPOINT=
AZURE_AI_FOUNDRY_API_KEY=
AZURE_AI_FOUNDRY_MODEL=
AZURE_AI_FOUNDRY_API_VERSION=2024-05-01-preview
```

Save rồi restart app. Không đưa secret vào repo hoặc flow description.

### Sửa và deploy lại

1. Sửa code local.
2. Chạy `pytest` và `evaluate_dataset.py`.
3. Chạy lại `func azure functionapp publish <function-app-name>`.
4. Chạy regression bằng Azure endpoint.
5. Chỉ sau khi Azure pass mới sửa URI hoặc mapping trong Power Automate.

## 3. Chuẩn bị SharePoint

Tạo document library `MI Transcript Inbox` với các cột:

| Column | Type | Required |
| --- | --- | --- |
| MeetingId | Single line of text | Yes |
| MeetingTitle | Single line of text | Yes |
| MeetingDate | Date only | Yes |
| ProcessingStatus | Choice: Pending, Processing, Success, Failed | No |
| ProcessingError | Multiple lines of text | No |

Giữ hai Lists `MI Meetings` và `MI Task Proposals` theo
`sp365/integration-contract.md`.

Trong `MI Meetings`, bật unique cho `MeetingId` và thêm
`UnresolvedWindowCount`, `NeedsReview`. Trong `MI Task Proposals`, bật unique
cho `ProposalKey` và thêm `MeetingIdText`. Đây là idempotency ở tầng lưu trữ;
job idempotency của backend không thay thế được nó.

## 4. Tạo main cloud flow

Tạo flow trong solution với tên `MI - Process Transcript File`.

Tạo hai solution environment variables:

```text
MI_API_BASE_URL = https://<api-host>       # không có slash cuối
MI_API_KEY      = <secret>
```

Quick Tunnel đổi URL mỗi lần chạy, vì vậy chỉ cần cập nhật
`MI_API_BASE_URL`; không sửa từng HTTP action.

Đầu flow, initialize hai String variables `ApiBaseUrl` và `ApiKey` từ current
value của hai environment variables trên. Bật Secure inputs/outputs cho action
khởi tạo `ApiKey`.

### Trigger

Chọn SharePoint **When a file is created (properties only)**:

```text
Site Address: SharePoint site
Library Name: MI Transcript Inbox
Folder: /Incoming
```

Trigger này chỉ trả metadata. File content được lấy ở action tiếp theo.

Thêm trigger condition hoặc Condition đầu flow để chỉ nhận file có đủ
`MeetingId`, `MeetingTitle`, `MeetingDate` và `ProcessingStatus = Pending`.
Ngay khi bắt đầu, update source file thành `Processing`, xóa `ProcessingError`.

### Get file content

Thêm OneDrive for Business hoặc SharePoint **Get file content**:

```text
File: Id/Identifier từ trigger
```

### HTTP - Submit processing job

Thêm action **HTTP**:

```text
Method: POST
URI: concat(variables('ApiBaseUrl'), '/api/v1/meetings/jobs/process-file')
Headers:
  Content-Type: application/octet-stream
  X-API-Key: variables('ApiKey')
  X-File-Name-Base64: base64(Name từ trigger)
```

Body:

```text
File Content từ action Get file content
```

Fixture corpus/A/B được generate dưới dạng self-contained package, nên backend
đọc canonical MeetingId/title/date từ file. Không duy trì ba giá trị này trong
một nhánh flow song song. Với raw transcript không có package metadata, flow có
thể gửi `X-Meeting-Id`, `X-Meeting-Title-Base64`, `X-Meeting-Date` làm override;
nếu date vẫn thiếu, backend mới suy context rồi fallback ngày xử lý. File name
dùng Base64 vì HTTP header chỉ an toàn với ASCII.

Bật **Secure inputs** và **Secure outputs** cho HTTP action vì run history có thể
chứa transcript và API key. Trong **Settings**, đặt **Retry policy = None**:
backend đã deduplicate retry theo file, nhưng bỏ retry giúp tránh hai flow run cùng
poll một job trong demo.

### Poll job status

1. Thêm **Initialize variable**: `JobStatus`, type `String`, value `queued`.
2. Thêm **Initialize variable**: `JobResult`, type `Object`, value `{}`.
3. Thêm **Do until** với condition:

```text
or(equals(variables('JobStatus'), 'succeeded'), equals(variables('JobStatus'), 'failed'))
```

4. Trong loop, thêm **Delay**: `PT15S`.
5. Sau Delay, thêm HTTP `GET`:

```text
URI: concat(variables('ApiBaseUrl'), body('HTTP_-_Submit_processing_job')?['status_url'])
Headers:
  X-API-Key: variables('ApiKey')
```

6. Set `JobStatus` = `body('HTTP_-_Poll_job_status')?['status']`.
7. Nếu status là `succeeded`, set `JobResult` =
`body('HTTP_-_Poll_job_status')?['result']`.
8. Cấu hình Do until timeout phù hợp transcript demo, ví dụ `PT20M`, count `100`.
   Nếu hết timeout hoặc status `failed`, chuyển Scope Catch/Update file properties
   thành `Failed`; không ghi item vào Lists.

Bật **Secure inputs/outputs** cho poll vì response cuối chứa summary, evidence và
raw diagnostics. Khi status `failed`, copy field `error` vào `ProcessingError`.
Nếu poll trả `404`, backend local đã restart và mất in-memory job; đánh dấu
`Failed`, không tự submit lại trong cùng flow run.

Power Automate dùng `JobResult` cho các bước Parse JSON và Create item dưới đây.

### Parse JSON

Thêm Data Operations **Parse JSON**:

```text
Content: `variables('JobResult')`
Schema: power-automate/schemas/pipeline-output.schema.json
```

Bật Secure inputs/outputs cho Parse JSON. Hai schema envelope để test/debug là
`job-submit.schema.json` và `job-status.schema.json`.

### Upsert meeting

1. **Get items** từ `MI Meetings`, filter theo `MeetingId`, Top Count `1`.
2. Nếu chưa có, **Create item** với `ProcessingStatus=Writing`.
3. Nếu đã có, dùng item hiện tại và **Update item** về `Writing` trước khi ghi
   task. Không tạo parent thứ hai.

| Column | Value |
| --- | --- |
| Title | `meeting_title` từ Parse JSON |
| MeetingId | `MeetingId` từ trigger |
| MeetingDate | `MeetingDate` từ trigger |
| Summary | `summary` từ Parse JSON |
| ProcessingStatus | `Writing` |
| UnresolvedWindowCount | `length(body('Parse_JSON')?['unresolved_window_ids'])` |
| NeedsReview | `or(greater(length(body('Parse_JSON')?['unresolved_window_ids']), 0), greater(length(body('Parse_JSON')?['tasks']), 0))` |
| ProcessedAt | để trống đến khi ghi task xong |

### Create tasks

1. Initialize variable `TaskSequence`, type Integer, value `0`.
2. Add **Apply to each** với input `tasks` từ Parse JSON.
3. Tắt concurrency của loop để sequence không bị trùng.
4. Trong loop, increment `TaskSequence` thêm `1`.
5. Tạo `ProposalKey` bằng
   `concat(MeetingId, '|', string(variables('TaskSequence')))`. Dùng **Get
   items** theo key này; Create nếu chưa có, Update nếu đã có.

| Column | Value |
| --- | --- |
| ProposalKey | expression ở trên |
| Meeting | `ID` từ Create meeting |
| MeetingIdText | `MeetingId` từ trigger |
| TaskSequence | variable `TaskSequence` |
| Title | `item()?['task_name']` |
| AssigneeText | `item()?['assignee']` |
| StartDate | `item()?['start_date']` |
| DueDate | expression bên dưới |
| DueDateText | `item()?['due_date_text']` |
| Evidence | `item()?['evidence']` |
| Status | `item()?['status']` |

DueDate expression:

```text
if(empty(item()?['due_date']), null, item()?['due_date'])
```

Sau khi Apply to each hoàn tất, update `MI Meetings` thành `Success`, đặt
`ProcessedAt=utcNow()`, rồi update source file thành `Success`.

### Chống ghi trùng và xử lý lỗi

Trước HTTP, chỉ xử lý `Pending`; khi flow bắt đầu chuyển sang `Processing`.
MeetingId và ProposalKey phải unique, và mọi write dùng Get items +
Create/Update. Nhờ vậy retry sau lỗi SharePoint không nhân đôi parent/task.

Đặt các action chính trong Scope `Try`. Tạo Scope `Catch`, cấu hình **Run after**
khi `Try` failed hoặc timed out; update source file và Meeting (nếu đã tạo)
thành `Failed`, lưu error. Không đổi task `Proposed` thành approved/active trong
flow này; bước human review là flow riêng.

## 5. AI fallback prompt

AI không nhận toàn bộ transcript. Backend chỉ gửi các candidate window mơ hồ tới
AI-last provider.

### Phương án A: OpenAI Responses API (khuyến nghị cho PoC)

Đặt key tại môi trường chạy backend, không đưa key vào Power Automate:

```text
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-5-mini
```

Backend gửi prompt hiện có cùng strict JSON Schema tới OpenAI chỉ khi rule engine
đánh dấu window mơ hồ. `gpt-5-mini` phù hợp cho trích xuất JSON ngắn với ngân sách
PoC; có thể đổi model bằng `OPENAI_MODEL` mà không đổi flow hoặc schema.
`OPENAI_REASONING_EFFORT=medium` là mặc định để model xử lý tham chiếu ngữ cảnh
trong window; dùng `high` khi cần ưu tiên chất lượng cho batch mơ hồ phức tạp,
đổi lại latency và chi phí cao hơn. Chi phí chỉ phát sinh cho những window thực sự
được gọi fallback.

### Phương án B: Microsoft Foundry trực tiếp

Tạo model deployment hỗ trợ structured outputs, sau đó copy full Chat
Completions URL vào:

```text
AZURE_AI_FOUNDRY_CHAT_ENDPOINT=https://<resource>.services.ai.azure.com/models/chat/completions
AZURE_AI_FOUNDRY_API_KEY=<secret>
AZURE_AI_FOUNDRY_MODEL=<deployment-name>
AZURE_AI_FOUNDRY_API_VERSION=2024-05-01-preview
```

Backend gửi strict JSON Schema và chỉ gọi model cho window mơ hồ. Không đặt đồng
thời `AI_FALLBACK_ENDPOINT` trừ khi cần fallback qua custom service; Foundry được
ưu tiên khi cả hai có giá trị.

### Phương án C: AI Builder qua HTTP flow

Đặt custom flow URL vào `AI_FALLBACK_ENDPOINT`.

Prompt dùng trong AI Builder:

```text
power-automate/prompts/ambiguous-event-extractor.txt
```

Tạo một Text input tên `WindowJson`, chọn JSON output và dùng:

```text
power-automate/schemas/ai-event-output.sample.json
```

Contract đầy đủ:

```text
power-automate/schemas/ai-event-output.schema.json
```

Prompt chỉ trả event. Python vẫn chịu trách nhiệm tính ngày, evidence, task state
và summary cuối, nên model không được tự tạo `due_date`.

### Nối AI Builder với backend

Tạo flow riêng `MI - Extract Ambiguous Events`:

```text
When an HTTP request is received
  -> Compose string(triggerBody())
  -> Run a prompt: WindowJson = output Compose
  -> Response 200: structured JSON output của prompt
```

Copy HTTP POST URL của trigger vào Azure setting `AI_FALLBACK_ENDPOINT`. Nếu URL
đã có SAS signature, `AI_FALLBACK_API_KEY` có thể để trống sau khi backend hỗ trợ
endpoint không cần header riêng; nếu tenant yêu cầu key riêng, cấu hình cả hai.
Không bật asynchronous response vì backend cần event JSON ngay trong request.

Nếu chưa tạo flow AI fallback, để `AI_FALLBACK_ENDPOINT` trống. Pipeline vẫn chạy
rule-first và trả các window chưa giải quyết trong `unresolved_window_ids`.

## 6. Cách dùng sau khi triển khai

1. Upload `.txt`, `.vtt` hoặc `.srt` vào `/Incoming`.
2. Điền `MeetingId`, `MeetingTitle`, `MeetingDate`.
3. Flow tự chạy.
4. Kiểm tra file chuyển `ProcessingStatus=Success`.
5. Kiểm tra một item trong `MI Meetings`.
6. Kiểm tra các item liên quan trong `MI Task Proposals`.
7. Nếu status `Failed`, mở run history và xem Scope `Catch` cùng HTTP status.

## 7. Thêm hoặc sửa field

Khi thêm field mới:

1. Thêm field vào model/output backend.
2. Cập nhật `pipeline-output.schema.json`.
3. Cập nhật expected output và chạy regression.
4. Tạo cột trong SharePoint.
5. Refresh schema/action trong Power Automate.
6. Map field trong Create item.
7. Export lại solution sau smoke test.

Không sửa riêng prompt để thêm field deterministic. Prompt chỉ thay đổi khi event
contract thực sự thay đổi.
