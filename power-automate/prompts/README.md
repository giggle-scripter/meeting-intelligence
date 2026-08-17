# AI Builder prompt

Tạo custom prompt với một Text input tên `WindowJson`, sau đó paste nội dung
`ambiguous-event-extractor.txt`.

Dòng cuối `{{WindowJson}}` là vị trí đánh dấu. Trong Prompt Builder, xóa literal
này và chèn input chip `WindowJson` bằng **Add content** → **Text**. Không để
nguyên hai dấu ngoặc nhọn.

Chọn JSON output và dùng `../schemas/ai-event-output.sample.json` làm custom
sample. Prompt này chỉ dùng cho candidate window mơ hồ, không nhận full
transcript và không tạo final task JSON.
