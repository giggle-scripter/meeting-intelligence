# Chạy thử phương pháp V2.27 trên transcript mới

Đây là lệnh **thử nghiệm opt-in trên CPU**. Backend mặc định vẫn chạy V1;
muốn thử V2.27 qua endpoint Power Automate, dùng
[ASGI app riêng](run-v227-backend-powershell.md). Lệnh CLI này chạy V1 với AI tắt để tạo trace mới,
lấy tập ứng viên `final_plus_bridge_plus_intermediate`, rồi áp mô hình full-fit
và policy đã đóng băng trong package private V2.28. Các bản ghi shadow của
action-candidate và commitment-router chỉ phục vụ tạo candidate; chúng không
thay output V1 và không gọi provider.

Package private cần đủ `model.json`, `frozen-policy.json`, `manifest.json` tại
`evaluation/runtime/experimental-distillation-v2/v228-frozen-promotion/`.
Runner kiểm SHA-256 model và policy theo manifest trước khi xử lý. Nếu thiếu
hoặc hash sai, lệnh dừng. Có thể dùng `--artifact-directory` trỏ đến bản sao
private. Không commit model, transcript, nhãn hoặc manifest chứa runtime data.

Từ root dự án:

```powershell
.\.venv\Scripts\python.exe scripts\experimental_distillation\run_v227_experimental.py `
  .\new-meeting.vtt `
  --meeting-date 2026-09-18 `
  --meeting-title 'Weekly delivery review' `
  --output .\backend\outputs\new-meeting-v227.json
```

Nhận `.txt`, `.vtt`, `.srt`. Nên cung cấp `--meeting-date`. Nếu bỏ qua, lệnh chỉ
nhận ngày ISO hoặc ngày/tháng/năm rõ ràng trên dòng ngữ cảnh cuộc họp; không
có thì dừng. Lệnh không dùng case ID, nhãn validation, expected output hoặc
ngày fallback cố định. JSON trả các trường meeting/task thông thường và phần
`experimental` gồm hash artifact/trace, số candidate và số task được chọn,
policy, `provider_call_count: 0` và cảnh báo chất lượng.

**Giới hạn quan trọng:** V2.27 DEV42 OOF F1 0.57049 là bằng chứng development.
Mô hình full-fit từ V2.28 đã trượt gate diagnostic. Vì vậy output ghi
`experimental_not_validated: true`; không dùng để thay task của V1 hay tự động
ghi Power Automate. V3.1 chỉ là comparator trên DEV42 + DeepSeek DEV13, chưa
có runner inference được chốt. Xem
[tài liệu bản phát hành](phat-hanh-v1-va-distillation.md).
