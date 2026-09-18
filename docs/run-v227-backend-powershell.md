# Tự chạy V2.27 dưới dạng backend thử nghiệm

V2.27 **không** được bật bằng `PIPELINE_VERSION=v227`. Endpoint mới là một
ASGI app opt-in riêng: `scripts.experimental_distillation.v227_api:app`.

Tenant challenger chỉ được chọn sau CLI activate/rollback và restart app; xem
[`run-v227-feedback-activation.md`](run-v227-feedback-activation.md). API chỉ
đọc `V227_FEEDBACK_TENANT_ID`, không có HTTP admin endpoint hay auto promotion.
Nó giữ route submit/poll của Power Automate nhưng dùng runner V2.27 với
model full-fit và policy private V2.28. Backend V1 tại
`backend.app.main:app` không thay đổi. V2.28 từng trượt gate diagnostic;
mọi kết quả V2.27 API phải được xem là **thử nghiệm, chưa validation**.

Các lệnh dưới đây là **để bạn tự chạy**. Không có dịch vụ hay tunnel nào
được tài liệu này tự khởi động. Ví dụ giả định bạn đang có clean release
worktree `C:\Intern AI SPS\meeting-intelligent-release`, còn `.venv`, API
key và private artifact ở project gốc. Sau khi merge, thay `cd` và đường
dẫn `.venv` bằng project gốc; vẫn giữ artifact private ngoài Git.

## Terminal PowerShell 1: V2.27 API

```powershell
cd 'C:\Intern AI SPS\meeting-intelligent-release'
$env:POWER_AUTOMATE_API_KEY = (Get-Content 'C:\Intern AI SPS\meeting-intelligent\evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
$env:V227_ARTIFACT_DIRECTORY = 'C:\Intern AI SPS\meeting-intelligent\evaluation\runtime\experimental-distillation-v2\v228-frozen-promotion'
& 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\uvicorn.exe' `
  scripts.experimental_distillation.v227_api:app `
  --host 127.0.0.1 --port 8011
```

API từ chối khởi động nếu thiếu `POWER_AUTOMATE_API_KEY` hoặc nếu model,
policy, manifest private thiếu/sai hash. Manifest V2.28 được khóa bằng
SHA-256 `7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3`.
**Không cần `OPENAI_API_KEY`**:
runner V2.27 luôn tắt provider và số lần gọi provider bằng 0. Chỉ dùng
một worker vì job store nằm trong bộ nhớ. Cổng `8011` tách khỏi V1 `8010`.

## Terminal PowerShell 2: HTTPS tunnel

Đây là tunnel cho staging V2.27, không phải V1 production. Nếu cần hostname
ổn định cho pilot, xem [runbook không cần Azure](deployment-without-azure-vi.md)
và dùng named tunnel; Quick Tunnel bên dưới chỉ dành cho demo ngắn. Không cài
Windows service hoặc scheduled task theo runbook này.

```powershell
& "$env:LOCALAPPDATA\cloudflared\cloudflared.exe" tunnel `
  --protocol http2 `
  --url http://127.0.0.1:8011
```

Đọc URL `https://...trycloudflare.com` **mới** trong terminal thứ hai. URL
Quick Tunnel thay đổi khi khởi tạo lại, không đảm bảo uptime và không bao giờ
là hostname production ổn định. Không dùng URL cũ từ lần chạy V1 trước. Nên dùng bản sao flow thử nghiệm và không ghi
task thử vào Lists production.

## Kiểm tra trước khi nối flow

Trong PowerShell thứ ba, đứng tại clean release worktree:

```powershell
cd 'C:\Intern AI SPS\meeting-intelligent-release'
$base = 'http://127.0.0.1:8011'
$key = (Get-Content 'C:\Intern AI SPS\meeting-intelligent\evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
Invoke-RestMethod "$base/health"

$path = 'data\power_automate_uploads\W1-SHORT-C1-N0-IT-DASG-ABS-002.txt'
$name64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFileName($path)))
$headers = @{ 'X-API-Key' = $key; 'X-File-Name-Base64' = $name64 }
$job = Invoke-RestMethod -Method Post `
  -Uri "$base/api/v1/meetings/jobs/process-file" `
  -ContentType 'application/octet-stream' -InFile $path -Headers $headers
$job | ConvertTo-Json
Invoke-RestMethod -Uri ($base + $job.status_url) -Headers @{ 'X-API-Key' = $key }
```

Nếu status còn `queued`/`running`, chạy lại dòng GET sau vài giây. Khi
`succeeded`, kiểm `pipeline_version` là `v227_experimental` và
`result.diagnostics.experimental_not_validated` là `true`. `failed` trả
`error`; không ghi task từ job lỗi. Có thể thay `$base` bằng URL HTTPS vừa
nhận để kiểm đường tunnel trước khi đưa vào flow.

## Power Automate

Trên **bản sao flow**, đặt `ApiBaseUrl` thành URL Quick Tunnel mới, giữ
`ApiKey` bằng khóa backend. `POST` gửi binary file tới
`{ApiBaseUrl}/api/v1/meetings/jobs/process-file`, `GET` theo
`{ApiBaseUrl}{status_url}`. Header và body như flow V1; schema JSON của
submit, status và result vẫn giữ cấu trúc cũ. Hãy kiểm tra
`pipeline_version == v227_experimental` và cờ
`experimental_not_validated == true` trước khi đọc kết quả. API từ chối
Meeting Note vì runner V2.27 chưa hỗ trợ; transcript phải có ngày họp trong
package, header `X-Meeting-Date`, hoặc dòng ngữ cảnh ghi ngày rõ ràng.
Không đưa OpenAI key vào flow. Cách cấu hình action chi tiết tại
[Power Automate README](../power-automate/README.md).
