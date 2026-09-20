# Sổ theo dõi phân chia dữ liệu quanh mốc V2.27

Tài liệu này làm rõ thứ tự mở nhãn sau khi đóng băng V2.27. Nó nằm ngoài
package bất biến `evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze/`
và không sửa package hoặc hash.

| Run | Tập development | Diagnostic9 | Final-dev18 | Outer17 |
|---|---|---|---|---|
| V2.24 | DEV42 = TRAIN34 + calibration8 | Đọc một lần | Chưa đọc | Chưa đọc |
| V2.27 source | DEV42 = TRAIN34 + calibration8 | Chưa đọc | Chưa đọc | Chưa đọc |
| V2.28 | DEV42 | Đọc lại một lần để xác nhận tập đã tiêu thụ ở V2.24 | Chưa đọc | Chưa đọc |
| V2.29 | DEV51 = TRAIN34 + calibration8 + diagnostic9 | Chín case được đưa vào development | Chưa đọc | Chưa đọc |

Theo thứ tự thời gian: V2.24 đánh giá diagnostic9 một lần; V2.28 đọc lại để
xác nhận trên tập đã tiêu thụ; V2.29 đưa diagnostic9 vào DEV51. **Final-dev18
và outer17 vẫn chưa được mở.** Những lần đọc sau không phải bằng chứng
validation cho V2.27. Package canonical V2.27 chỉ dựa trên DEV42
leave-template-out và giữ cả ba tập diagnostic9/final-dev18/outer17 đóng tại
thời điểm freeze.

Các hash SHA-256 dùng để kiểm tra nguồn private (không commit nội dung):

| File | SHA-256 |
|---|---|
| V2.24 `split-access-audit.json` | `47a6c95b88771651a60bca20d1e5d2048762456f74c384306c69b27c2cf5acce` |
| V2.27 `metrics.json` | `05dfff5486df84d0b9a7333a76bc4c94c813f49acdc42e30ab168f0497f9c86e` |
| V2.27 `split-access-audit.json` | `8011cd721eb5e82b7e55dae337b51108ac48894c90bed054b474d0bcaeeeb132` |
| V2.28 `metrics.json` | `668029ad1cf1c36e662df7ed03ab56272101f9bf44e9cb9036af1e8ecd33e500` |
| V2.28 `manifest.json` | `7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3` |
| V2.29 `metrics.json` | `8a302d35b6d227ac9520e43d113fee2b7743b83b32633d2a96d666c942bbe9bb` |
| V2.29 `manifest.json` | `7b8a5cdc52ce5307b89422290977abe56edab0c0bcdbc84a1e4174f18921fac5` |
| V2.27 canonical `manifest.json` | `480e3648141c7d0f422ffef9dbf86d5be6505b3b8dbd8ccebc0942704d1e8099` |

Test kiểm tra nhất quán là
[`tests/test_v227_split_ledger.py`](../../tests/test_v227_split_ledger.py).
Khi private runtime artifact không có trong checkout Git, test chỉ bỏ qua
phần đối chiếu file private; các test cấu trúc fold độc lập vẫn chạy.
