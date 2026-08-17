# Báo Cáo Demo Meeting Intelligence

## 1. Mục Đích

PoC nhận transcript cuộc họp, trích xuất các đầu việc còn hiệu lực, người phụ
trách, ngày bắt đầu, deadline, evidence và summary. Power Automate ghi kết quả
vào Microsoft Lists để người dùng kiểm tra các đề xuất task.

Input hỗ trợ `.txt`, `.vtt` và `.srt`. Pipeline xử lý tiếng Việt và tiếng Anh
trong cùng transcript.

## 2. Luồng Đang Dùng

```text
Upload transcript vào thư mục trigger
  -> Power Automate: Get file content
  -> POST /api/v1/meetings/jobs/process-file
  -> FastAPI trả 202 + job_id ngay
  -> Power Automate poll GET /api/v1/meetings/jobs/{job_id}
  -> succeeded: Parse JSON
  -> Create MI Meetings item
  -> Apply to each tasks: Create MI Task Proposals item
```

Backend chạy local bằng Uvicorn. Cloudflare Tunnel chỉ công khai local API cho
Power Automate gọi trong PoC.

```text
Power Automate -> HTTPS Tunnel -> http://127.0.0.1:8010 -> FastAPI -> OpenAI
```

Job/poll là luồng bắt buộc cho transcript trung bình hoặc dài. Power Automate
không giữ một HTTP request mở trong khi OpenAI xử lý; backend xử lý tiếp sau khi
đã trả `job_id`.

## 3. Backend Pipeline

```text
Transcript
  -> caption / turn / sentence / clause
  -> normalize speaker, remove duplicate caption, find date mentions
  -> candidate windows
  -> deterministic rule events
  -> selective OpenAI fallback for ambiguous windows
  -> deduplicate, link events, reduce final task state
  -> resolve dates, evidence, summary, diagnostics
```

Reducer áp dụng event theo thứ tự hội thoại. Đây là bước quyết định task cuối
cùng khi có cập nhật deadline, đổi owner, hủy task hoặc recap.

## 4. Rule-Only Và Hybrid

| Chế độ | Cách hoạt động | Mục đích |
|---|---|---|
| Rule-only | Không cấu hình OpenAI; pipeline chỉ dùng parser, date resolver và rule. | Baseline deterministic, không tốn API credit. |
| Hybrid | Rule luôn chạy trước. Chỉ candidate window mơ hồ mới gọi OpenAI. | Chạy demo và đánh giá khả năng xử lý ngữ nghĩa. |

Rule-only **không phải** luồng production đầy đủ. Nó cho biết phần nào có thể
xử lý ổn định mà không cần AI. Hybrid không gửi toàn bộ transcript cho model;
nó chỉ gửi các window được đánh dấu cần AI cùng context giới hạn.

Ví dụ chạy baseline rule-only:

```powershell
.\scripts\run_validation_batches.ps1 `
  -Batch w1 `
  -RuleOnly `
  -Report "evaluation\validation-w1-rule-only.json"
```

Ví dụ chạy hybrid qua job API:

```powershell
.\scripts\run_validation_batches.ps1 `
  -Batch w3 `
  -JobApi `
  -TimeoutSeconds 3600 `
  -PollIntervalSeconds 15 `
  -Report "evaluation\validation-w3-job-hybrid.json"
```

Các lệnh evaluation chỉ gọi local FastAPI và ghi JSON report trong `evaluation/`;
chúng không trigger Power Automate hoặc ghi Microsoft Lists.

## 5. API Và Cấu Hình Demo

| Endpoint | Dùng cho |
|---|---|
| `GET /health` | Kiểm tra Uvicorn còn hoạt động. |
| `POST /api/v1/meetings/process` | Debug JSON transcript ngắn tại local. |
| `POST /api/v1/meetings/jobs/process-file` | Submit file cho flow upload. |
| `GET /api/v1/meetings/jobs/{job_id}` | Poll trạng thái `queued`, `running`, `succeeded`, `failed`. |

Môi trường demo cần các biến sau trong terminal Uvicorn:

```powershell
$env:OPENAI_API_KEY = "<secret>"
$env:OPENAI_MODEL = "gpt-5-mini"
$env:OPENAI_REASONING_EFFORT = "medium"
$env:POWER_AUTOMATE_API_KEY = "mi-demo-secret"
uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
```

