# Đề xuất cải tiến V1: Task Ledger Reconciliation

## Quyết định đề xuất

Giữ **V1** làm pipeline chính, tạm dừng port logic xử lý sang Power Automate và
chưa chuyển sang V2. Thay phần AI fallback/reducer hiện tại bằng cơ chế
**Task Ledger + targeted AI mutation + final reconciliation**.

## Vấn đề hiện tại

Live run gần nhất đã gọi AI thành công trên 82/86 case, nhưng chưa hoàn tất W5:

- recall tăng từ `60.57%` lên `65.45%`;
- precision giảm từ `42.94%` xuống `40.66%`;
- unexpected task tăng từ `198` lên `235`;
- riêng 12 case W4 cần 195 API calls nhưng precision chỉ đạt `26.90%`.

Nguyên nhân chính không còn là thiếu API key. Transcript dài được chia thành
nhiều local window, nhưng các window chưa dùng chung một task identity ổn định.
Do đó cùng một task có thể bị tạo nhiều lần, còn cancellation, reassignment hoặc
deadline update ở đoạn sau không link được với task ở đoạn trước.

## Phương pháp mới

```text
Transcript / Meeting Note
  -> Rule extraction theo từng window
  -> Explicit task label tạo provisional identity (không output)
  -> Task Ledger gán stable task_id
  -> AI chỉ resolve mutation/coreference mơ hồ
  -> Áp dụng event tuần tự lên cùng một ledger
  -> Final reconciliation trên ledger rút gọn
  -> Xuất các active final tasks
```

### 1. Stable Task Ledger

Mỗi task được cấp một `task_id` duy nhất và lưu canonical action, aliases,
assignees, deadline, status và event history. Các cách gọi như “bộ dữ liệu test”
và “test dataset” có thể trở thành alias của cùng một task thay vì hai task mới.

Task đã tồn tại từ trước meeting nhưng mới chỉ được nhắc bằng label cụ thể được
ghi thành internal `TASK_REFERENCE`/`PROVISIONAL`. Deadline, owner hoặc active
confirmation promote identity này; cancellation/rejection chuyển terminal;
discussion-only không xuất task. Pronoun như “task đó”, “cái đó” không đủ quyền
tạo identity.

### 2. AI chỉ xử lý mutation có mục tiêu

AI không còn là generic task extractor. Với cancellation, rejection, owner hoặc
deadline change, AI phải chọn một `related_task_id` từ danh sách candidate task
do backend cung cấp. Nếu không có target duy nhất, event được đánh dấu
`unresolved` thay vì tạo task mới hoặc chọn task gần nhất.

### 3. Reducer áp dụng latest valid state

- `OWNER_ASSIGN` bổ sung owner; `OWNER_REASSIGN` thay owner cũ.
- `DEADLINE_REPLACE` ghi đè deadline trước.
- `TASK_CANCEL` và `TASK_REJECT` là terminal state.
- Task terminal không được tự động mở lại bởi một mention tích cực về sau.
- Final output chỉ được sinh từ ledger toàn meeting, không merge trực tiếp final
  task từ các chunk.

### 4. Final reconciliation cho long/extra-long

Sau khi xử lý hết các chunk, backend reconciliation trên ledger rút gọn thay vì
gửi lại toàn transcript. Bước này hợp nhất alias/duplicate, bind unresolved
mutation khi đủ bằng chứng và giữ state cuối cùng. AI reconciliation, nếu cần,
chỉ được merge hoặc link các task ID đã tồn tại; không được tạo task mới.

Final recap chỉ là authoritative snapshot khi transcript có marker rõ như
“final list” hoặc “chỉ còn các task sau”. Recap thông thường không được dùng làm
hard whitelist để xóa task hợp lệ.

## Triển khai dự kiến

1. Tạo trace replay evaluator: legacy trace là safety-only; trace sinh sau
   mutation contract là blocking quality gate.
2. Implement Task Ledger, alias linking và terminal-state rules.
3. Đổi event dedup sang semantic key dựa trên `task_id` và anchor clause.
4. Đổi AI schema/prompt sang mutation-target contract.
5. Thêm final reconciliation và checkpoint/resume cho transcript extra-long.
6. Chạy targeted W4/W5, sau đó mới chạy full 86 và tiếp tục Power Automate.

Trạng thái ngày 2026-08-13: các bước deterministic và provisional identity đã
implement; `204/204` test pass. Smoke 4 case đã pass. Gate B đầu tiên chạy đủ
24 case nhưng fail vì precision giảm nhẹ và unexpected tăng 2, dù recall/field
tăng. Review tìm được provisional promotion quá rộng và candidate task ID bị
dịch do deterministic recap/context được thêm muộn; hai lỗi đã sửa. Local A/B
giảm 4 false positives mỗi mode, không giảm recall/field. Gate B trên identity
order mới sau đó chạy 24 case nhưng dừng vì unexpected tăng
`107 -> 108`. Hai post-provider guards đã sửa creation authority của AI
deadline và chặn deadline trên stable ID có concrete aliases xung đột. Native
replay cùng 102 paid responses đã pass blocking Gate B với
P=.3690/R=.5636/field=.8118/unexpected=106, không có contract safety error.
Vì candidate input và provider output không đổi, không cần gọi lại Gate B;
guard hiện chặn 22 deadline mutation trên 8/24 case nên cần giảm identity
conflation bằng deterministic split trước khi trả phí full live. Power Automate
chưa tiếp tục. Follow-up split nay đã hoàn tất: ledger tách identity alias khỏi
evidence alias, giữ numbered/human-note siblings riêng và reconcile candidate
memory trước ranking. Provider-free audit trên đúng Gate-B subset đạt 0
conflicting/empty/duplicate candidates qua 102 would-be requests. Do candidate
IDs đã đổi, bounded live smoke đã được chạy lại tại
`evaluation/live-v1-20260811T035647Z` và pass: 8/8 provider call thành công,
P/R/unexpected không regression, field tăng `.8485 -> .8636`, unresolved
mutation giảm `21 -> 16`, 0 contract/safety rejection. Gate B mới sau đó pass
với P `.3721 -> .3832`, recall giữ `.5818`, unexpected `108 -> 103` và
unresolved `142 -> 117`. Gate C đã chạy đủ 86 case/134 provider calls; raw run
fail nhẹ P/R nhưng blocking native replay sau ba post-provider semantic guard
đạt P=.4091/R=.5848/field=.8333/unexpected=234 và pass contract safety. Report:
`evaluation/live-v1-20260813T034446Z/native-replay-gate-c-fixes-final.json`.

## Tiêu chí nghiệm thu

- Full benchmark hoàn tất 86/86, gồm đủ 4 case W5.
- Precision không thấp hơn baseline và recall không regression đáng kể.
- Unexpected task giảm, đặc biệt ở W4/W5.
- Mutation unresolved không tạo task mới.
- Cancellation/reassignment/deadline update xuyên chunk link đúng task.
- Số event sau dedup nhỏ hơn trước dedup trong các duplicate fixtures.
- API call và token cho W4/W5 giảm rõ rệt.
- Power Automate chỉ làm trigger/upload/poll/store; logic task identity và
  reconciliation tiếp tục thuộc backend Python.

## Kết quả kỳ vọng

Phương pháp này giải quyết trực tiếp ba lỗi hiện tại: **task trùng**, **task
outdated** và **mất liên kết xuyên long context**. Đồng thời nó giữ AI ở vai trò
fallback có kiểm soát, cho phép replay/test deterministic trước khi tiêu tốn API
và tránh nhân đôi logic chưa ổn định sang Power Automate.
