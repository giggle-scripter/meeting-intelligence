# Normalized Transcript Dataset

Dataset này là bản chuẩn hóa từ `archive/corpus-source/generated_transcripts`. Dữ liệu nguồn
không bị thay đổi.

## Cấu trúc

```text
normalized_transcripts/
  README.md
  index.json
  cases/
    <case_id>/
      metadata.json
      transcript.txt
```

Mọi case dùng cùng một schema. `transcript.txt` luôn có định dạng:

```text
[HH:MM:SS] Speaker: nội dung
```

Wave 1-3 được bỏ header generation. Wave 4-5 được ghép các part theo thứ tự,
bỏ `T000001` và part boundary nhưng giữ timestamp, speaker và nguyên văn lời
thoại.

## metadata.json

- `case_id`, `meeting_id`: business ID của meeting.
- `meeting_title`, `meeting_date`: input cho API.
- `wave`, `wave_name`, `labels`, `objective`: đặc tính test.
- `part_count`, `source_files`: truy vết về dữ liệu nguồn.
- `generation`: model và cấu hình sinh dữ liệu.
- `validation`: trạng thái, word count và turn count.
- `ground_truth`: hiện là `available=false`; corpus chưa có expected tasks.

## index.json

Index chứa toàn bộ case và đường dẫn tương đối. Dùng index để lọc theo wave,
label, meeting date hoặc độ dài mà không cần scan từng thư mục.

## Dùng với API

Để dùng với Power Automate, upload `transcript.txt` và map ba field
`meeting_id`, `meeting_title`, `meeting_date` từ `metadata.json`.

## Ground truth

Đây là exploratory/stress corpus, chưa phải regression dataset. Muốn đánh giá
precision/recall, review một case và tạo `expected_output.json` trong dataset
validation riêng. Không sửa transcript chuẩn hóa để làm output dễ pass.
