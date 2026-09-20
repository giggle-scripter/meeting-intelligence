# Quyết định đóng băng phát hành ngày 2026-09-18

Backend phục vụ Power Automate tiếp tục dùng V1 theo quy tắc, với OpenAI
fallback tùy chọn. Không có student distillation nào được promote vào API.
Hai mốc giữ lại để đối chiếu nghiên cứu là **V2.27 canonical** và **V3.1
development comparator**; chỉ V2.27 được phân loại
`PROMISING_INTERNAL_ONLY`. Xem [tài liệu phát hành tiếng Việt](../phat-hanh-v1-va-distillation.md)
để biết kiến trúc, cách chạy và ranh giới sử dụng.

## Hai mốc được giữ

| Mốc | Dữ liệu và kết quả | Trạng thái |
|---|---|---|
| V2.27 | DEV42 leave-template-out: precision 0.5240963855, recall 0.6258992806, F1 **0.5704918033**, field accuracy 0.8448275862, worst supported-family F1 0.4444444444 (OPS-FPC) | Qua gate development đã khai báo; chỉ dùng nội bộ |
| V3.1 | DEV42 + DeepSeek DEV13 grouped OOF: precision 0.5387931034, recall 0.6906077348, F1 **0.6053268765** | Mốc so sánh development; chưa qua shadow |

V2.27 dùng TRAIN34 + calibration8, 15 template fold. Candidate pool lấy từ
V2.25, fold template/family theo V2.26 và volume-adaptive policy theo V2.27.
Mỗi fold fit một lần; policy chỉ dùng dữ liệu fit. Các gate aggregate F1
`>=0.57`, supported-family F1 `>=0.40` và field accuracy giảm không quá 0.03
đều đạt. V2.24 từng có DEV42 F1 0.5789, cao hơn V2.27 về số thô; V2.27 được
chọn vì protocol nested template/family và policy an toàn khi inference chặt
hơn. Đây không phải tuyên bố V2.27 có F1 cao nhất ở mọi tập.

V3.1 dùng thêm 13 meeting development DeepSeek, vì thế F1 của nó không so
trực tiếp với DEV42 của V2.27. V3.2 fit theo biến thể đã khai báo trước nhưng
shadow7 đạt F1 **0.3859649123** (precision 0.3142857143, recall 0.5), không
qua gate. Run `-02` lỗi sau khi mở một phần nhãn, run `-03` mở lại cùng bảy
nhãn để hoàn tất; erratum nằm tại
`evaluation/runtime/experimental-distillation-v2/v4-deepseek-28-cases-01/v32-shadow7-audit-erratum.json`.
Shadow7 đã tiêu thụ và không còn là holdout sạch. Final-dev18, outer17 vẫn
đóng.

## Khóa bằng chứng

File [release-freeze-lock-2026-09-18.json](release-freeze-lock-2026-09-18.json)
ghi đường dẫn private và SHA-256, không sao chép dữ liệu. Hai hash manifest
quan trọng:

- V2.27: `480e3648141c7d0f422ffef9dbf86d5be6505b3b8dbd8ccebc0942704d1e8099`.
- V3.1: `eb471fbfadb87a9992e6215db9dcdc15008633c1701e04d54fb6f566e490e5ef`.

DS26 gồm 20 case canonical, SHA-256
`5677839eee80409a16a42ce23eb19ab6903b60726b03f0904e02df937779e59b`.
DS27 gồm 28 case canonical, SHA-256
`c2723ebb6e0288b248444931814b5ab38a7d6522e474cf2da2bb8366523094d1`;
split 21 development và bảy shadow đóng. Bảy case shadow DS26 đã dùng trong
V3.2; V4 sau đó dùng cả 20 DS26 làm development. Không tái diễn giải chúng
thành holdout mới.

V4.5 có oracle F1 0.8897959184 trên 1.236 candidate, nhưng đây chỉ là
giới hạn trên của tập ứng viên. Các selector V4.6–V4.12 chưa qua gate; V4.12
còn dừng vì lỗi sau bước metrics. Teacher canary V5 có official F1
0.5373134328 và chưa qua human adjudication. Nhánh teacher V1 trước đó dừng
vì exact-span recall ceiling 0.274 thấp hơn gate 0.75. Các mốc này không được
promote.

Private model, nhãn, transcript, prompt, phản hồi provider và runtime trace
phải tiếp tục nằm trong thư mục Git ignore. Không sửa package V2.27 tại chỗ.
Muốn mở lại nghiên cứu cần ngày/protocol/manifest mới, split-access ledger và
gate được review riêng. Quyết định này không tuyên bố cải thiện chất lượng
production hay tổng quát hóa sang final-dev18/outer17.
