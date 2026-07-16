# Meeting Intelligence Feasibility

## Mục tiêu

Kiểm chứng ba khả năng:

1. AI chuyển transcript thành bản tóm tắt và danh sách task có cấu trúc.
2. Power Automate xử lý được JSON do AI trả về.
3. Microsoft Lists lưu và hiển thị được meeting cùng task đề xuất.

## Phạm vi hiện tại

- Transcript mẫu đã ẩn danh.
- Prompt AI Builder và bộ test feasibility.
- JSON output có cấu trúc cố định.
- Manual flow trên Power Automate.
- Hai Microsoft Lists dùng làm data store thử nghiệm.

## Ngoài phạm vi

- Ghi âm cuộc họp và speech-to-text.
- Production deployment.
- Tích hợp SP365 API chính thức.
- Quy trình duyệt task tự động.
- Phân quyền, retention và chống tạo dữ liệu trùng.

## Trạng thái

- Prompt feasibility: đạt 3/3 test case cơ bản.
- Microsoft Lists data model: đang hoàn thiện.
- Power Automate prototype: chưa chạy end-to-end.
- Python API: phương án fallback, chưa đưa vào luồng chính.

## Luồng mục tiêu

```text
Transcript
  → AI Builder prompt
  → Structured JSON
  → Power Automate
  → MI Meetings
  → MI Task Proposals
```

## Cấu trúc chính

- `backend/`: AI core và API fallback.
- `evaluation/`: transcript test và kết quả đánh giá prompt.
- `power-automate/`: flow design, schema và solution export.
- `sp365/`: data model, field mapping và payload mẫu.
- `docs/`: kiến trúc, bảo mật, kế hoạch và demo script.
