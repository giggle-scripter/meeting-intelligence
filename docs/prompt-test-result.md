# Kết quả đánh giá prompt AI Builder

## Mục tiêu

Đánh giá khả năng của prompt `MI Extract Meeting Tasks v1` trong việc:

- Tóm tắt transcript cuộc họp tiếng Việt.
- Trích xuất task dưới dạng JSON có cấu trúc.
- Không tự tạo task, assignee hoặc deadline ngoài transcript.

## Dữ liệu kiểm thử

| Test case | Tình huống | Số task kỳ vọng |
|---|---|---:|
| `meeting_01_clear` | Task, assignee và deadline tương đối được nói rõ | 2 |
| `meeting_02_missing_fields` | Có task nhưng chưa có deadline | 1 |
| `meeting_03_ambiguous` | Chỉ thảo luận, không có action item rõ ràng | 0 |

Transcript nằm trong `evaluation/transcripts/`. Output của prompt v1 nằm trong
`evaluation/results/`.

## Quy tắc đánh giá

Một task được tính là đúng khi:

- Hành động khớp với nội dung kỳ vọng.
- Assignee có căn cứ trong transcript.
- Evidence hỗ trợ trực tiếp cho task.
- Field không xác định được giữ trống.

Các câu thảo luận chung không được chuyển thành task.

## Kết quả

| Test case | Kỳ vọng | Thực tế | TP | FP | FN | Schema | Task bịa |
|---|---:|---:|---:|---:|---:|---|---:|
| `meeting_01_clear` | 2 | 2 | 2 | 0 | 0 | Pass | 0 |
| `meeting_02_missing_fields` | 1 | 1 | 1 | 0 | 0 | Pass | 0 |
| `meeting_03_ambiguous` | 0 | 0 | 0 | 0 | 0 | Pass | 0 |
| **Tổng** | **3** | **3** | **3** | **0** | **0** | **3/3** | **0** |

## Metrics

| Metric | Kết quả |
|---|---:|
| Task precision | 100% |
| Task recall | 100% |
| Task F1 | 100% |
| Schema validity | 100% (3/3) |
| Assignee accuracy | 100% |
| Missing-field accuracy | 100% |
| Evidence grounding | 100% |
| Task hallucination rate | 0% |

Deadline tương đối `trước thứ Sáu` được giữ trong `due_date_text`. Prompt không
tự chuyển thành ngày cụ thể khi không có ngày họp. Case mơ hồ trả về mảng
`tasks` rỗng như kỳ vọng.

## Kết luận

Prompt v1 đạt yêu cầu feasibility trên ba transcript mẫu. Kết quả đủ để tiếp
tục thử nghiệm Option A bằng Power Automate và Microsoft Lists.

## Giới hạn

- Dataset chỉ có ba transcript giả lập đã anonymize.
- Kết quả chưa đại diện cho độ chính xác production.
- Chưa kiểm thử transcript dài, câu nói trùng, assignee mơ hồ hoặc giao việc
  mâu thuẫn.
- Chưa đánh giá AI Builder capacity, chi phí và độ ổn định của flow.

## Bước tiếp theo

Kiểm thử end-to-end với cùng ba transcript:

~~~text
Manual trigger
-> Run a prompt
-> Tạo meeting record
-> Tạo các proposed task record
~~~
