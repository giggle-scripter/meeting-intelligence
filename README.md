# Meeting Intelligence Feasibility

## Mục tiêu

Kiểm chứng hai khả năng:

1. AI Builder chuyển transcript thành `summary` và danh sách `tasks` có cấu trúc.
2. Power Automate lưu meeting và task proposal vào Microsoft Lists.

## Luồng prototype

```text
Transcript
  -> AI Builder prompt
  -> meeting_title + summary + tasks
  -> Power Automate
  -> MI Meetings + MI Task Proposals
```

## Kết quả

- Prompt test: 3/3 case pass, 3/3 task được trích xuất đúng, không có task bịa.
- End-to-end test: 3/3 flow run thành công, tạo đúng 3 meeting và 3 task.
- Task được liên kết với meeting bằng `MeetingItemId`.

Chi tiết:

- [Bảng đánh giá prompt](docs/prompt-test-result.md)
- [Kết quả end-to-end](evaluation/results.csv)
- [Thiết kế prototype](docs/prototype-design.md)

## Cấu trúc repository

```text
.
|-- docs/
|   |-- assets/                 # Ảnh flow và kết quả Microsoft Lists
|   |-- prompt-test-result.md   # Bảng đánh giá prompt
|   `-- prototype-design.md     # Flow, Lists và field mapping
|-- evaluation/
|   |-- transcripts/            # Ba transcript kiểm thử
|   `-- results.csv             # Kết quả end-to-end
|-- power-automate/
|   |-- prompts/                # Prompt AI Builder
|   |-- schemas/                # JSON schema và sample output
|   `-- solution-export/        # Power Automate solution export
`-- README.md
```
