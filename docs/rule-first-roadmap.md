# Rule-first roadmap

Pipeline mặc định là V1 rule-first. Rule xử lý parsing, dates, linking, state,
evidence và final output; AI chỉ trích xuất event trong candidate window mơ hồ.
Mọi thay đổi phải giữ deterministic path hoạt động khi không có provider AI.

## Chất lượng và vận hành

- Chạy `scripts/evaluate_dataset.py data/validation` cho regression corpus chuẩn.
- Chạy A/B có và không Meeting Note khi thay đổi context hoặc rule note.
- Khi đánh giá V2, dùng `--pipeline-version v2`; evaluator áp dụng quality gate
  precision tối thiểu 0.90 và recall tối thiểu 0.85 (có thể override bằng flags).
- Giữ report canonical trong `evaluation/`; chuyển thử nghiệm và report theo đợt
  vào `evaluation/archive/<YYYY-MM>/`.

## Hướng phát triển

V2 hiện còn cung cấp context compaction và orchestration cho V1. Chỉ tách hoặc
xóa V2 sau khi chuyển context sang module ổn định, chuyển idempotency helper,
loại pipeline version `v2|shadow`, cập nhật test/tài liệu và xác nhận deployment
không còn phụ thuộc vào các module này.
