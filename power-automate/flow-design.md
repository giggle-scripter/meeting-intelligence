# Thiết kế Power Automate flow

## Mục tiêu

Nhận transcript thủ công, chạy prompt AI Builder và lưu meeting cùng task đề
xuất vào Microsoft Lists.

## Thành phần

- Solution: `Meeting Intelligence Feasibility`
- Flow: `MI - Analyze Transcript - Prototype`
- Prompt: `MI Extract Meeting Tasks v1`
- Data store: `MI Meetings`, `MI Task Proposals`

## Luồng xử lý

```text
Manually trigger a flow
  -> Run meeting prompt
  -> Create meeting record
  -> Apply to each task
     -> Create task proposal
```

Prompt sử dụng JSON output có cấu trúc, vì vậy flow đọc trực tiếp các trường
dynamic content và không cần action `Parse JSON`.

## Trigger inputs

| Input | Kiểu | Mục đích |
| --- | --- | --- |
| `TestCaseId` | Text | Định danh lần test |
| `MeetingTitle` | Text | Tiêu đề đưa vào prompt |
| `Transcript` | Text | Nội dung transcript |

`TestCaseId` là metadata của flow, không được gửi vào prompt.

## Mapping `MI Meetings`

| Cột | Nguồn |
| --- | --- |
| `Title` | `meeting_title` |
| `TestCaseId` | Trigger `TestCaseId` |
| `Summary` | `summary` |
| `ProcessingStatus` | Giá trị cố định `Success` |
| `ProcessedAt` | `utcNow()` |

ID của item vừa tạo được dùng làm `MeetingItemId` cho các task.

## Mapping `MI Task Proposals`

| Cột | Nguồn |
| --- | --- |
| `Title` | `item()?['task_name']` |
| `MeetingItemId` | ID từ `Create meeting record` |
| `AssigneeText` | `item()?['assignee']` |
| `StartDate` | Ngày bắt đầu hoặc `null` |
| `DueDate` | Deadline cụ thể hoặc `null` |
| `DueDateText` | `item()?['due_date_text']` |
| `Evidence` | `item()?['evidence']` |
| `Status` | Giá trị cố định `Proposed` |

Expression cho `StartDate`:

```text
if(empty(item()?['start_date']), null, item()?['start_date'])
```

Expression cho `DueDate`:

```text
if(empty(item()?['due_date']), null, item()?['due_date'])
```

## Smoke test

Test case `meeting_01` đã chạy thành công:

- Flow status: `Succeeded`
- Tạo 1 item trong `MI Meetings`
- Tạo 2 item trong `MI Task Proposals`
- Hai task dùng đúng ID của meeting vừa tạo
- Deadline tương đối được lưu trong `DueDateText`

## Giới hạn

- Data store hiện nằm trong `My lists` và chỉ phù hợp cho prototype cá nhân.
- Flow chưa chống tạo dữ liệu trùng khi chạy lại cùng `TestCaseId`.
- Assignee được lưu dạng text, chưa ánh xạ Microsoft 365 user.
- Chưa có retry, error branch, approval hoặc production monitoring.
- Personal site URL và connection credentials không được ghi vào repository.
