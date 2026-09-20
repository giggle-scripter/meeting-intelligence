# Mốc V2.27 canonical dành cho nghiên cứu nội bộ

Package bất biến nằm tại
`evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze/`.
Runtime artifact được giữ private và Git ignore. Script
`scripts/experimental_distillation/freeze_v227_baseline.py` từ chối ghi vào
thư mục output không rỗng và ghi SHA-256 cho source, dependency, split và
artifact đã sao chép. Phân loại: **`PROMISING_INTERNAL_ONLY`**.

V2.27 là thí nghiệm DEV42 leave-template-out: candidate union của V2.25
(`final_plus_bridge_plus_intermediate`), nested template/family holdout của
V2.26 và volume-adaptive policy của V2.27. Mỗi template có một sparse hashed
ranker 768 feature, một epoch, learning rate 0.15. Chọn policy chỉ trên
meeting thuộc phần fit; feature policy chỉ được dựa vào lượng candidate,
source mix, phân phối score và mức đầy đủ assignee/due/status. Cấm template,
family, case ID và nhãn expected làm feature. Identity được so bằng Hungarian
matching theo từng meeting.

DEV42 có 42 meeting (TRAIN34 + calibration8), 15 template holdout và 27
family. Precision **0.52410**, recall **0.62590**, F1 **0.57049**, field
accuracy **0.84483**, worst supported-family F1 **0.44444** (OPS-FPC).
Aggregate gate F1 `>=0.57`, supported-family gate `>=0.40` và dung sai field
accuracy tối đa 0.03 so với expanded-pool baseline đều đạt. Run không gọi
teacher, provider hay Kaggle.

Diagnostic9, final-dev18 và outer17 không được mở trong source run V2.27;
không được dùng kết quả về sau để tuyên bố validation cho package này. Lịch
sử đọc diagnostic9 sau đó được ghi tại
[split ledger](v227_split_ledger.md): V2.24 đọc một lần, V2.28 đọc lại làm
confirmation, V2.29 đưa vào DEV51. Final-dev18 và outer17 vẫn đóng.

`source-hashes.json` giữ con trỏ nguồn/hash; thư mục `canonical/` chứa
metrics, coverage, policy từng fold, split-access audit, test, status và
report. Kiểm tra package private bằng:

```powershell
.\.venv\Scripts\python.exe scripts\experimental_distillation\freeze_v227_baseline.py --verify
```

`evaluation/runtime/experimental-distillation-v2/v227-debug4/` chỉ là bản
debug, không được trích dẫn làm baseline. Cách chạy thử trên transcript mới
ở [hướng dẫn V2.27](../run-v227-experimental.md); bản full-fit dùng ở CLI
thuộc V2.28 và đã trượt diagnostic gate, vì thế chưa thể triển khai vào API.