Khởi động tunnel ở terminal khác:

```powershell
& "$env:LOCALAPPDATA\cloudflared\cloudflared.exe" tunnel `
  --protocol http2 `
  --url http://127.0.0.1:8010
```

Mỗi lần dừng và tạo Quick Tunnel mới, URL `trycloudflare.com` thay đổi. Cập nhật
URL mới vào action `Submit job` của Power Automate. Không đưa OpenAI key vào flow;
flow chỉ gửi `X-API-Key` của backend.

## 6. Case Dùng Để Demo

| Thứ tự | Case | Mục tiêu | Kỳ vọng |
|---:|---|---|---|
| 1 | `W1-SHORT-C1-N0-IT-DASG-ABS-010` | Baseline direct assignment. | Một task rõ owner và ngày tuyệt đối. |
| 2 | `W2-SHORT-C2-N0-OPS-DREP-012` | Deadline replacement. | Chỉ giữ deadline release note cuối cùng. |
| 3 | `W2-SHORT-C2-N0-IT-BRST-006` | Brainstorm. | Summary có chủ đề CRM-ERP, không tạo task. |
| 4 | `W3-MED-C3-N1-OPS-INT-020` | Feature interaction. | Tám task đúng owner/date; có recap, deadline replacement và task không deadline. |
| 5 | `W4-LONG-C4-N1-IT-STATE-006` | Long-distance state. | Sáu task final sau handoff, cancellation và cập nhật state. |

Các file upload nằm trong `data/power_automate_uploads/` và có tên đúng bằng
`case_id`. Upload bản copy có tên mới nếu cần trigger flow lại.

`W3-MED-C3-N1-OPS-INT-020` từng bị evaluator đánh dấu fail chỉ do
`due_date_text` khác wording (`hạn thứ Năm` thay vì `hạn chót là thứ Năm tuần này`).
Task identity, owner, start date và due date đều khớp, nên dùng được cho demo
nghiệp vụ. `W4-LONG-C4-N1-IT-STATE-006` đã được smoke test thủ công qua flow.

## 7. Kế Hoạch Test Tiếp Theo

1. Chạy lại W1-W3 bằng cùng build và job API để có baseline hybrid thống nhất.
2. Chạy đủ 12 case W4 và 4 case W5 qua job API; không dùng `/process` đồng bộ.
3. Phân loại lỗi theo nguồn `RULE`, `AI`, `RECAP` và lỗi date linking.
4. Với transcript dài, triển khai segment extraction song song có overlap nhỏ;
   merge/reducer vẫn chạy tuần tự theo global clause order.
5. Chỉ chuyển sang pipeline mới khi regression W1-W5 và smoke Power Automate
   cùng đạt tiêu chí đã chốt.

Metric theo dõi: case pass rate, task precision, task recall, field accuracy,
task thừa/thiếu, AI provider error, unresolved window, AI call rate và thời gian
job. Không gộp metric từ report khác build, model hoặc mode để kết luận accuracy.

## 8. Giới Hạn PoC

- Job store đang in-memory; restart Uvicorn làm mất trạng thái job đang chạy.
- Quick Tunnel không có uptime guarantee và có thể reconnect ngắn.
- OpenAI fallback có chi phí, timeout và rate limit; rule-only là baseline miễn phí.
- W4/W5 hiện là stress corpus để cải tiến pipeline, không phải toàn bộ đều là
  happy-path demo.
- Microsoft Lists lưu task ở trạng thái `Proposed`; bước duyệt/giao việc chính
  thức là workflow nghiệp vụ tiếp theo.

## 9. Checklist Trước Khi Demo

1. Uvicorn trả `status: ok` tại `http://127.0.0.1:8010/health`.
2. Tunnel đang chạy và Power Automate dùng đúng URL hiện tại.
3. OpenAI API key đã cấu hình ở terminal Uvicorn.
4. Xóa dữ liệu demo cũ khỏi `MI Meetings` và `MI Task Proposals`.
5. Upload lần lượt case 1 đến 4; case 5 chạy cuối hoặc chuẩn bị sẵn kết quả.
6. Kiểm tra một meeting item và các task có cùng `MeetingItemId`.
