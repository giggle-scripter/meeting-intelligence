# Tự chạy core V2 adaptive dưới dạng backend thử nghiệm

V2.27 **không** được bật bằng `PIPELINE_VERSION=v227` và không có ASGI endpoint
riêng. Backend dùng app thống nhất `backend.app.core_api:app`; mặc định là
`v1-frozen`, còn core V2 được chọn bằng `MEETING_CORE=v2-adaptive` (hoặc
`-Core`) rồi restart process.

Tenant challenger chỉ được chọn sau CLI activate/rollback và restart app; xem
[`run-v227-feedback-activation.md`](run-v227-feedback-activation.md). API đọc
`MEETING_FEEDBACK_TENANT_ID` (launcher truyền alias V2 tương thích), không có
HTTP admin endpoint hay auto promotion. V2 giữ route submit/poll/feedback của
Power Automate, dùng runner V2.27 với model full-fit và policy private V2.28.
V2.28 từng trượt gate diagnostic; mọi kết quả V2 API phải được xem là
**thử nghiệm, chưa validation**. V1 và V2 dùng cùng URL, API key và lớp Cloudflare;
chỉ thay core rồi restart.

Các lệnh dưới đây là **để bạn tự chạy**. Không có dịch vụ hay tunnel nào
được tài liệu này tự khởi động. Ví dụ giả định bạn đang có clean release
worktree `C:\Intern AI SPS\meeting-intelligent-release`, còn `.venv`, API
key và private artifact ở project gốc. Sau khi merge, thay `cd` và đường
dẫn `.venv` bằng project gốc; vẫn giữ artifact private ngoài Git.

## Terminal PowerShell 1: backend core V2

```powershell
cd 'C:\Intern AI SPS\meeting-intelligent-release'
$env:POWER_AUTOMATE_API_KEY = (Get-Content 'C:\Intern AI SPS\meeting-intelligent\evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
$env:MEETING_FEEDBACK_TENANT_ID = 'pilot-tenant'
$env:MEETING_FEEDBACK_DIRECTORY = 'evaluation\runtime\v227-feedback'
$env:V227_ARTIFACT_DIRECTORY = 'C:\Intern AI SPS\meeting-intelligent\evaluation\runtime\experimental-distillation-v2\v228-frozen-promotion'
$env:MEETING_JOB_SQLITE_PATH = 'evaluation\runtime\v227-feedback\meeting-jobs.sqlite3'
$python = 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe'
$env:PYTHONPATH = (Get-Location).Path
& .\scripts\run_local_meeting_core.ps1 -Core v2-adaptive -PythonExecutable $python
```

Launcher chạy cả V2 artifact preflight và common core preflight trước Uvicorn.
Preflight chỉ đọc cấu hình và artifact local, không gọi service, provider hoặc
tunnel. API từ chối khởi động nếu thiếu `POWER_AUTOMATE_API_KEY` hoặc nếu model,
policy, manifest private thiếu/sai hash. Manifest V2.28 được khóa bằng
SHA-256 `7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3`.
Khi audio tắt, **không cần `OPENAI_API_KEY`**: runner V2.27 luôn tắt
provider và số lần gọi provider bằng 0. Nếu bật `V227_AUDIO_TO_TEXT_ENABLED`,
preflight yêu cầu `OPENAI_API_KEY` server-side cho bước transcription. Chỉ
dùng một worker vì SQLite job store là file local, không dùng chung cho nhiều
replica. Cổng `8011` là cổng chung của V1 và V2.

Sau khi backend chạy, dùng [local V2.27 operator CLI](run-local-v227-operator-vi.md)
để submit, poll và gửi feedback đã được reviewer phê duyệt. CLI không khởi
động backend hay tunnel.

## Terminal PowerShell 2: HTTPS tunnel

Đây là tunnel cho staging V2, không phải V1 production. Nếu cần hostname
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
`succeeded`, kiểm `core_id` là `v2-adaptive`, `pipeline_version` là
`v227_experimental` và
`result.diagnostics.experimental_not_validated` là `true`. `failed` trả
`error`; không ghi task từ job lỗi. Có thể thay `$base` bằng URL HTTPS vừa
nhận để kiểm đường tunnel trước khi đưa vào flow.

## Power Automate

Trên **bản sao flow**, đặt `ApiBaseUrl` thành URL Quick Tunnel mới, giữ
`ApiKey` bằng khóa backend. `POST` gửi binary file tới
`{ApiBaseUrl}/api/v1/meetings/jobs/process-file`, `GET` theo
`{ApiBaseUrl}{status_url}`. Header và body như flow V1; schema JSON của
submit, status và result vẫn giữ cấu trúc cũ. Hãy kiểm tra
`core_id == v2-adaptive`, `pipeline_version == v227_experimental` và cờ
`experimental_not_validated == true` trước khi đọc kết quả. API từ chối
Meeting Note vì runner V2.27 chưa hỗ trợ; transcript phải có ngày họp trong
package, header `X-Meeting-Date`, hoặc dòng ngữ cảnh ghi ngày rõ ràng.
Không đưa OpenAI key vào flow. Cách cấu hình action chi tiết tại
[Power Automate README](../power-automate/README.md).
