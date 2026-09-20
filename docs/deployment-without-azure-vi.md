# Pilot V1 không cần Azure

Tài liệu này là đường triển khai MVP khi tenant không đăng ký được Azure do
giới hạn khu vực. **Không cần tạo Azure account và không được tìm cách vượt qua
điều kiện eligibility của Azure.** Azure là lựa chọn managed về sau, không phải
điều kiện để chạy pilot V1 hoặc flow Power Automate.

## Mô hình pilot được khuyến nghị

Dùng một máy Windows đang có sẵn để chạy một worker V1 duy nhất:

```text
Power Automate -> HTTPS Cloudflare named tunnel -> 127.0.0.1:8011 Uvicorn
                                               -> backend.app.core_api:app
                                                  (MEETING_CORE=v1-frozen)
```

Named tunnel dùng hostname ổn định của domain do người dùng sở hữu trong
Cloudflare. Tunnel chỉ chuyển tiếp tới loopback; không bind Uvicorn ra mạng
LAN/Wi-Fi. Cấu hình tunnel tham khảo tài liệu chính thức của Cloudflare:

- [Cloudflare Tunnel: Get started](https://developers.cloudflare.com/tunnel/get-started/)
- [Chạy tunnel như một service](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/)
- [Cloudflare Access service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)

Các lệnh PowerShell bên dưới chỉ là ví dụ để người vận hành tự chạy; tài liệu
này không tự khởi động Uvicorn, tunnel, scheduled task hay Windows service.

## Chuẩn bị trên máy Windows

Máy pilot cần Python, release worktree, quyền chạy PowerShell, mạng ra ngoài
và Power Automate/SharePoint của tenant. Cài `cloudflared` theo kênh quản trị
của máy; không commit credential vào repository. Nếu muốn hostname ổn định,
người vận hành cần domain/account Cloudflare và quyền tạo tunnel/DNS. Nếu chưa
có các quyền đó, chỉ dùng Quick Tunnel cho demo ngắn ở phần dưới.

Tạo môi trường Python và API key mạnh (ví dụ sau chỉ in cách làm, không chạy
tự động):

```powershell
Set-Location 'C:\Intern AI SPS\meeting-intelligent-release'
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

New-Item -ItemType Directory -Force evaluation\runtime\power-automate-local | Out-Null
$bytes = [byte[]]::new(32)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
[System.IO.File]::WriteAllText('evaluation\runtime\power-automate-local\api-key.txt', $secret)
```

Giữ file key ngoài Git và dùng cùng giá trị trong flow. Không đưa OpenAI key,
API key hoặc Cloudflare credential vào transcript, URL, source hay log.

## Chạy V1 và named tunnel thủ công

Terminal PowerShell 1, giữ mở trong thời gian pilot. Launcher chạy preflight
offline, bắt buộc SQLite status store và không tự tạo service/scheduled task:

```powershell
Set-Location 'C:\Intern AI SPS\meeting-intelligent-release'
$env:POWER_AUTOMATE_API_KEY = (Get-Content 'evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
$env:MEETING_FEEDBACK_TENANT_ID = 'pilot-tenant'
$env:MEETING_FEEDBACK_DIRECTORY = 'evaluation\runtime\meeting-feedback'
$env:MEETING_JOB_SQLITE_PATH = 'evaluation\runtime\meeting-jobs.sqlite3'
# Tùy chọn: $env:OPENAI_API_KEY = '<key chỉ đặt ở backend>'
.\scripts\run_local_meeting_core.ps1 -Core v1-frozen
```

Kiểm tra local trước khi mở flow, trong PowerShell 2:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8011/health'
```

Nếu đã có domain/account Cloudflare, đăng nhập và tạo named tunnel theo tài
khoản của người dùng. Các lệnh sau là ví dụ; thay giá trị trong dấu `<...>` và
không chạy chúng từ tài liệu này:

```powershell
cloudflared tunnel login
cloudflared tunnel create meeting-intelligent-v1
cloudflared tunnel route dns meeting-intelligent-v1 api.example.com
cloudflared tunnel --config 'C:\Users\<user>\.cloudflared\config.yml' run meeting-intelligent-v1
```

`config.yml` cần trỏ hostname ổn định về loopback và có ingress từ chối mặc
định:

```yaml
tunnel: <tunnel-uuid>
credentials-file: C:\Users\<user>\.cloudflared\<tunnel-uuid>.json
ingress:
  - hostname: api.example.com
    service: http://127.0.0.1:8011
  - service: http_status:404
```

Không chạy `cloudflared service install`, không tạo scheduled task và không
đăng ký service trong pilot này. Việc mở tunnel hay Uvicorn là thao tác thủ
công có chủ đích; khi đóng terminal, endpoint dừng.

## Bảo vệ endpoint và nối Power Automate

Backend luôn kiểm `X-API-Key`. Có thể thêm Cloudflare Access Application cho
hostname và dùng service token để tạo lớp xác thực thứ hai. Khi đó HTTP submit
và poll trong flow gửi các header sau:

```text
X-API-Key: <giá trị secret của backend>
CF-Access-Client-Id: <client id của service token>
CF-Access-Client-Secret: <client secret của service token>
```

Service token là tùy chọn; không bỏ `X-API-Key`. Đặt `MI_API_BASE_URL` là
`https://api.example.com` (không có slash cuối), `MI_API_KEY` và, nếu dùng
Access, hai biến token trong solution environment variables. Bật **Secure
inputs** và **Secure outputs** cho mọi action khởi tạo/đọc key, HTTP submit,
poll, Parse JSON và action có transcript/result để run history không giữ
secret hoặc nội dung nhạy cảm. Mapping job submit/poll, `status_url`, retry và
upsert giữ nguyên theo [Power Automate README](../power-automate/README.md).

Kiểm tra hostname và API key bằng lệnh ví dụ sau trước khi trigger flow:

```powershell
$key = (Get-Content 'evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
$headers = @{ 'X-API-Key' = $key }
Invoke-RestMethod 'https://api.example.com/health' -Headers $headers
```

## Quick Tunnel chỉ dành cho demo ngắn

Khi chưa có domain hoặc Cloudflare account phù hợp, có thể dùng Quick Tunnel:

```powershell
cloudflared tunnel --protocol http2 --url http://127.0.0.1:8011
```

URL `trycloudflare.com` đổi sau mỗi lần tạo lại, không có hostname ổn định và
không có cam kết uptime. Chỉ dùng URL hiện tại để smoke test hoặc demo ngắn;
không dùng Quick Tunnel làm hostname production/pilot dài ngày và không ghi
URL đó vào tài liệu như endpoint cố định.

## Giới hạn uptime và dữ liệu của pilot

Máy Windows phải bật, có mạng ổn định và không sleep/hibernate trong cửa sổ
pilot. Restart, Windows Update, mất mạng hoặc đóng terminal làm endpoint dừng;
người vận hành phải mở lại launcher và tunnel rồi kiểm tra `/health`. SQLite giữ
status và idempotency qua restart, nhưng job `queued`/`running` đang thực thi sẽ
được đánh dấu `failed` vì callable Python không thể resume; flow phải xử lý lỗi
và upload lại có chủ đích, không tự submit lại mù.

Feedback directory và active pointer của V2 cũng là file local. Chúng chưa là
store bền vững có lock, backup, retention, restore hay khả năng multi-replica.
Vì vậy pilot này không được gọi là unattended 24/7, HA hoặc production
multi-worker. Trước bất kỳ cam kết 24/7 nào phải có persistent job/feedback
store, backup và retention đã duyệt, kiểm tra restore, restart smoke test,
idempotency/locking và kế hoạch khôi phục. Power Automate vẫn phải giữ vùng
staging và human approval cho V2; CPU trainer chỉ chạy một lần bằng CLI sau
feedback được duyệt, không cần GPU và không tự promote model.

## Bước tiếp theo khi cần chạy lâu hơn

Khi pilot đạt tiêu chí, chuyển cùng container/API sang một Windows host luôn
bật hoặc VPS tại region mà người dùng đủ điều kiện sử dụng. Azure Container
Apps/Functions vẫn có thể được xem xét nếu tenant sau này có eligibility, nhưng
đó là lựa chọn sau pilot và không chặn MVP. Trước khi cho chạy unattended, hoàn
tất cả các hạng mục durable store, backup/retention, restore/restart smoke,
health monitoring, secret rotation và rollback. Không dùng GPU cho CPU trainer
one-shot.

Đường chạy V1 đầy đủ và runbook hiện tại:

- [Phát hành V1 và distillation](phat-hanh-v1-va-distillation.md)
- [Chạy V1 local](run-local-2026-09-18.md)
- [Kế hoạch triển khai theo cổng (Azure tùy chọn)](deployment-week-plan-vi.md)
