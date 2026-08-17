# C# API Client

Client đọc file `.txt`, `.vtt` hoặc `.srt`, gọi pipeline API và in JSON output.

```powershell
$env:MEETING_PIPELINE_ENDPOINT='http://127.0.0.1:8000/api/v1/meetings/process'
$env:MEETING_PIPELINE_API_KEY=''
dotnet run --project clients\csharp\MeetingTaskPipeline.Client -- meeting.vtt meeting-001 "Weekly Sync" 2026-07-27
```

Client không ghi transcript ra file và không chứa secret trong source code.
