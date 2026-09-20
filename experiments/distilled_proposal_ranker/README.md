# Bộ xếp hạng đề xuất task bằng distillation V1

Package này chỉ dành cho thử nghiệm offline. Không import vào pipeline
production, API, ledger hoặc serializer. Protocol khóa tại
`config/protocol-v1.json`; runtime data nằm trong
`evaluation/runtime/experimental-distillation-v1`, model artifact trong
`artifacts/models/experimental-distillation-v1`. Các đường dẫn này không
được commit dữ liệu private.

Thử nghiệm dùng nested cross-validation theo nhóm meeting. Nhãn human ở
outer-validation fold chỉ để đánh giá, không được train hoặc chọn ngưỡng.
Teacher mặc định `cache-only`, nên không gọi mạng. Nhánh teacher này đã dừng
vì exact-span recall ceiling 0.274 thấp hơn gate 0.75; không có teacher-label
integration hoàn chỉnh hay student production. Mốc nghiên cứu được giữ cho
release là V2.27 và V3.1, mô tả tại
[tài liệu phát hành](../../docs/phat-hanh-v1-va-distillation.md).
