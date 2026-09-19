# Operator local V2.27 (không khởi động service)

CLI này chỉ kết nối tới một V2.27 Uvicorn API đã chạy sẵn. Nó không khởi động
Uvicorn, tunnel, provider hay trainer. Transcript không được ghi vào review,
receipt hoặc thông báo lỗi; API key chỉ đọc từ file riêng hoặc biến môi trường.

## Submit và review

Chạy từ thư mục release, dùng Python của virtualenv hiện tại:

```powershell
$env:POWER_AUTOMATE_API_KEY = (Get-Content 'evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
& '.venv\Scripts\python.exe' scripts\local_v227_operator.py submit-review `
  --base-url http://127.0.0.1:8011 `
  --transcript data\fixtures\smoke_001\smoke_001.txt `
  --meeting-id smoke-001 --meeting-title 'Weekly Sync' --meeting-date 2026-07-27 `
  --poll-timeout 120 --poll-interval 1
```

Có thể dùng `--api-key-file <đường-dẫn-riêng>` thay cho biến môi trường. Base
URL bắt buộc là localhost HTTP hoặc HTTPS; timeout và thời gian poll đều bị
giới hạn. Review mặc định được ghi dưới
`evaluation/runtime/local-v227-operator/reviews/`. Mở file này, sửa toàn bộ
`corrected_final_tasks` nếu cần, rồi điền `reviewer`, `reviewed_at` có timezone
và đổi `approval` thành `true`.

## Gửi feedback sau approval

```powershell
& '.venv\Scripts\python.exe' scripts\local_v227_operator.py send-feedback `
  --base-url http://127.0.0.1:8011 `
  --review evaluation\runtime\local-v227-operator\reviews\job-....json
```

CLI từ chối trước khi gọi API nếu approval chưa là `true`, thiếu reviewer hoặc
timestamp không có timezone, hoặc task không có đúng bảy trường với `status`
`Proposed`. Receipt được ghi atomically dưới thư mục runtime và lần chạy lại
với cùng payload sẽ dùng receipt hiện có. Không chạy trainer tự động.
