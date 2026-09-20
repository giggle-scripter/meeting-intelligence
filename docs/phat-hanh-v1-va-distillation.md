# Bản phát hành V1 và hai mốc nghiên cứu distillation

Tài liệu này chốt phạm vi mã nguồn để review, push và merge. **Backend phục vụ
Power Automate mặc định là V1 frozen** (`MEETING_CORE=v1-frozen`). Hai mốc V2.27 và V3.1 là
kết quả nghiên cứu nội bộ, không phải hai chế độ production của API.

| Mốc giữ lại | Vai trò | Kết quả task identity | Quyết định |
|---|---|---|---|
| V2.27 | Mốc chính, DEV42 leave-template-out | Precision 0.52410; recall 0.62590; F1 **0.57049**; field accuracy 0.84483 | `PROMISING_INTERNAL_ONLY`; đóng băng manifest |
| V3.1 | Mốc so sánh trên DEV42 + DeepSeek DEV13 | Precision 0.53879; recall 0.69061; F1 **0.60533** | Chỉ giữ làm development comparator; không promote |

Không so hai con số F1 như cùng một phép thử: V3.1 dùng thêm 13 meeting
development. V3.2 huấn luyện một lần theo hướng V3.1 nhưng chỉ đạt F1
**0.38596** trên bảy case shadow DS26 (precision 0.31429, recall 0.5), nên
không có bằng chứng chuyển sang production. Shadow này đã được đọc và không
còn là holdout sạch. Final-dev18 và outer17 vẫn đóng.

## V1 đang chạy như thế nào

Pilot không bị chặn bởi Azure. Nếu tenant không đăng ký được Azure, dùng
[runbook Windows + Cloudflare không cần Azure](deployment-without-azure-vi.md):
Uvicorn bind `127.0.0.1`, named tunnel cung cấp hostname ổn định khi có domain
và Cloudflare account, còn Quick Tunnel chỉ dành cho demo ngắn. Không tìm cách
vượt qua điều kiện Azure eligibility.

Người dùng tải `.txt`, `.vtt` hoặc `.srt` lên OneDrive/SharePoint. Power
Automate lấy nội dung file, gọi job API và poll `status_url`. FastAPI đọc
transcript và metadata, tách turn/clause, khử lặp, nhận diện sự kiện giao việc,
cam kết, thay người hoặc hạn, từ chối và hủy. Bộ giảm trạng thái giữ quyết định
còn hiệu lực cuối cùng; Python giải ngày, dựng bằng chứng, summary và task
proposal. Đoạn mơ hồ có thể đi qua OpenAI Responses API nếu backend có
`OPENAI_API_KEY`; Python vẫn kiểm tra ID, schema và bằng chứng trước khi dùng sự
kiện. Không có key thì nhánh quy tắc vẫn hoạt động, kèm thông tin window chưa
giải quyết. OpenAI key chỉ nằm ở backend, không đưa vào flow.

V1 và distillation khác nhau: V1 là core mặc định của API; V2.27/V3.1 đánh giá
việc tạo tập ứng viên rồi chọn task bằng mô hình nhỏ. Không dùng
`PIPELINE_VERSION` để chọn V2. Core V2 chỉ được bật bằng
`MEETING_CORE=v2-adaptive` (hoặc `-Core`) rồi restart app thống nhất.

## Dữ liệu, phương pháp và ranh giới đánh giá

V2.27 dùng DEV42 = TRAIN34 + calibration8, gồm 15 template và 27 family.
Nguồn ứng viên V2.25 hợp nhất task cuối của V1, bridge và intermediate trace.
V2.26 đặt các fold holdout theo template/family. V2.27 huấn luyện một sparse
hashed logistic ranker 768 chiều, một epoch và một fit cho mỗi fold; chính
sách chọn lọc xét số lượng ứng viên trong meeting, nguồn, phân phối điểm và
mức đầy đủ owner/date/status. Policy và ngưỡng được chọn chỉ trên phần fit;
template, family, case ID và nhãn expected không được làm feature. Matching
task dùng Hungarian match theo từng meeting. Kết quả trên DEV42 là
out-of-fold (OOF), không phải thử nghiệm trên final-dev hoặc outer.

V3.1 thêm 13 case development của DS26 vào DEV42 và dùng lại kiểu ranker/policy
để so sánh trên 55 meeting. DS26 có 20 case canonical, SHA-256
`5677839eee80409a16a42ce23eb19ab6903b60726b03f0904e02df937779e59b`;
bảy case còn lại được giữ tới V3.2, rồi đã tiêu thụ. DS27 gồm 28 case,
SHA-256 `c2723ebb6e0288b248444931814b5ab38a7d6522e474cf2da2bb8366523094d1`;
21 development và bảy shadow theo split đã khai báo. Không dùng các shadow đã
đọc để chọn lại threshold hoặc trình bày như external validation.

