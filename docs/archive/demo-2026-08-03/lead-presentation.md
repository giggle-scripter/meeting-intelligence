# Meeting Intelligence PoC - Báo Cáo Trình Bày

## Mục Tiêu

Xác minh PoC có thể nhận transcript cuộc họp, trả về:

1. Summary có chủ đề cuộc họp và các quyết định chính.
2. Danh sách task proposal gồm task, assignee, start date, due date, deadline text
   và evidence.
3. Luồng tự động ghi meeting/task vào Microsoft Lists qua Power Automate.

Phạm vi hiện tại là transcript có sẵn. Không bao gồm record cuộc họp, UI sản phẩm
hoàn chỉnh hoặc bước giao task chính thức.

## Những Phần Đã Hoàn Thành

```text
Transcript file upload
  -> Power Automate trigger
  -> FastAPI local qua Cloudflare Tunnel
  -> Rule-first extraction + selective OpenAI fallback
  -> async job submit/poll
  -> MI Meetings + MI Task Proposals (Microsoft Lists)
```

- Backend FastAPI xử lý `.txt`, `.vtt`, `.srt`; hỗ trợ tiếng Việt và tiếng Anh.
- Power Automate không cần copy/paste transcript: upload file vào thư mục trigger.
- Với transcript dài, flow nhận `job_id` và poll kết quả, tránh timeout HTTP đồng bộ.
- Task được lưu ở trạng thái `Proposed`, có evidence để người dùng kiểm tra.
- Đã có 81 automated backend tests pass.

## Demo Đề Xuất

| Case | Mục đích demo | Kết quả cần quan sát |
|---|---|---|
| `W1-SHORT-C1-N0-IT-DASG-ABS-010` | Giao việc trực tiếp, ngày tuyệt đối. | Một task đúng owner và deadline. |
| `W2-SHORT-C2-N0-OPS-DREP-012` | Thay deadline. | Chỉ giữ deadline cuối cùng. |
| `W2-SHORT-C2-N0-IT-BRST-006` | Brainstorm. | Có summary chủ đề, không tạo task. |
| `W3-MED-C3-N1-OPS-INT-020` | Nhiều feature cùng lúc. | Task/owner/date đúng; recap không tạo duplicate. |
| `W4-LONG-C4-N1-IT-STATE-006` | Context dài, đổi state xa nhau. | Sáu task final sau handoff/cancellation. |

File demo ở `data/power_automate_uploads/`. Demo theo thứ tự W1 -> W2 -> W3 ->
W4; W4 chạy cuối vì tốn thời gian và API credit hơn.

## Kết Quả Đã Xác Nhận

- W1/W2 có các case pass cho assignment, absolute date, deadline replacement,
  brainstorm không action item và cancellation.
- `W3-MED-C3-N1-OPS-INT-020` khớp 8/8 task, owner, start date và due date.
  Evaluator cũ chỉ khác wording `due_date_text`, không khác deadline nghiệp vụ.
- `W4-LONG-C4-N1-IT-STATE-006` đã smoke test qua Power Automate, trả 6 task
  final đúng sau các thay đổi owner/deadline/task state.
- Uvicorn + Cloudflare Tunnel + Power Automate + Microsoft Lists đã chạy end-to-end.

