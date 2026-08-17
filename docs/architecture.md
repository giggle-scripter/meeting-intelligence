# Architecture

## Nguyên tắc

- Raw transcript không bị sửa. Evidence mặc định lấy từ clause transcript;
  positive Meeting Note do người viết có thể là trusted additive evidence và
  được ghi rõ nguồn Meeting Note.
- Python chịu trách nhiệm cho parsing, normalization, date arithmetic, linking,
  state transition và output validation.
- AI không đọc toàn transcript; AI chỉ nhận candidate window mơ hồ.
- AI không tính `due_date`, không viết evidence và không quyết định final state.
- Mọi stage dùng ID ổn định để trace ngược về caption nguồn.

## Pipeline

```text
MeetingInput
  -> Embedded/sidecar Meeting Note extraction
  -> Parser selector (VTT/SRT/TXT)
  -> Caption update deduplicator
  -> Speaker normalizer
  -> Turn builder
  -> Sentence splitter
  -> Clause splitter
  -> Cue and date annotation
  -> Meeting-context compaction (topic, note cue, digest)
  -> Candidate windows
       -> clear: rule event extraction
       -> ambiguous: constrained AI event extraction
  -> Final recap snapshot extraction
  -> Event deduplication
  -> Task linker and state machine
  -> Date resolver
  -> Evidence and template summary
  -> PipelineResult
```

## AI boundary

AI input chỉ gồm:

- clause ID, speaker và raw text trong window;
- rule flags;
- date mention ID và semantic relation.

AI output chỉ được chứa task event enum, action, assignee, source clause IDs,
date mention ID và confidence. Nếu AI endpoint chưa cấu hình, window mơ hồ được
đưa vào `unresolved_window_ids` thay vì đoán.

Meeting Note được compact thành cue để AI định vị và giải coreference, không
được tự động trở thành output AI. Với assertion tích cực, cụ thể từ nguồn human,
rule extractor có thể phát `HUMAN_NOTE`; reducer vẫn áp dụng các correction hoặc
cancel rõ ràng trong transcript sau event đó. Question, uncertainty và
`AUTO_OVERVIEW` chỉ là context.

## Task state

Reducer xử lý event theo `order_index`:

```text
UNKNOWN -> PROPOSED -> CONFIRMED -> REASSIGNED
                                   -> CANCELLED
                                   -> REJECTED
```

Event correction sau ghi đè owner/deadline trước của đúng task. Task cancelled
hoặc rejected vẫn tồn tại trong state để audit nhưng không xuất hiện trong
final tasks.

V1 không có implicit reopen: một positive event lặp lại không được hồi sinh task
`CANCELLED` hoặc `REJECTED`. Reopen chỉ nên được thêm bằng operation riêng khi
có bằng chứng transcript rõ.

AI mutation event dùng `anchor_clause_id` để giữ đúng chronology. Với reference
ở xa, provider nhận tối đa 5 active-task candidates được reduce từ những event
trước anchor; danh sách này chỉ hỗ trợ target resolution và không được dùng làm
bằng chứng tạo task.

Nếu transcript có recap cuối dạng danh sách owner hoặc `Task 1`, `Task 2`, recap
được coi là snapshot authoritative. Snapshot đã phản ánh rename, handoff,
deadline replacement và cancellation nên các event cũ không được phát lại lên
trạng thái cuối. Câu thiếu object như `làm theo` hoặc `hoàn thành` không tạo task
độc lập khi đã có snapshot.

## Date semantics

- `trước/before D` không có giờ: due = D - 1 ngày.
- `trước/before 18h D` và `trước cuối ngày D`: due = D.
- `vào/on/by/chậm nhất D`: due = D.
- Ngày/tháng thiếu năm dùng năm meeting; nếu đã qua thì rollover năm sau.
- Calendar duration cộng từ start date.
- Working-day duration và event dependency để trống cho đến khi có calendar hoặc
  dependency anchor đáng tin cậy.

## Deployment

`backend/function_app.py` giữ Azure Functions ASGI entry point. Có thể chạy cùng
FastAPI app trên Azure Functions hoặc App Service. Power Automate gọi endpoint
HTTPS bằng `X-API-Key`.
