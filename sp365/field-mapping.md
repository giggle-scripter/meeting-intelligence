# Mapping trường dữ liệu Microsoft Lists

## Phạm vi

Prototype sử dụng hai Microsoft Lists trong `My lists`:

- `MI Meetings`: lưu kết quả tổng hợp của từng cuộc họp.
- `MI Task Proposals`: lưu các task do AI đề xuất.

Hai list này chỉ phục vụ feasibility test. Khi triển khai cho nhiều người dùng,
cần chuyển chúng sang SharePoint site của dự án và cập nhật connection của flow.

## MI Meetings

| Nguồn | Cột đích | Kiểu dữ liệu | Quy tắc |
| --- | --- | --- | --- |
| `meeting_title` | `Title` | Single line of text | Bắt buộc |
| Trigger `TestCaseId` | `TestCaseId` | Single line of text | Mã test, ví dụ `meeting_01` |
| `summary` | `Summary` | Multiple lines of text | Plain text |
| Giá trị cố định | `ProcessingStatus` | Choice | Ghi `Success` khi flow hoàn thành phần xử lý AI |
| `utcNow()` | `ProcessedAt` | Date and time | Thời điểm flow xử lý kết quả |

`ID` là cột tự động của Microsoft Lists. Flow sử dụng giá trị này để liên kết
các task với meeting vừa được tạo.

## MI Task Proposals

| Nguồn | Cột đích | Kiểu dữ liệu | Quy tắc |
| --- | --- | --- | --- |
| `task_name` | `Title` | Single line of text | Bắt buộc |
| `ID` của meeting vừa tạo | `MeetingItemId` | Number, 0 decimal places | Khóa liên kết tới `MI Meetings` |
| `assignee` | `AssigneeText` | Single line of text | Để trống nếu chưa xác định |
| `start_date` | `StartDate` | Date only | Chuỗi rỗng được chuyển thành `null` |
| `due_date` | `DueDate` | Date only | Chỉ nhận ngày đã chuẩn hóa; chuỗi rỗng thành `null` |
| `due_date_text` | `DueDateText` | Single line of text | Giữ nguyên mô tả như `trước thứ Sáu` |
| `evidence` | `Evidence` | Multiple lines of text | Câu transcript làm bằng chứng |
| Giá trị cố định | `Status` | Choice | Flow luôn ghi `Proposed` |

Không dùng `status` do AI trả về để điều khiển trạng thái nghiệp vụ. Việc duyệt
task sẽ được xử lý ở bước sau của dự án.

## Xử lý ngày trong Power Automate

`StartDate`:

```text
if(empty(item()?['start_date']), null, item()?['start_date'])
```

`DueDate`:

```text
if(empty(item()?['due_date']), null, item()?['due_date'])
```
