# Báo cáo nghiên cứu distillation đến 2026-09-18

Báo cáo này là bản ghi phương pháp và quyết định, không phải thông báo triển
khai mô hình vào API. Phạm vi release và cách chạy ở
[tài liệu phát hành](../phat-hanh-v1-va-distillation.md); hash và đường dẫn
private ở [freeze lock](release-freeze-lock-2026-09-18.json).

## Câu hỏi nghiên cứu và phép đo

Giả thuyết: tạo tập task candidate có giới hạn rồi dùng selector nhỏ có thể
cải thiện nhận diện task mà vẫn giữ đúng owner, hạn và trạng thái. Chỉ số chính
là precision/recall/F1 của **task identity** theo evaluator trong repo;
field accuracy được báo riêng. Candidate coverage/oracle F1 hỏi liệu có
candidate khớp nhãn trong pool, dùng gold matching để tính trần; tuyệt đối
không được gọi đó là F1 của student. Với OOF, fit và chọn policy chỉ trên phần
training của từng fold; template/family holdout không được dùng để chỉnh
threshold. Các manifest ghi rõ lần đọc nhãn, provider và Kaggle.

## Dữ liệu

V2.27 dùng DEV42 = TRAIN34 + calibration8, gồm 15 template holdout fold và 27
family. Diagnostic9, final-dev18 và outer17 không được mở trong freeze V2.27.
DS26 gồm 20 case DeepSeek canonical; 13 case dùng cho V3.1 development, bảy
case shadow được giữ đến V3.2 rồi tiêu thụ. DS27 gồm 28 case, tách 21
development và bảy shadow theo ledger. V4 sau đó sử dụng cả 20 DS26 và 21
DS27 development, cùng DEV42 thành 83 meeting. V5 canary chọn 12 meeting,
bốn từ mỗi nhóm DEV42, DS26 và DS27 development. Final-dev18 và outer17 vẫn
đóng xuyên suốt. Dữ liệu và nhãn private không được commit.

## Diễn biến và kết quả

**Nhánh span và teacher ban đầu.** Các thử nghiệm span/boundary cho thấy lỗi
ở cả độ phủ candidate và chọn task. Teacher/candidate exact-span ceiling của
V1 chỉ có recall 0.274 so với gate 0.75, nên dừng full teacher-label
integration. V2.21 neural repair đạt held-out identity F1 0.37838, dưới
ngưỡng 0.50. V2.24 DEV42 cross-fit đạt F1 0.5789, field accuracy 0.8723
nhưng chưa là mốc cuối theo protocol ổn định hơn.

**V2.27, mốc chính.** V2.25 tạo `final_plus_bridge_plus_intermediate`
candidate union. V2.26 đặt nested template/family holdout. V2.27 dùng hashed
logistic ranker 768 feature, một epoch/một fit mỗi fold, rồi policy thích ứng
với số lượng ứng viên, source mix, phân phối score và mức đầy đủ field. Không
đưa template, family, case ID hay nhãn expected vào feature. DEV42 OOF:

| Chỉ số | Giá trị |
|---|---:|
| Precision | 0.5240963855 |
| Recall | 0.6258992806 |
| F1 | **0.5704918033** |
| Field accuracy | 0.8448275862 |
| Worst supported-family F1 | 0.4444444444 (OPS-FPC) |

Các gate đã khai báo được đáp ứng; kết luận chỉ là
`PROMISING_INTERNAL_ONLY`. V2.24 có F1 thô cao hơn; V2.27 được chốt do
protocol chặt và package bất biến, không phải vì vượt mọi bản trên mọi tập.

**V3.1/V3.2, kiểm tra khả năng chuyển tập.** V3.1 áp biến thể ranker/policy
trên DEV42 + DeepSeek DEV13 và đạt grouped OOF F1 0.6053268765, precision
0.5387931034, recall 0.6906077348. Riêng DEV42 F1 0.5657894737. Đây là
development mở rộng, không phải final holdout. V3.2 fit một lần với biến thể
đã chốt nhưng shadow7 chỉ đạt F1 0.3859649123, precision 0.3142857143,
recall 0.5, field accuracy 0.7121212121 và 24 task thừa. Run `-02` lỗi
`NameError` sau khi mở một phần nhãn; `-03` hoàn tất bằng cách mở lại cùng
nhãn. Erratum ghi riêng; shadow đã tiêu thụ. V3.1 được lưu làm comparator,
V3.2 không promote.

**V4 candidate rescue.** Pool V4.5 có 1.236 candidate trên 83 meeting,
candidate recall 0.8014705882 và oracle F1 0.8897959184. Student selector
không đạt trần này: V4.6 tốt nhất F1 0.49498; V4.7 0.30769; V4.8 0.4714;
V4.9 0.4891. V4.10 và V4.11 dừng do lỗi thực thi. V4.12 ghi base F1
0.37273, cascade A 0.41520, cascade B 0.40771 nhưng bước sau metrics lỗi
`oof_selected`; không có đánh giá hoàn chỉnh hoặc manifest. Các số này chỉ
có giá trị chẩn đoán.

**V5 DeepSeek teacher canary.** Có 12 provider attempts hợp lệ. Official
combined F1 0.5373134328, precision 0.5, recall 0.5806451613, field
accuracy 0.7592592593; F1/precision không qua gate. V5.1 phát hiện lệch
granularity giữa transcript, candidate và nhãn canonical. Adjusted F1 0.8986
chỉ là chẩn đoán theo contract khác, **không thay official metric**. V5.2
đóng gói 12 case blind adjudication; reviewer chưa hoàn tất. Không sửa nhãn
canonical từ audit này.

## Điều có thể và chưa thể kết luận

V2.27 là baseline nghiên cứu nội bộ bất biến theo gate DEV42; V3.1 là điểm
so sánh development có thêm dữ liệu. V4.5 chứng minh pool có nhiều ứng viên
khớp gold, không chứng minh selector chọn đúng. Shadow V3.2 là bằng chứng âm
cho hướng promote đó. Chưa có bằng chứng từ final-dev18/outer17, cải thiện
production, sự đồng thuận người đánh giá cho V5, hay ưu thế của DeepSeek so
với provider khác. Tập dữ liệu nhỏ, support theo family không đều, phép đo
nhạy với cách chia một task thành nhiều dòng, và nhiều artifact private nên
Git checkout riêng không đủ để replay toàn bộ số liệu.

V1 rule-first tiếp tục vận hành qua Power Automate. Chỉ mở nghiên cứu mới bằng
protocol/ngày/manifest và split ledger riêng; không dùng lại shadow đã mở làm
holdout mới, không tự gọi teacher/provider hoặc triển khai student từ các số
development trên.
