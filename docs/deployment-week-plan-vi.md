# Kế hoạch triển khai Azure + Power Automate theo cổng

Kế hoạch này chia rollout thành các cổng có thể kiểm tra. Với ngày hiện tại
2026-09-18, mục tiêu trong tuần này chỉ là **đóng băng và demo prototype local
đến hết 2026-09-20**: V1 submit/poll, V2 staging approval và feedback thủ công
đều chạy được trên release worktree. Năm ngày dưới đây là thứ tự công việc,
không phải cam kết năm ngày lịch đã trôi qua trong tuần này. Các bước managed
Azure bắt đầu sau 2026-09-20 và chỉ được mở khi cổng trước đã đạt; không bật V2
làm production model trong giai đoạn này.

## Nguyên tắc và điều kiện trước

- V1 là backend duy nhất nối flow production. V2.27 chỉ chạy tenant thử nghiệm
  riêng; V2.28 private serving đã trượt diagnostic gate.
- Local files hiện có (`jobs` in-memory, feedback directory, active pointer) chưa
  phải durable database, chưa có backup/retention/replication và chưa deploy-ready
  cho multi-replica. Không trỏ Power Automate production vào process local hoặc
  Quick Tunnel dài hạn.
- Azure region, SKU, network egress, log retention và Power Automate
  license/connector cần quote từ tenant. Xem [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
  và [Power Automate user license/service principal/flow](https://learn.microsoft.com/en-us/power-automate/assign-user-license-service-principal-flow).
- Container Apps Jobs phù hợp cho CPU training theo event/run; xem
  [Container Apps jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs).
  GPU là nhánh tùy chọn sau tuần này, không chạy liên tục.

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

### Ngày 4 — sau 2026-09-20, gated: managed V1 foundation

Chỉ mở sau khi prototype local pass. Build image từ release worktree, chạy API
trong Azure Container App/managed container với một revision. Đưa API key/OpenAI
key vào Key Vault hoặc secret reference của app; bật HTTPS ingress giới hạn,
Secure inputs/outputs ở flow và không gửi OpenAI key qua Power Automate. Dùng
private Blob cho source, job payload/result và retention; thay in-memory job
status bằng store/queue durable trước khi mở replica.

**Pass/gate:** `/health`, submit/poll, restart và retry vẫn giữ trạng thái; log
không có transcript/token/key; private storage có quyền tối thiểu và retention
đã ghi. Nếu gate fail, giữ prototype local và không mở Ngày 5.

### Ngày 5 — sau managed V1 pass, gated: V2 event job và operator handoff

Đặt V2 API ở app/hostname riêng, tenant và storage prefix riêng; không ghi thẳng
Lists production. Sau feedback approved, managed wiring mới được phép phát event
từ private storage/queue để chạy Azure Container Apps Job trên CPU. Job đọc
append-only feedback, reconstruct trace, replay ranker deterministic và ghi
challenger package bất biến. Job không có provider key, không gọi mạng và ghi
manifest với `gpu_used: false`.

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
Power Automate ──HTTPS──> Managed V1 API (Container App, durable job store)
        │                         │
        │                         ├── private Blob/source + result
        │                         └── Key Vault / app secrets
        │
        └── V2 staging API ──approved feedback──> Queue/Blob event
                                             │
                                             ▼
                                  Container Apps Job (CPU trainer)
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
phút. Azure Container Apps, Blob, queue, Key Vault, logs, bandwidth và region
được báo giá riêng; Power Automate license/connector/service principal cũng
được quote riêng theo tenant. Tạm thời không dành ngân sách cho GPU chạy liên
tục.

Các rủi ro phải giữ ở trạng thái rõ ràng: job store in-memory nếu chưa thay bằng
durable store, replica race nếu chưa có idempotent lock, transcript nhạy cảm nếu
retention chưa được phê duyệt, và model drift nếu activation không qua reviewer.
Nếu một cổng fail, giữ V1 local/managed ở trạng thái đã pass và dừng V2 ở
staging; không bypass approval để “cho chạy thử”.
