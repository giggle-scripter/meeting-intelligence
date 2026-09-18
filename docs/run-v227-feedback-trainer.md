# Huấn luyện challenger V2.27 từ feedback đã duyệt

`train_v227_feedback.py` là CLI nghiên cứu chạy offline trên CPU. Lệnh dành
cho đúng một tenant đã được cấu hình ở server; nó đọc các bản ghi append-only
trong `evaluation/runtime/v227-feedback/<tenant>/`, kiểm tra lại SHA-256 của
source và feedback, rồi chỉ dùng feedback có đủ `reviewer`,
`reviewed_at` (ISO-8601 có timezone) và `approval: true`.

```powershell
& 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe' `
  scripts\experimental_distillation\train_v227_feedback.py `
  --tenant-id acme `
  --feedback-directory evaluation\runtime\v227-feedback `
  --base-artifact-directory evaluation\runtime\experimental-distillation-v2\v228-frozen-promotion
```

Có thể chỉ định `--output` để chọn thư mục challenger mới. Nếu bỏ qua, CLI
tạo thư mục bất biến theo digest đầu vào dưới
`<tenant>/challengers/<digest>/`. Package được dựng trong thư mục tạm cùng
filesystem rồi rename nguyên thư mục một lần, nên không có output dở dang.
Một package đã tồn tại được kiểm tra manifest, status và SHA-256 của mọi file;
nếu cùng input thì lệnh trả lại package (idempotent), còn khác input thì dừng.
Không đặt output bên trong package base. Base manifest phải đúng SHA-256 V2.28
đã pin: `7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3`.

API ghi `raw_upload_sha256` là SHA-256 của bytes upload trước khi giải mã
package hoặc UTF-16. `content_hash` được tính từ digest này cùng metadata của
meeting; trainer dùng công thức đó để kiểm tra identity và kiểm tra riêng
`transcript_sha256` trên transcript UTF-8 đã giải mã. Vì vậy upload
UTF-16/package vẫn giữ đúng identity nguồn.

Với mỗi meeting, CLI dựng lại candidate union từ transcript bằng
`DisabledAiClient` và trace V1 shadow; không đọc expected output, nhãn gold,
provider hay dịch vụ mạng. Nếu source không có transcript hợp lệ hoặc không
thể dựng lại feature đúng với đường chạy inference, lệnh dừng
`STOP_SAFE_FEATURE_RECONSTRUCTION_UNAVAILABLE`. Trace tổng hợp trong test có
thể được đặt ở trường `trace`, nhưng không được chứa `expected`, `gold`,
`labels`, `oracle` hay `validation`.

Task được gán nhãn dương một cách bảo thủ: cặp tên task và assignee sau chuẩn
hóa phải trùng tuyệt đối. API chỉ nhận đúng bảy trường task và không có
`task_key`. Task human không tìm thấy trong candidate được ghi ở
`coverage.json` để review, không bị biến thành feature hoặc candidate giả.

Artifact base được mở và hash lại nhưng không sửa. Challenger bắt đầu từ
weights full-fit frozen rồi replay tất cả meeting đã duyệt bằng cập nhật CPU
deterministic. CLI ghi `model.json`, policy, `source-hashes.json`,
`coverage.json`, `holdout-diagnostics.json`, `manifest.json` và `status.json`.
Manifest luôn ghi `auto_promotion: false`, `active_model_mutated: false`,
`provider_call_count: 0`, `network_calls: 0`, `gpu_used: false`. Challenger
không được API sử dụng tự động; việc promotion cần một quy trình review mới.

Sau review, activation/rollback dùng CLI riêng trong
[`run-v227-feedback-activation.md`](run-v227-feedback-activation.md). API chỉ
đọc `V227_FEEDBACK_TENANT_ID`; pointer chỉ được đọc khi restart.

Kiểm tra bằng test tổng hợp:

```powershell
& 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe' -m pytest -q tests\test_v227_feedback_trainer.py
```
