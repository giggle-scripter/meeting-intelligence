# Meeting Note policy

## Mục đích

Meeting Note là context bổ sung cho transcript. Note có thể giúp xác định chủ đề,
liên kết tham chiếu ngắn và khôi phục một action được ghi rõ, nhưng không thay
thế transcript một cách mặc định.

## Nguồn và mức tin cậy

| Source | Vai trò |
| --- | --- |
| `SECRETARY`, `PARTICIPANT`, `MANUAL` | Human note; assertion tích cực, cụ thể có thể là trusted additive evidence. |
| `AUTO_OVERVIEW` | Context-only; không bao giờ tự tạo task hay evidence. |

Human note chỉ tạo `HUMAN_NOTE` event khi nêu rõ action cụ thể và đủ owner hoặc
cam kết. Evidence output phải ghi `Meeting Note` cùng author khi event này là
nguồn duy nhất. Question, suggestion, uncertainty, recap mơ hồ và text không có
action đều không tạo task.

## Xử lý xung đột

Transcript vẫn là nguồn diễn biến cuộc họp. Correction, reassignment, rejection
hoặc cancellation rõ ràng trong transcript sau đó được reducer áp dụng lên task
tạo từ `HUMAN_NOTE`. Note chỉ dùng làm cue cho AI; AI phải có clause transcript
hỗ trợ cho mọi event mà nó phát ra.

## Định dạng

JSON API truyền note trong field `meeting_note`. Preferred file upload dùng
self-contained package gồm `=== MEETING METADATA ===`, optional
`=== MEETING NOTE ===` và `=== TRANSCRIPT ===`; dùng
`scripts/prepare_power_automate_uploads.py` để sinh package thay vì sửa fixture
thủ công.
