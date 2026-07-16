# Thiết lập Microsoft Lists cho prototype

## Mục tiêu

Tạo nơi lưu meeting và task proposal để kiểm tra luồng AI Builder → Power
Automate → Microsoft Lists. Prototype hiện dùng list trong `My lists`.

## List `MI Meetings`

Giữ cột `Title` mặc định và tạo thêm các cột sau:

| Tên cột | Kiểu dữ liệu | Cấu hình |
| --- | --- | --- |
| `TestCaseId` | Single line of text | Không bắt buộc |
| `Summary` | Multiple lines of text | Plain text |
| `ProcessingStatus` | Choice | `Success`, `Failed` |
| `ProcessedAt` | Date and time | Bao gồm ngày và giờ |

Cột `ID` được Microsoft Lists tạo tự động, không cần tạo thủ công.

## List `MI Task Proposals`

Giữ cột `Title` mặc định và tạo thêm các cột sau:

| Tên cột | Kiểu dữ liệu | Cấu hình |
| --- | --- | --- |
| `MeetingItemId` | Number | 0 decimal places |
| `AssigneeText` | Single line of text | Không bắt buộc |
| `StartDate` | Date and time | Date only |
| `DueDate` | Date and time | Date only |
| `DueDateText` | Single line of text | Không bắt buộc |
| `Evidence` | Multiple lines of text | Plain text |
| `Status` | Choice | `Proposed`, `Approved`, `Rejected`; mặc định `Proposed` |

## Kiểm tra thủ công

1. Tạo một item trong `MI Meetings` và ghi lại giá trị cột `ID`.
2. Tạo một item trong `MI Task Proposals`.
3. Điền `MeetingItemId` bằng `ID` của meeting ở bước 1.
4. Để trống `StartDate` và `DueDate` nếu transcript không có ngày cụ thể.
5. Ghi mô tả tương đối, ví dụ `trước thứ Sáu`, vào `DueDateText`.
6. Xác nhận item được lưu và `Status` là `Proposed`.

## Giới hạn hiện tại

- `My lists` phù hợp để test cá nhân, chưa phải nơi lưu dùng chung của dự án.
- `AssigneeText` mới lưu tên dạng text, chưa ánh xạ tới tài khoản Microsoft 365.
- `MeetingItemId` là liên kết logic, chưa phải SharePoint Lookup column.
- Quyền truy cập, retention, approval và chống tạo trùng chưa nằm trong scope PR này.

Khi có SharePoint project site, tạo lại hai list từ cấu trúc trên hoặc sao chép
từ list hiện có, sau đó cập nhật connection trong Power Automate.
