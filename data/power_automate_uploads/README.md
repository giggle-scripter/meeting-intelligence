# Power Automate Upload Transcripts

Thư mục phẳng dùng để upload vào OneDrive/Power Automate.

- `<case-id>.txt` là package có meeting metadata + transcript gốc.
- `<case-id>__with-note.txt` là package A/B có cùng metadata + Meeting Note + transcript.
- Hai file dùng cùng `case_id` và expected output; phân biệt bằng cột `variant`.
- Nếu case không có `meeting_note.txt`, chỉ tạo bản transcript gốc.
- Backend tách metadata và Meeting Note trước khi parser đọc raw transcript.
- Tên file chính là `case_id`, ví dụ `W2-SHORT-C2-N0-IT-BRST-006.txt`.
- `index.csv` vẫn là manifest/audit; runtime đọc ID/title/date ngay từ file package.
- Dữ liệu gốc tại `data/validation/<case_id>/` không bị thay đổi.

Tạo lại thư mục:

```powershell
python scripts/prepare_power_automate_uploads.py
```
