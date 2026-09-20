# Chạy lớp audio-to-text V2.27 (opt-in)

Lớp này nhận audio rồi chuyển thành transcript có nhãn speaker trước khi chạy
đúng inference V2.27 và job poll hiện có. Đây là tính năng thử nghiệm, không tự
bật theo `PIPELINE_VERSION` và không thay đổi backend V1. Chỉ bật khi đã có
phê duyệt nội bộ cho chi phí và dữ liệu audio.

## Cấu hình server

Chạy từ release worktree; không chạy dịch vụ hay tunnel theo tài liệu này:

```powershell
cd 'C:\Intern AI SPS\meeting-intelligent-release'
$env:POWER_AUTOMATE_API_KEY = '<backend API key>'
$env:V227_AUDIO_TO_TEXT_ENABLED = 'true'
$env:OPENAI_API_KEY = '<OpenAI key chỉ có trên server>'
$env:V227_ARTIFACT_DIRECTORY = 'C:\Intern AI SPS\meeting-intelligent\evaluation\runtime\experimental-distillation-v2\v228-frozen-promotion'
& 'C:\Intern AI SPS\meeting-intelligent\.venv\Scripts\uvicorn.exe' `
  scripts.experimental_distillation.v227_api:app `
  --host 127.0.0.1 --port 8011
```

`V227_AUDIO_TO_TEXT_ENABLED` mặc định tắt. Khi tắt, route trả `404` và không
đọc `OPENAI_API_KEY`, không gọi provider. Key OpenAI chỉ nằm trong biến môi
trường của backend; client và Power Automate chỉ gửi `X-API-Key` của backend.
Thiếu key khi đã bật audio làm app fail closed lúc startup.

OpenAI giới hạn upload ở 25 MB. Route hỗ trợ `.mp3`, `.mp4`, `.mpeg`, `.mpga`,
`.m4a`, `.wav`, `.webm`; body là bytes audio, tên file truyền qua
`X-File-Name` hoặc UTF-8 Base64 trong `X-File-Name-Base64`. Bắt buộc gửi thêm
`X-Meeting-Date: YYYY-MM-DD`; có thể gửi `X-Meeting-Id` và `X-Meeting-Title`.

```powershell
$headers = @{
  'X-API-Key' = $env:POWER_AUTOMATE_API_KEY
  'X-File-Name' = 'meeting.wav'
  'X-Meeting-Id' = 'meeting-2026-09-18'
  'X-Meeting-Title' = 'Họp điều phối'
  'X-Meeting-Date' = '2026-09-18'
}
$job = Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8011/api/v1/meetings/jobs/process-audio' `
  -ContentType 'audio/wav' -InFile '.\meeting.wav' -Headers $headers
$job | ConvertTo-Json
Invoke-RestMethod -Uri ('http://127.0.0.1:8011' + $job.status_url) `
  -Headers @{ 'X-API-Key' = $env:POWER_AUTOMATE_API_KEY }
```

`POST` trả `202` và `job_id`; poll `status_url` đến `succeeded` hoặc `failed`.
Upload audio không được ghi xuống disk. Hệ thống chỉ giữ transcript đã nhận
được trong source private của tenant khi `V227_FEEDBACK_TENANT_ID` được bật,
cùng `raw_upload_sha256`, `transcript_sha256` và `source_modality: "audio"`.
Job status vẫn giữ nguyên public V1 contract; `source_modality` chỉ xuất hiện
trong source và feedback record private để phục vụ human review và trainer.
Hash audio là khóa idempotency; gửi lại cùng bytes và metadata trả cùng job.

## Human review và chi phí

Nhãn speaker của STT có thể sai; reviewer phải sửa speaker, transcript và task
trước khi gửi feedback. Speaker diarization không thay thế human approval và
không được dùng để tự tạo task đã phê duyệt. Feedback vẫn đi qua route private
`POST /api/v1/meetings/jobs/{job_id}/feedback` với `approval_metadata`; xem
[`run-v227-feedback-trainer.md`](run-v227-feedback-trainer.md).

Mỗi audio submit có thể phát sinh phí transcription theo chính sách/model của
OpenAI, ngoài chi phí vận hành inference. Hãy giới hạn quyền bật cờ, theo dõi
usage và không đưa audio nhạy cảm lên provider nếu tenant chưa cho phép.

Thông số provider bám theo [OpenAI Speech-to-Text documentation](https://developers.openai.com/api/docs/guides/speech-to-text):
`gpt-4o-transcribe-diarize`, `response_format=diarized_json` và
`chunking_strategy=auto` (kể cả file ngắn; tài liệu yêu cầu `auto` cho file dài
hơn 30 giây).
