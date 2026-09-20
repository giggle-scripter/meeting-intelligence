# Kích hoạt challenger V2.27

API chỉ đọc tenant từ biến môi trường `V227_FEEDBACK_TENANT_ID`. Không có
biến này thì API luôn dùng package V2.28 frozen đã pin hash và bỏ qua pointer.
Không có HTTP admin endpoint và không có auto promotion.

Sau khi trainer tạo package, đặt đúng tenant trong môi trường riêng tư rồi chạy
CLI offline:

```powershell
$env:V227_FEEDBACK_TENANT_ID = "acme"
python scripts\experimental_distillation\v227_api.py activate `
  --challenger evaluation\runtime\v227-feedback\acme\challengers\<digest> `
  --feedback-directory evaluation\runtime\v227-feedback `
  --artifact-directory evaluation\runtime\experimental-distillation-v2\v228-frozen-promotion
```

CLI kiểm tra package immutable, hash của mọi artifact, liên kết base V2.28,
policy, 768 trọng số hữu hạn, có meeting feedback và ít nhất một positive đã
match. Pointer private của tenant được ghi atomic tại
`<feedback-directory>\<tenant>\active-v227-pointer.json`; pointer giữ pointer
trước đó và audit. Vì vậy nhiều tenant có thể dùng chung
`V227_FEEDBACK_DIRECTORY` mà không ghi đè trạng thái activation của nhau. Nếu
tenant khác hoặc package bị sửa, lệnh dừng.

Rollback về base:

```powershell
python scripts\experimental_distillation\v227_api.py rollback `
  --feedback-directory evaluation\runtime\v227-feedback `
  --artifact-directory evaluation\runtime\experimental-distillation-v2\v228-frozen-promotion
```

Pointer chỉ được đọc lúc startup và luôn được chọn theo tenant đã cấu hình.
Sau activate hoặc rollback phải restart ASGI app; không có hot reload. Nếu
manifest, artifact hoặc pointer của tenant bị tamper, startup hoặc job load sẽ
fail closed.
