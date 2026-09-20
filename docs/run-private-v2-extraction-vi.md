# Runbook chuẩn bị tách riêng V2

Tài liệu này mô tả bước chuẩn bị mã nguồn V2. Việc chuẩn bị không kết luận quyền sở hữu, không tạo package cài đặt độc lập và không xoá hoặc sửa lịch sử Git của checkout hiện tại.

## Kiểm tra ranh giới

Chạy từ gốc checkout:

```powershell
& 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe' scripts\audit_private_v2_boundary.py --python 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe'
```

Kết quả đạt có `status=PASS`, các mã kiểm tra, số lượng và hash. Boundary hiện yêu cầu đủ bốn nhóm capability: `inference`, `feedback_training`, `activation_rollback` và `adapter`; hiện có 14 source files và 14 capability files. Audit import closure dùng AST, không thực thi source, và phải có `import_closure_failures=0`; audit bao phủ cả import tương đối giữa các bridge V2.17/V2.18/V2.19 và import private tuyệt đối. Source-only hand-off gồm cả trainer `scripts/experimental_distillation/train_v227_feedback.py` cùng các bridge private trực tiếp mà trainer dùng. Công cụ không in nội dung source, transcript, dataset, feedback hay secret. Nếu có mã `STOP_`, dừng quy trình và xử lý nguyên nhân trước khi tách.

## Chuẩn bị gói source-only

Chỉ thực hiện sau khi có uỷ quyền rõ ràng từ chủ sở hữu phù hợp:

```powershell
& 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe' scripts\prepare_private_v2_extraction.py --output-directory D:\migration\meeting-intelligent-v2-source --authorized
```

Thư mục output phải nằm ngoài checkout và mới hoặc rỗng. Công cụ chỉ sao chép các file được liệt kê trong `docs/private-v2-extraction-boundary.json`; nó từ chối symlink, file đặc biệt và tên có dấu hiệu secret. Output có `manifest.json` và `manifest.sha256`, được gắn nhãn `migration_source_only`, `standalone_installable=false`, `ownership_decision=false`.

Output không bao giờ gồm `.git`, evaluation/runtime, artifact, dataset, feedback, key hoặc credential. Operator HTTP client và public company shell vẫn nằm ngoài private implementation trừ khi được boundary khai báo là dependency bắt buộc. Không dùng output này để chạy production, cài đặt package hay huấn luyện.

## Các bước trong private repository riêng

1. Người được uỷ quyền tạo một repository private mới và đặt quyền truy cập, retention, secret scanning, branch protection và người phê duyệt theo chính sách công ty.
2. Đối chiếu `manifest.json`, `manifest.sha256`, boundary manifest và canonical V2.28 manifest hash. Kiểm tra hash file sau khi nhận bằng công cụ độc lập.
3. Chép source vào private repository theo đúng đường dẫn tương đối. Giữ `backend/app/core/registry.py` làm boundary lựa chọn core; V1 vẫn là mặc định. Chỉ bật `v2-adaptive` trong môi trường có artifact và feedback store được kiểm soát riêng.
4. Thiết lập packaging riêng sau khi review dependency và cấp phép. Không suy ra rằng source-only hand-off đã là package có thể cài đặt.
5. Chuyển artifact V2.28, runtime data, dataset, feedback và key qua kênh lưu trữ được phê duyệt; chúng không nằm trong extraction này. Kiểm tra quyền đọc/ghi và audit log riêng cho các thành phần đó.
6. Chạy test, kiểm tra bảo mật và review pháp lý trong private repository trước khi tạo release hoặc triển khai.

Lịch sử Git trước đó vẫn còn trong repository hiện tại. Runbook này không yêu cầu rewrite history, filter branch hoặc xoá commit. Việc tách source cũng không phải là kết luận IP; mọi quyết định ownership và cấp phép cần được chủ sở hữu/pháp chế xác nhận riêng.
