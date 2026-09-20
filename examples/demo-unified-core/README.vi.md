# Gói demo smoke Meeting Core

Gói này kiểm tra một backend đã chạy sẵn với `backend.app.core_api:app`. Client
đọc API key từ biến môi trường hoặc file key, kiểm tra `/health`, gửi một file
`.txt`, `.vtt` hoặc `.srt`, rồi poll đúng `status_url` tương đối mà server trả
về. Mỗi lần chạy tạo report JSON đã loại transcript và bí mật.

Client không khởi động Uvicorn, tunnel, provider AI hoặc trainer. Hãy khởi
động backend bằng quy trình vận hành hiện có, sau đó chạy smoke ở terminal
riêng:

```powershell
$env:POWER_AUTOMATE_API_KEY = "<key-lay-tu-secret-store>"
& .\scripts\run_unified_core_smoke.ps1 `
  --base-url http://127.0.0.1:8011 `
  --expected-core v1-frozen `
  --meeting-file .\examples\demo-unified-core\meeting-demo.txt `
  --report-output .\examples\demo-unified-core\smoke-report.json
```

Thay biến môi trường bằng `--api-key-file D:\Secrets\meeting-core.key` nếu
quy trình secret store cấp file riêng. Giá trị key không được in ra terminal
hay ghi vào report.

Đổi `--expected-core` thành `v2-adaptive` khi backend đã được cấu hình đúng
cho V2. Client dừng nếu health báo core khác, nếu job thất bại, hoặc nếu poll
quá thời hạn. Submit không được tự động retry.

Feedback mặc định không được ghi. Muốn ghi phải truyền đồng thời file JSON và
cờ cho phép riêng; file phải có `job_id`, `core_id` khớp smoke report,
`content_hash` khớp job và approval rõ ràng:

```powershell
& .\scripts\run_unified_core_smoke.ps1 `
  --base-url http://127.0.0.1:8011 --expected-core v2-adaptive `
  --meeting-file .\examples\demo-unified-core\meeting-demo.txt `
  --report-output .\examples\demo-unified-core\smoke-report.json `
  --feedback-file .\examples\demo-unified-core\feedback-demo.json `
  --allow-feedback
```

V1 ghi feedback chỉ để audit và report đánh dấu `audit_only`; V1 không đủ
điều kiện train adaptive. V2 không có Meeting Note trong smoke này và report
đánh dấu `adaptive_training_eligible` khi health xác nhận `v2-adaptive`.

File mẫu không chứa transcript thật, API key hoặc dữ liệu cá nhân. Không đưa
report runtime vào commit nếu report chứa thông tin nội bộ.
