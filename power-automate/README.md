# Power Automate Integration

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
sau khi HTTP action chạy ổn với Azure endpoint.

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
