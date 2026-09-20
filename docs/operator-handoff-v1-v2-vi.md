# Runbook bàn giao shell Meeting Core

Tài liệu này dành cho người vận hành khi bàn giao một shell nội bộ cho công ty. Shell dùng app thống nhất `backend.app.core_api:app` và cùng hợp đồng HTTP hiện tại: Power Automate gửi `POST /api/v1/meetings/jobs/process-file`, poll `GET /api/v1/meetings/jobs/{job_id}`, và gửi feedback qua `POST /api/v1/meetings/jobs/{job_id}/feedback`. App mặc định chọn `v1-frozen`; `v2-adaptive` là plugin tùy chọn, được chọn bằng `MEETING_CORE` rồi restart backend. API key dùng header `X-API-Key`; đây là khóa xác thực backend, khác với khóa provider AI (ví dụ khóa DeepSeek nếu môi trường có cấu hình). Các flow Power Automate, route API và lớp Cloudflare (nếu tenant đã có lớp đó) giữ nguyên URL và hợp đồng.

## Lối đi pilot và tài liệu liên quan

Thứ tự an toàn là: cấu hình và **khởi động thủ công** →
[unified smoke không feedback](../examples/demo-unified-core/README.vi.md) →
chỉ khi có approval rõ ràng mới chạy feedback smoke tùy chọn →
[backup và verify](run-pilot-runtime-protection-vi.md) →
[retention report](run-pilot-runtime-protection-vi.md) → diễn tập
[restore sang path mới](run-pilot-runtime-protection-vi.md). Smoke và runtime
protection không tự khởi động service, không tự huấn luyện; retention report
không tự xóa dữ liệu. [CI thủ công](run-local-v227-operator-vi.md) chỉ kiểm tra
repository theo workflow đã yêu cầu, không chạy service và không phải bằng chứng
về deployment hoặc chất lượng. [Private V2 extraction](run-private-v2-extraction-vi.md)
là migration riêng cần ủy quyền, không phải điều kiện tiên quyết của pilot.

## Chuẩn bị V1 frozen

V1 frozen là lựa chọn mặc định, chạy pipeline V1 hiện hành và không dùng adaptive training. Chuẩn bị một Python executable có các dependency trong `pyproject.toml`, một API key dùng chung, thư mục feedback riêng theo tenant và một SQLite path bền vững nằm ngoài source checkout:

```powershell
$env:POWER_AUTOMATE_API_KEY = "<secret-from-secret-store>"
$env:MEETING_FEEDBACK_TENANT_ID = "company-prod"
$env:MEETING_FEEDBACK_DIRECTORY = "D:\MeetingRuntime\feedback"
$env:MEETING_JOB_SQLITE_PATH = "D:\MeetingRuntime\meeting-jobs.sqlite3"
& powershell -ExecutionPolicy Bypass -File .\scripts\run_local_meeting_core.ps1
```

PowerShell script nhận Python cụ thể bằng `-PythonExecutable` nếu Python không nằm tại `.venv\Scripts\python.exe`. Script luôn bind `127.0.0.1:8011`, dùng một worker và chạy preflight offline trước khi khởi động `backend.app.core_api:app`. Preflight chỉ báo core, capability và mã pass/fail; không in API key, transcript hoặc nội dung feedback.

Lệnh chạy đúng là:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_meeting_core.ps1 -Core v1-frozen
```

Kiểm tra `GET http://127.0.0.1:8011/health`: `core_id` phải là `v1-frozen`, `adaptive` là `false`, và `supports_meeting_note` là `true`. Chỉ sau khi health pass mới bật flow gọi vào backend. Dừng bằng `Ctrl+C`; đổi cấu hình core không được thực hiện khi process đang chạy.

## Demo V2 adaptive tùy chọn

