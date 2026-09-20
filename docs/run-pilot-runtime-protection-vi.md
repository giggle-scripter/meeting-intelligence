# Bảo vệ runtime pilot riêng tư

`scripts/pilot_runtime_protection.py` là CLI offline để backup, kiểm tra,
restore và lập báo cáo retention cho đúng một tenant. CLI không khởi động
backend, tunnel, provider hay trainer; retention chỉ báo cáo và không xóa.
Không truyền API key vào lệnh. Dùng đường dẫn SQLite, feedback root, tenant và
thư mục backup cụ thể; thư mục backup phải nằm ngoài checkout source.

## Backup và verify

```powershell
$py = 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\python.exe'
& $py scripts\pilot_runtime_protection.py backup `
  --sqlite-job-path 'D:\MeetingRuntime\meeting-jobs.sqlite3' `
  --feedback-root 'D:\MeetingRuntime\feedback' `
  --tenant-id pilot-tenant `
  --output 'D:\MeetingBackups\pilot-tenant\2026-09-21'

& $py scripts\pilot_runtime_protection.py verify `
  --backup 'D:\MeetingBackups\pilot-tenant\2026-09-21' `
  --tenant-id pilot-tenant
```

Backup dùng SQLite backup API để lấy snapshot nhất quán. Chỉ có
`<tenant>/feedback`, `sources`, `challengers` và `active-v227-pointer.json`
được chọn; symlink, file đặc biệt, file key/provider và nội dung không được
đọc vào báo cáo. `manifest.json` ghi path tương đối, kích thước và SHA-256.
Verify từ chối file thiếu, file thừa, hash sai và symlink.

## Restore có kiểm soát

Chỉ chạy sau khi backend đã dừng và operator đã kiểm tra đúng backup. Đích
SQLite phải mới; thư mục tenant phải mới hoặc đã tồn tại nhưng hoàn toàn rỗng;
`--confirm-restore` và `--backend-stopped` đều bắt buộc.

```powershell
& $py scripts\pilot_runtime_protection.py restore `
  --backup 'D:\MeetingBackups\pilot-tenant\2026-09-21' `
  --sqlite-job-path 'D:\MeetingRuntime\restored-jobs.sqlite3' `
  --feedback-root 'D:\MeetingRuntime\restored-feedback' `
  --tenant-id pilot-tenant `
  --confirm-restore --backend-stopped
```

Lệnh verify toàn bộ manifest trước, chép cả cây tenant và SQLite vào vùng tạm,
verify lại rồi mới promote bằng rename nguyên tử khi hệ điều hành hỗ trợ. Nếu
copy hoặc promote lỗi, vùng tạm được dọn và không để lại tenant hay SQLite
restore dở dang; thư mục tenant rỗng có sẵn được giữ nguyên. CLI không xóa dữ
liệu tenant có nội dung và từ chối đích SQLite đã có.

## Retention report

```powershell
& $py scripts\pilot_runtime_protection.py retention `
  --sqlite-job-path 'D:\MeetingRuntime\meeting-jobs.sqlite3' `
  --feedback-root 'D:\MeetingRuntime\feedback' `
  --tenant-id pilot-tenant `
  --cutoff '2026-08-01T00:00:00+00:00'
```

Báo cáo trả về số file, dung lượng và candidate cũ hơn cutoff theo path tương
đối. Không có thao tác xóa tự động. Wrapper PowerShell tương đương là
`scripts\pilot_runtime_protection.ps1`; truyền `-PythonExecutable` nếu Python
không ở đường dẫn mặc định.
