# Dataset Layout

```text
data/
  validation/               corpus chuẩn (source of truth)
  evaluation_splits/        manifest locked-validation và digest corpus
  blind_test/               chỉ chứa blind corpus độc lập đã review/freeze
  ml/action-classifier/     dataset clause-level sinh từ validation
  fixtures/                 fixture nhỏ cho unit/integration test
  power_automate_uploads/   fixture upload A/B sinh từ validation
  normalized_transcripts/   bản normalized cũ, trùng validation và không dùng làm corpus chuẩn
archive/corpus-source/
  generated_transcripts/    nguồn/provenance W1-W5 đã archive
```

Mỗi case trong `validation/` dùng cấu trúc:

```text
case_001/
  metadata.json
  transcript.vtt
  expected_output.json
  meeting_note.txt          # optional
```

`validation/` là nguồn chuẩn đã review cho regression. Không sửa trực tiếp
`power_automate_uploads/`: chạy `scripts/prepare_power_automate_uploads.py` sau
khi transcript hoặc Meeting Note nguồn thay đổi. Mỗi case upload có bản
`<case-id>.txt` (không note) và `<case-id>__with-note.txt` (embedded note), cùng
`index.csv` để đối chiếu expected task count.

Dataset classifier được regenerate, không sửa tay:

```powershell
.\.venv\Scripts\python.exe scripts\build_action_classifier_dataset.py `
  data\validation `
  --output-dir data\ml\action-classifier
```

`train.jsonl` chứa cả record train được và record chờ review. Chỉ record có
`eligible_for_training=true` mới được đưa vào model. `folds.json` group theo
`meeting_id`, không split clause cùng meeting qua train/validation.

`model/action-clf-v1.json` là artifact Logistic Regression portable sinh bởi
`scripts/train_action_classifier.py`; `model/training-report.json` lưu metric
grouped 5-fold và limitation. Không sửa tay hai file này. Model hiện dùng
multilingual MiniLM embedding, chỉ hợp lệ cho shadow evaluation và chưa được tune
production threshold.

`evaluation_splits/locked_validation_v1.json` khóa 20 case đại diện cùng
SHA-256 digest của source/expected output. Đây vẫn là development corpus đã
từng được xem, vì vậy chỉ gọi là **locked validation**, không gọi là blind.
`blind_test/` chỉ được điền bằng meeting độc lập đã review theo contract trong
thư mục đó.