Độ phủ ứng viên hoặc oracle F1 chỉ đo liệu trong pool có ứng viên khớp nhãn;
nó là giới hạn trên và không phải F1 của student. Teacher V1 ban đầu dừng tại
exact-span recall 0.274, dưới gate 0.75; không có full teacher-label
integration. V4.5 đạt oracle F1 0.88980 trên 1.236 ứng viên nhưng các student
V4.6–V4.12 không vượt gate hoàn chỉnh. Teacher canary V5 đạt official F1
0.53731 và chưa qua human adjudication; adjusted score từ audit không thay
official score. Đây là lý do không chọn các bản đó làm mốc release.

## Bằng chứng đóng băng và khả năng chạy

- V2.27 manifest SHA-256:
  `480e3648141c7d0f422ffef9dbf86d5be6505b3b8dbd8ccebc0942704d1e8099`.
- V3.1 manifest SHA-256:
  `eb471fbfadb87a9992e6215db9dcdc15008633c1701e04d54fb6f566e490e5ef`.
- Đường dẫn và các hash bổ sung nằm trong
  [release-freeze-lock-2026-09-18.json](experiments/release-freeze-lock-2026-09-18.json).

Manifest, model, transcript, nhãn và phản hồi provider nằm trong
`evaluation/runtime/`, được Git ignore; **không push** các file đó. V2.27 có
CLI và core opt-in trong app thống nhất để chạy trên transcript mới. Cả hai dùng model full-fit
và policy trong private package V2.28. V2.28 đã trượt gate diagnostic, nên
kết quả luôn mang cờ `experimental_not_validated`; xem
[CLI V2.27](run-v227-experimental.md) và
[backend V2.27 thử nghiệm](run-v227-backend-powershell.md). V3.1 chỉ được chốt là bằng chứng
development và chưa có endpoint/runner inference ổn định. Không được quảng
cáo một trong hai là bản thay V1 trong Power Automate.

## Cách chạy V1 với Power Automate

Các lệnh sau **chỉ là hướng dẫn**, không tự khởi chạy dịch vụ. Tại root dự án,
cài môi trường theo [README](../README.md). Tạo secret riêng và lưu vào file
private được Git ignore; làm một lần:

```powershell
cd 'C:\Intern AI SPS\meeting-intelligent'
New-Item -ItemType Directory -Force evaluation\runtime\power-automate-local | Out-Null
$bytes = [byte[]]::new(32)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
[System.IO.File]::WriteAllText('evaluation\runtime\power-automate-local\api-key.txt', $secret)
```

Nếu file đã có, **giữ nguyên khóa** để khỏi phải cập nhật flow. Mở PowerShell
thứ nhất và giữ cửa sổ này chạy:

```powershell
cd 'C:\Intern AI SPS\meeting-intelligent'
$env:POWER_AUTOMATE_API_KEY = (Get-Content 'evaluation\runtime\power-automate-local\api-key.txt' -Raw).Trim()
$env:MEETING_FEEDBACK_TENANT_ID = 'pilot-tenant'
$env:MEETING_FEEDBACK_DIRECTORY = 'evaluation\runtime\meeting-feedback'
$env:MEETING_JOB_SQLITE_PATH = 'evaluation\runtime\meeting-jobs.sqlite3'
# Tùy chọn: $env:OPENAI_API_KEY = '<OpenAI API key>'
.\scripts\run_local_meeting_core.ps1 -Core v1-frozen
```

Mở PowerShell thứ hai và giữ cửa sổ này chạy:

```powershell
& "$env:LOCALAPPDATA\cloudflared\cloudflared.exe" tunnel `
  --protocol http2 `
  --url http://127.0.0.1:8011
```

Lệnh trên là Quick Tunnel cho demo ngắn. URL `https://...trycloudflare.com`
đổi sau mỗi lần tạo lại, không có cam kết uptime và **không bao giờ là
hostname production ổn định**. Pilot dài hơn phải dùng named tunnel với domain;
xem [runbook không cần Azure](deployment-without-azure-vi.md). Kiểm tra
`GET {ApiBaseUrl}/health` trước khi bật flow. Trong Power Automate,
`ApiBaseUrl` là hostname hiện tại, `ApiKey` là
nội dung file private. Trigger OneDrive/SharePoint → Get file content → HTTP
`POST {ApiBaseUrl}/api/v1/meetings/jobs/process-file` với body binary,
`Content-Type: application/octet-stream`, `X-API-Key: {ApiKey}` và
`X-File-Name-Base64: base64(tên file)`. Lấy `status_url` từ phản hồi 202,
poll `GET {ApiBaseUrl}{status_url}` với cùng key tới `succeeded`/`failed`.
Chỉ ghi MI Meeting/MI Task Proposal khi `succeeded`. Bật Secure inputs/outputs
cho action chứa khóa, transcript và kết quả. Launcher local dùng SQLite cho
status/idempotency của một process; job `queued`/`running` khi restart được đánh
dấu `failed` vì không thể resume callable. Chi tiết mapping trong
[Power Automate README](../power-automate/README.md).

## Quy tắc merge

Chỉ merge mã V1 hiện có, CLI nghiên cứu đã tách biệt, hai bản ghi freeze và tài
liệu. Không merge dữ liệu private, notebook output, token, prompt/response
provider hoặc các thử nghiệm chưa qua review. Bất kỳ việc mở lại model,
threshold, dữ liệu holdout hay teacher đều phải có protocol và manifest mới.
