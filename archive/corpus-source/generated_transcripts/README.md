# Generated Transcript Corpus

Corpus gồm 86 meeting được sinh thành 5 wave:

| Wave | Cases | Runtime input |
| --- | ---: | --- |
| `wave_1_baseline` | 20 | `transcript.txt` |
| `wave_2_feature_isolation` | 30 | `transcript.txt` |
| `wave_3_feature_interaction` | 20 | `transcript.txt` |
| `wave_4_long_state_reducer` | 12 | ghép `part_01.txt` đến `part_05.txt` |
| `wave_5_extra_long_stress` | 4 | ghép `part_01.txt` đến `part_10.txt` |

`generation_meta.json` và `part_*.meta.json` chứa metadata sinh dữ liệu.
`session.json` và `run_summary.json` chỉ là provenance, không gửi vào API.

## Chạy exploratory corpus

```powershell
.\.venv\Scripts\python.exe scripts\run_generated_corpus.py --wave wave_1_baseline
```

Chạy một case dài:

```powershell
.\.venv\Scripts\python.exe scripts\run_generated_corpus.py `
  --wave wave_4_long_state_reducer `
  --case W4-LONG-C4-N1-IT-STATE-006
```

Chạy qua Azure và lưu actual output:

```powershell
.\.venv\Scripts\python.exe scripts\run_generated_corpus.py `
  --endpoint https://<function-app>.azurewebsites.net/api/v1/meetings/process `
  --output evaluation\generated-actual
```

## Giới hạn

Corpus này có transcript và generation objective nhưng không có final task ground
truth. Vì vậy nó dùng để kiểm tra parser, tải, unresolved windows và hành vi trên
meeting dài; chưa dùng để tính precision, recall hoặc field accuracy.

Muốn đưa một case vào regression chính thức:

1. Review transcript và xác định final active tasks bằng tay.
2. Tạo thư mục `data/validation/<meeting-id>/`.
3. Copy transcript đã ghép thành `transcript.txt`.
4. Tạo `metadata.json` theo contract của dataset.
5. Tạo `expected_output.json`.
6. Chạy `scripts/evaluate_dataset.py data/validation`.

Không chỉnh transcript gốc để làm output dễ pass. Nếu cần sửa lỗi dữ liệu sinh,
ghi lại lý do và tạo một fixture đã curate riêng.
