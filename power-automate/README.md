# Tích hợp Power Automate

Backend dùng trong flow phát hành là **V1** (`PIPELINE_VERSION=v1`). Lệnh
PowerShell để tự chạy Uvicorn, HTTPS tunnel và quản lý `X-API-Key` nằm tại
[hướng dẫn bản chốt](../docs/phat-hanh-v1-va-distillation.md#cách-chạy-v1-với-power-automate).
Pilot Windows không cần Azure, named tunnel và Access service token xem tại
[runbook triển khai không cần Azure](../docs/deployment-without-azure-vi.md).
V2.27/V3.1 không được nối vào endpoint hoặc Lists production của flow V1; nếu
chạy thử, phải dùng app/hostname và vùng staging riêng như phần dưới.

Tổng quan V1/V2 MVP, ranh giới bằng chứng và kế hoạch triển khai theo tuần (Azure
tùy chọn) xem
[tài liệu chia sẻ](../docs/v1-v2-mvp-vi.md) và
[kế hoạch triển khai](../docs/deployment-week-plan-vi.md).

Job endpoint đã qua full Gate C/replay contract. Power Automate tiếp tục là lớp
orchestration; không port parsing, rule, date hoặc reducer vào flow.

Flow dự kiến:

```text
SharePoint/OneDrive file trigger
  -> Get file content
  -> HTTP POST /api/v1/meetings/jobs/process-file
  -> Do until GET status_url is succeeded or failed
  -> Upsert MI Meeting bằng MeetingId
  -> Parse result.tasks
  -> Apply to each tasks
       -> Upsert MI Task Proposal bằng ProposalKey
  -> Đánh dấu Meeting và source file Success
```

Dataset smoke test có hai file cho mỗi case:

```text
<case-id>.txt              # metadata + transcript
<case-id>__with-note.txt   # metadata + Meeting Note + transcript
```

Generated fixture tự chứa `meeting_id`, `meeting_title` và `meeting_date`. Flow
chỉ cần gửi `X-File-Name-Base64`, `X-API-Key` và binary content. Các header
meeting metadata vẫn là optional override cho raw transcript/client cũ. Hai
variant A/B dùng cùng base metadata trong package; thêm variant vào key lưu trữ
nếu cần giữ đồng thời hai kết quả. `index.csv` chỉ là manifest/audit.

Không copy/paste transcript vào manual trigger. Custom Connector chỉ được tạo
sau khi HTTP action chạy ổn với public HTTPS endpoint (named tunnel là đường
pilot khuyến nghị; Azure là lựa chọn tùy chọn).

Submit trả `202` với `job_id`, `status=queued` và `status_url`. Poll bằng
`GET /api/v1/meetings/jobs/{job_id}` với cùng `X-API-Key`; trạng thái hợp lệ là
`queued`, `running`, `succeeded`, `failed`. Chỉ tạo Meeting và Task Proposal
khi `succeeded`, lấy payload từ `result`.

Các sửa bắt buộc trước demo:

- đặt API base URL và API key thành solution environment variables;
- bật Secure inputs/outputs cho submit, poll và Parse JSON;
- dùng `status_url` thay vì tự ghép route từ `job_id`;
- đặt `MeetingId` và `ProposalKey` unique, dùng Get items + Create/Update để
  retry flow không ghi trùng;
- lưu `UnresolvedWindowCount` và `NeedsReview`; task luôn ở trạng thái
  `Proposed`, không tự coi là công việc đã phê duyệt;
- source library có `Pending/Processing/Success/Failed`; Catch phải lưu job
  error và không tạo task khi backend trả `failed` hoặc `404` sau restart.

Contract JSON nằm trong `schemas/`: `job-submit.schema.json`,
`job-status.schema.json`, `pipeline-output.schema.json`.

Trước khi export/upload flow artifact, chạy regression package để xác nhận mọi
file phẳng vẫn chứa transcript, Meeting Note và metadata khớp corpus nguồn:

```powershell
.\.venv\Scripts\python.exe scripts\audit_power_automate_uploads.py `
  --report evaluation\power-automate-upload-audit.json
```

Audit này kiểm 86 source cases/172 variants hiện tại; không dùng expected task
count làm transport gate. Quality vẫn được đánh giá bằng evaluator riêng.

## Flow staging V2.27 (opt-in, không ghi V1 production Lists)

Flow staging V2 phải giữ các bước theo đúng thứ tự sau:

1. Submit transcript/audio tới V2.27 staging; lưu `job_id` và `status_url` từ
   response submit.
2. Poll `status_url` tới `succeeded` hoặc `failed`. Khi `succeeded`, lấy
   `content_hash` và `result.tasks` từ response poll.
3. Ghi toàn bộ `result.tasks` vào **SharePoint List staging** để human review.
   Reviewer có thể thêm, xóa hoặc sửa các row task cuối. Bước Approval rõ ràng
   phải trả về `reviewer` và thời điểm duyệt (`reviewed_at`). Backend không tự
   xác minh danh tính reviewer; backend tin vào flow đã kiểm soát quyền và gửi
   metadata đó.
4. Khi Approve, flow đọc **toàn bộ list cuối** (sau cả thêm/xóa/sửa), rồi POST
   JSON tới feedback endpoint. `corrected_final_tasks` là cả danh sách cuối,
   không phải chỉ các task đã thay đổi. Payload mẫu và schema ở
   [`examples/v227-feedback-request.json`](examples/v227-feedback-request.json)
   và [`schemas/v227-feedback-request.schema.json`](schemas/v227-feedback-request.schema.json).
   Endpoint hiện tại không nhận corrected transcript, corrected speaker hoặc
   audio/STT edit; lỗi loại này phải xử lý riêng.
5. Nếu reviewer chọn Reject, hoặc submit/poll thất bại, flow kết thúc với
   trạng thái lỗi/reject: **không POST feedback và không gửi email**. Chỉ sau
   feedback trả `201` (hoặc idempotent `200`) mới gửi email danh sách task đã
   approved; không email `result.tasks` chưa được duyệt. SharePoint List và
   endpoint đều ở staging, không ghi trực tiếp V1 production Lists.

Các nhánh lỗi bắt buộc:

- Submit trả non-2xx, poll timeout/`failed`, hoặc job `404`: đánh dấu source
  `Failed`, lưu error/job id và không tạo task, không POST feedback, không email.
- Feedback trả `4xx`: giữ review ở trạng thái cần sửa; không retry bằng payload
  khác cho cùng job. Feedback trả `5xx`/timeout chỉ retry cùng payload và giữ
  email ở trạng thái chờ.
- Email lỗi sau khi feedback đã accepted: giữ bằng chứng feedback approved,
  retry email approved; không quay lại gửi output chưa duyệt.

Intake chỉ bật khi service V2.27 được chạy với biến môi trường server
`V227_FEEDBACK_TENANT_ID` (không nhận tenant từ request). Có thể đặt thư mục
lưu riêng bằng `V227_FEEDBACK_DIRECTORY`; mặc định là
`evaluation/runtime/v227-feedback/<tenant>`. V1 không có route này và không bị
thay đổi. Transcript gốc được lưu trước khi submit job, theo `content_hash`
ổn định; bản ghi là append-only, không commit lên Git và không ghi transcript
vào log.

Sau khi poll job V2.27 ở trạng thái `succeeded`, Power Automate phải dừng ở một
bước human approval. Chỉ khi người duyệt chọn **Approve**, flow mới gọi:

```text
POST /api/v1/meetings/jobs/{job_id}/feedback
X-API-Key: <cùng key của V2.27>
{
  "job_id": "job-...",
  "content_hash": "<giá trị từ job status>",
  "corrected_final_tasks": [...],
  "approval_metadata": {
    "reviewer": "person@example.com",
    "reviewed_at": "2026-09-18T10:00:00+07:00",
    "approval": true
  }
}
```

Thiếu hoặc sai metadata bị từ chối; `job_id` và `content_hash` phải khớp job.
Schema chỉ khóa các trường task và metadata cần dùng; server hiện không từ chối
các property lạ ở top-level, nên không mô tả sai rằng chúng bị reject.
Một job chỉ nhận một bản correction: gửi lại đúng payload là idempotent, gửi
nội dung khác sẽ bị từ chối. Dữ liệu feedback giữ theo chính sách retention
của tenant và phải xóa định kỳ khỏi thư mục private sau khi hết hạn; không xóa
hay sửa giữa chừng bản ghi append-only để dùng lại cho training.

`POST /feedback` chỉ ghi correction record; nó không gọi
`train_v227_feedback.py`. Trong local MVP, trainer là CLI offline do operator
chạy thủ công sau Approval; flow không tự khởi động training. Managed continual
learning chỉ có thể bắt đầu sau
khi triển khai queue/blob event, consumer/job, retry và idempotency riêng; không
được suy ra từ việc API đã nhận feedback.