V2 adaptive là plugin/demo tùy chọn. Ngoài bốn biến dùng chung ở trên, V2 cần artifact bundle riêng và các kiểm tra offline của `scripts/local_v227_preflight.py` phải pass. Sau khi bundle pass, `scripts.core_preflight` mới nạp package adapter tùy chọn và báo đúng `runtime_model_id` đang được chọn; nếu pointer challenger của tenant đang active thì giá trị này là `v227-tenant-challenger`. Package không nạp được sẽ làm preflight fail kín, không in đường dẫn hay cấu hình nhạy cảm. V2 không nhận Meeting Note và không được xem như trạng thái production chỉ vì demo chạy được.

```powershell
$env:V227_ARTIFACT_DIRECTORY = "D:\MeetingRuntime\artifacts\v228-frozen-promotion"
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_meeting_core.ps1 -Core v2-adaptive
```

Preflight V2 kiểm tra manifest bất biến, hash model/policy, tenant feedback, SQLite và các cài đặt cục bộ trước khi có request. Không cài hoặc gọi trainer trong thao tác vận hành. Nếu artifact không hợp lệ, API không khởi động.

Feedback luôn được tiếp nhận theo cùng endpoint và được ghi vào ledger riêng của tenant khi payload hợp lệ. Feedback của V1 chỉ phục vụ audit, luôn có `adaptive_training_eligible=false` và không đủ điều kiện làm dữ liệu train cho V2. Việc ghi nhận feedback để audit và việc dùng feedback cho adaptive training là hai chính sách khác nhau.

## Chuyển core

Chuyển core chỉ bằng hai bước: đặt `MEETING_CORE` (hoặc dùng `-Core`) thành `v1-frozen` hay `v2-adaptive`, rồi restart backend. Không đổi core giữa chừng trong một process và không trộn hai core trong cùng job store nếu chưa có kế hoạch rollout. Sau restart, gọi `/health` và xác nhận `core_id`, `adaptive`, `pipeline_version` trước khi cho Power Automate chạy lại. Hợp đồng API, API key, tenant feedback, polling và lớp Cloudflare vẫn giữ nguyên.

## Tạo snapshot bàn giao

`scripts/build_company_shell_snapshot.py` chỉ sao chép allowlist runtime/docs/test đã định trước sang thư mục output mới hoặc rỗng. Với ủy quyền phù hợp, snapshot company-shell có thể bỏ artifact và plugin V2: tool loại `meeting_v2_adaptive`, `scripts/experimental_distillation`, runtime/artifact/dataset private và các adapter/test V2 được đánh dấu; snapshot vẫn chạy V1. Tool không xóa source, không di chuyển lịch sử Git, và tạo `manifest.json` cùng `manifest.sha256`. Sau khi copy, tool chạy isolated V1 import/health smoke bằng Python `-I`, thêm duy nhất đường dẫn snapshot vào `sys.path`, và cài deny hook cho các module V2 private để loại cả import lẫn resolve nhầm từ checkout hoặc editable install.

Tool này không quyết định quyền sở hữu IP. Chỉ chạy khi đã có ủy quyền phù hợp:

```powershell
& .\.venv\Scripts\python.exe .\scripts\build_company_shell_snapshot.py `
  D:\Handoff\meeting-core-v1 `
  --authorized `
  --python .\.venv\Scripts\python.exe
```

Không dùng output snapshot làm bằng chứng pháp lý về ownership. Giữ `manifest.sha256` cùng snapshot và lưu lại người phê duyệt ở hệ thống quản trị riêng.

## Giới hạn lịch sử Git

Snapshot là bản sao file phục vụ runtime và bàn giao. Nó không chứa `.git`, commit, branch, reflog hay toàn bộ lịch sử nghiên cứu. Vì vậy snapshot không chứng minh nguồn gốc, quyền sở hữu, thời điểm tạo ra artifact hoặc quyền sử dụng dữ liệu. Khi cần audit, tham chiếu repository và quy trình phê duyệt nội bộ riêng; đừng suy diễn các kết luận IP từ manifest.
