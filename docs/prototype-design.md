# Thiết kế prototype

## Thành phần

| Thành phần | Tên |
| --- | --- |
| Power Automate solution | `Meeting Intelligence Feasibility` |
| Cloud flow | `MI - Analyze Transcript - Prototype` |
| AI Builder prompt | `MI Extract Meeting Tasks v1` |
| Meeting list | `MI Meetings` |
| Task list | `MI Task Proposals` |

## Luồng xử lý

```text
Manually trigger a flow
  -> Run meeting prompt
  -> Create meeting record
  -> Apply to each task
     -> Create task proposal
```

Trigger nhận ba input text: `TestCaseId`, `MeetingTitle` và `Transcript`.
Prompt trả structured output nên flow sử dụng dynamic content trực tiếp, không
cần action `Parse JSON`.

## Output contract

Output gồm:

- `meeting_title`
- `summary`
- `tasks[]`: `task_name`, `assignee`, `start_date`, `due_date`,
  `due_date_text`, `evidence`, `status`

Schema: [`prompt-output.schema.json`](../power-automate/schemas/prompt-output.schema.json)

Sample: [`prompt-output.sample.json`](../power-automate/schemas/prompt-output.sample.json)

## MI Meetings

| Cột | Kiểu | Nguồn |
| --- | --- | --- |
| `Title` | Single line of text | `meeting_title` |
| `TestCaseId` | Single line of text | Trigger `TestCaseId` |
| `Summary` | Multiple lines of text | `summary` |
| `ProcessingStatus` | Choice | `Success` |
| `ProcessedAt` | Date and time | `utcNow()` |

Microsoft Lists tự tạo cột `ID`. Flow dùng ID của meeting vừa tạo để liên kết
các task.

## MI Task Proposals

| Cột | Kiểu | Nguồn |
| --- | --- | --- |
| `Title` | Single line of text | `item()?['task_name']` |
| `MeetingItemId` | Number | ID từ `Create meeting record` |
| `AssigneeText` | Single line of text | `item()?['assignee']` |
| `StartDate` | Date only | `start_date` hoặc `null` |
| `DueDate` | Date only | `due_date` hoặc `null` |
| `DueDateText` | Single line of text | `item()?['due_date_text']` |
| `Evidence` | Multiple lines of text | `item()?['evidence']` |
| `Status` | Choice | `Proposed` |

Expression xử lý ngày trống:

```text
if(empty(item()?['start_date']), null, item()?['start_date'])
if(empty(item()?['due_date']), null, item()?['due_date'])
```

## Bằng chứng

| Nội dung | File |
| --- | --- |
| Flow design | [`flow-design.png`](assets/flow-design.png) |
| Ba flow run thành công | [`flow-runs.png`](assets/flow-runs.png) |
| Meeting records | [`meetings.png`](assets/meetings.png) |
| Task proposal records | [`task-proposals.png`](assets/task-proposals.png) |
| Kết quả end-to-end | [`evaluation/results.csv`](../evaluation/results.csv) |
