# Deployment

## Azure Functions

Backend đã có `backend/function_app.py` và `backend/host.json`.

1. Tạo Python Azure Function App.
2. Deploy nội dung thư mục `backend/`.
3. Cấu hình application settings theo `.env.example`.
4. Gọi `/health` để kiểm tra.
5. Cấu hình Power Automate HTTP action gọi
   `/api/v1/meetings/jobs/process-file`, sau đó poll status URL đến khi job kết
   thúc.

Không commit `local.settings.json`; dùng `backend/local.settings.example.json` làm
mẫu.

Để bật AI-last trực tiếp, cấu hình
`AZURE_AI_FOUNDRY_CHAT_ENDPOINT`, `AZURE_AI_FOUNDRY_API_KEY` và
`AZURE_AI_FOUNDRY_MODEL`. Nếu để trống, pipeline vẫn chạy rule-only và trả
`unresolved_window_ids`.

## Container/App Service

```powershell
docker build -t meeting-task-pipeline .
docker run --rm -p 8000:8000 --env-file .env meeting-task-pipeline
```

Container dùng cùng FastAPI app, phù hợp Azure Web App for Containers hoặc Azure
Container Apps. Chỉ expose HTTPS ở môi trường triển khai.

## Verification

```text
GET /health
POST /api/v1/transcripts/preprocess
POST /api/v1/meetings/process
POST /api/v1/meetings/jobs/process
POST /api/v1/meetings/jobs/process-file
GET /api/v1/meetings/jobs/{job_id}
```

Chạy preprocess trước để xác nhận parser/dedup, sau đó mới chạy full pipeline.
