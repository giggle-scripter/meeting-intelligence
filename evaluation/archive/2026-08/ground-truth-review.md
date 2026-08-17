# Ground-truth review

## Phạm vi

- Dataset: `data/validation`
- Số case: 86
- Policy: `1.0`
- Trạng thái: 86/86 case đã review
- Static audit: 86/86 case pass

## Quy tắc review

- Chỉ giữ task còn hiệu lực ở cuối cuộc họp.
- Loại task đã hoàn thành trước cuộc họp, bị hủy hoặc bị từ chối mà không có
  người nhận thay.
- Giữ task đã chốt nhưng chưa có assignee với `assignee=""`.
- Handoff dùng owner cuối cùng.
- Deadline correction dùng mốc cuối cùng của đúng task.
- `start_date` dùng ngày bắt đầu được nói rõ; nếu không có thì dùng
  `meeting_date`.
- `trước ngày/thứ D` không kèm giờ dùng `D - 1`.
- `trước HH ngày D`, `cuối ngày D`, `deadline D`, `vào D` dùng ngày `D`.
- Dependency hoặc khoảng ngày làm việc không đủ lịch đầu vào giữ
  `due_date=""` và bảo toàn `due_date_text`.
- Ngày không hợp lệ, ví dụ `29/02/2026`, không được tự cuộn sang ngày khác.

## Kiểm tra

Chạy audit:

```powershell
python scripts/audit_ground_truth.py data/validation `
  --report evaluation/ground-truth-audit.json
```

Chạy benchmark rule-only:

```powershell
python scripts/evaluate_dataset.py data/validation `
  --reviewed-only `
  --report evaluation/validation-report-reviewed-rule-only.json
```

Kết quả rule-only hiện tại:

| Metric | Kết quả |
|---|---:|
| Case pass | 15/86 |
| Task precision | 0.242 |
| Task recall | 0.361 |
| Matched-field accuracy | 0.898 |

`matched-field accuracy` chỉ đo các field của task đã match. Metric chính cần
ưu tiên cải thiện là task precision và task recall.

So với baseline sau khi audit ground truth (`10/86`, precision `0.175`, recall
`0.289`), pipeline đã bổ sung:

- parser cho final recap dạng bullet và nhãn `Task A/B/...`;
- context ledger để nối câu hỏi về work item với cam kết ở lượt sau;
- giới hạn liên kết deadline để ngày của task sau không rò sang task trước.

Rule-only vẫn chưa phải kết quả sản phẩm cuối. Các case còn lại cần AI fallback
cho handoff dài, owner dùng lẫn role/tên, recap tự do và correction cách xa task.
