# Kế hoạch triển khai V1/V2 theo cổng (Azure tùy chọn)

Kế hoạch này chia rollout thành các cổng có thể kiểm tra. Với ngày hiện tại
2026-09-18, mục tiêu trong tuần này chỉ là **đóng băng và demo prototype local
đến hết 2026-09-20**: V1 submit/poll, V2 staging approval và feedback thủ công
đều chạy được trên release worktree. Năm ngày dưới đây là thứ tự công việc,
không phải cam kết năm ngày lịch đã trôi qua trong tuần này. Azure là nhánh
managed tùy chọn sau pilot; tenant không đăng ký được Azure vẫn đi theo đường
Windows + Cloudflare named tunnel trong [runbook không cần Azure](deployment-without-azure-vi.md).

## Nguyên tắc và điều kiện trước

- V1 là backend duy nhất nối flow production. V2.27 chỉ chạy tenant thử nghiệm
  riêng; V2.28 private serving đã trượt diagnostic gate.
- Local files hiện có (`jobs` in-memory, feedback directory, active pointer) chưa
  phải durable database, chưa có backup/retention/replication và chưa deploy-ready
  cho multi-replica. Không trỏ Power Automate production vào process local hoặc
  Quick Tunnel dài hạn.
- Power Automate license/connector vẫn cần được xác nhận theo tenant. Azure
  region/SKU/network egress chỉ cần quote nếu sau này chọn nhánh managed; Azure
  không phải điều kiện pass của MVP. Xem [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
  và [Power Automate user license/service principal/flow](https://learn.microsoft.com/en-us/power-automate/assign-user-license-service-principal-flow).
- CPU trainer chỉ chạy one-shot sau human approval; GPU không cần cho MVP.

## Lịch theo cổng

### Ngày 1 — 2026-09-18: local contract và freeze prep

Chốt V1 package trên máy local: upload transcript test, `POST` job, poll
`status_url`, kiểm `MeetingId`/`ProposalKey` idempotency, lưu `NeedsReview` và
`UnresolvedWindowCount`. Chạy regression upload audit và smoke qua flow bản sao.
Lập bảng giá/region/license với owner tenant; không dùng giá placeholder để
phê duyệt ngân sách.

**Pass:** V1 rule-only và provider path (nếu được phép) đều trả schema đúng;
retry không tạo duplicate; flow local chỉ ghi vùng demo sau `succeeded`.

### Ngày 2 — 2026-09-19: local Power Automate smoke và V2 review

Chạy flow local với V1 theo trigger file → submit → poll → parse và vùng lưu
demo riêng. Chạy V2.27 ở tenant/storage prefix thử nghiệm, review và sửa task
list cuối, sau đó Approval mới được POST `corrected_final_tasks`; không sửa
transcript/speaker qua feedback endpoint. Email, nếu dùng trong demo, chỉ nhận
task đã approved.

**Pass:** submit/poll và retry local đúng contract; rejection/feedback lỗi
không tạo email; task chưa approved không đi vào output gửi người dùng.

### Ngày 3 — 2026-09-20: local prototype freeze và demo

Chốt release worktree và runbook local; chạy demo end-to-end có nhánh
success, failed/timeout, human reject và approved feedback. Lưu bằng chứng
`job_id`, `content_hash`, feedback hash, `coverage.json` (nếu trainer được gọi
thủ công) và xác nhận V1 production path không bị thay đổi.

**Pass:** prototype local được freeze/demo trước hoặc trong ngày 2026-09-20;
mọi lỗi có failure branch và không có direct write vào V1 production Lists.

### Ngày 4 — sau 2026-09-20: pilot Windows + named tunnel

Sau khi prototype local pass, chạy V1 trên một máy Windows luôn bật trong cửa
sổ pilot. Uvicorn chỉ bind `127.0.0.1:8010`; Cloudflare named tunnel cung cấp
hostname ổn định nếu người dùng có domain/account, còn Quick Tunnel chỉ dành
cho demo ngắn. Dùng `X-API-Key`; có thể thêm Cloudflare Access service token.
Bật Secure inputs/outputs cho submit, poll và Parse JSON. Không tạo service
Windows hay scheduled task theo runbook; operator mở hai terminal và kiểm
`/health` trước khi bật flow. Xem [pilot không cần Azure](deployment-without-azure-vi.md).

**Pass:** V1 submit/poll, retry, failure branch và flow staging chạy đúng;
hostname và key được kiểm tra; mọi người hiểu giới hạn uptime/sleep/restart.
Đây là pilot có operator, không phải 24/7 unattended.

### Ngày 5 — sau pilot pass: V2 review và đường dài hạn tùy chọn

V2 vẫn dùng app/hostname và storage prefix staging riêng; không ghi thẳng Lists
production. Sau feedback approved, operator chạy CPU trainer one-shot bằng CLI,
review challenger rồi mới activate/restart thủ công. Không auto-promotion,
không GPU và không cần Azure Container Apps Job. Nếu cần chạy lâu hơn, chuyển
API sang Windows host always-on hoặc VPS tại region người dùng đủ điều kiện;
Azure Container Apps/Functions chỉ là lựa chọn managed về sau.

Trước khi tuyên bố unattended/24/7 phải thay job store in-memory và local
feedback/pointer bằng persistent store có lock, backup, retention và restore;
chạy restore/restart smoke, kiểm idempotency/multi-replica và có rollback.

Reviewer xem `coverage.json`, diagnostics, manifest/hash và danh sách case. Một
operator có quyền riêng chạy activation CLI cho tenant staging, restart app,
smoke một job mới rồi kiểm audit. Nếu kết quả lỗi, chạy rollback về frozen base,
restart và xác nhận pointer/base hash. Chỉ sau khi staging pass mới cân nhắc
pilot tenant; production flow vẫn là V1.

**Pass/gate:** feedback thiếu reviewer/timestamp/approval bị từ chối; trainer
không đọc gold/expected trong trace; cùng input tạo cùng digest; output mới chưa
active; activation cần explicit operator action; không có auto-promotion; email
Power Automate chỉ đi sau approval; rollback có bằng chứng trước/sau. Nếu chưa
có queue/event wiring, trainer vẫn là CLI thủ công và không được gọi là
continual learning tự động.

## Kiến trúc đích sau các cổng

```text
OneDrive/SharePoint
        │ trigger + file content
        ▼
Power Automate ──HTTPS──> Windows V1 API (Uvicorn + named tunnel, pilot)
        │                         │
        │                         └── local files (operator-managed, not durable)
        │
        └── V2 staging API ──approved feedback──> CPU trainer CLI (one-shot)
                                             │
                                  challenger package + diagnostics
                                             │
                                  operator approve → activate/restart
                                             │
                                  rollback → frozen base
```

Power Automate giữ vai trò orchestration: trigger, HTTP submit/poll, approval,
upsert và email sau approval. Parser, reducer, date resolver, task identity và
training logic vẫn ở backend/job để tránh nhân đôi logic trong flow.

## Chi phí và rủi ro cần quote

V1 provider cost phụ thuộc token/call và chịu cost gates; V2 text inference và
CPU trainer không có provider charge. Audio diarization tính theo token của
provider, **$2.50/1M input tokens và $10/1M output tokens**, không suy ra giá mỗi
phút. Pilot Windows + named tunnel không cần Azure account; vẫn cần tính chi
phí máy, điện, mạng và Power Automate license/connector theo tenant. Azure
Container Apps, Blob, queue, Key Vault, logs, bandwidth và region chỉ quote khi
chọn nhánh managed. Tạm thời không dành ngân sách cho GPU chạy liên tục.

Các rủi ro phải giữ ở trạng thái rõ ràng: job store in-memory và feedback/pointer
local nếu chưa thay bằng durable store, host sleep/restart làm mất job, replica
race nếu chưa có idempotent lock, transcript nhạy cảm nếu retention chưa được
phê duyệt, và model drift nếu activation không qua reviewer. Nếu một cổng fail,
giữ V1 pilot ở trạng thái đã pass và dừng V2 ở staging; không bypass approval để
“cho chạy thử”.
