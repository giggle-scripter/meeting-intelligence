# SP365 Integration Contract

## MI Meetings

| Column | Source |
| --- | --- |
| Title | `meeting_title` |
| MeetingId | request `meeting_id`; bật **Enforce unique values** |
| MeetingDate | request `meeting_date` |
| Summary | response `summary` |
| ProcessingStatus | `Writing`, `Success` hoặc `Failed` |
| UnresolvedWindowCount | `length(unresolved_window_ids)` |
| NeedsReview | `true` khi còn unresolved window hoặc có task proposal |
| ProcessedAt | Power Automate `utcNow()` |

## MI Task Proposals

| Column | Source |
| --- | --- |
| ProposalKey | `<MeetingId>|<TaskSequence>`; bật **Enforce unique values** |
| Meeting | SharePoint ID của meeting item vừa tạo |
| MeetingIdText | request `meeting_id`, dùng để filter/upsert |
| TaskSequence | biến đếm trong `Apply to each` |
| Title | `task_name` |
| AssigneeText | `assignee` |
| StartDate | `start_date` |
| DueDate | `due_date`, để trống nếu `""` |
| DueDateText | `due_date_text` |
| Evidence | `evidence` |
| Status | `Proposed` |

`Meeting` là lookup và phải nhận numeric SharePoint item ID, không nhận business
ID dạng `meeting_001`.

Flow submit file tới `/api/v1/meetings/jobs/process-file`, lưu `job_id` và poll
URL từ `status_url`. Chỉ map `summary` và task từ `result` khi job `succeeded`.
Sau success, flow upsert Meeting theo `MeetingId`, upsert từng task theo
`ProposalKey`, rồi mới đặt Meeting/source file `Success`. Khi `failed`, lưu error
job và không tạo task proposal.
