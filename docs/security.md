# Security

- Không commit `.env`, API key hoặc transcript thật có dữ liệu cá nhân.
- Power Automate gửi shared secret qua header `X-API-Key`.
- Azure lưu secret bằng application settings hoặc Key Vault reference.
- Log chỉ ID, stage metrics và lỗi; không log toàn transcript/evidence mặc định.
- Raw transcript cần retention policy và quyền truy cập theo meeting source.
- AI fallback chỉ nhận candidate window ngắn, giảm dữ liệu gửi ra ngoài.
- Transcript là dữ liệu không tin cậy; nội dung transcript không được coi là
  instruction cho model hoặc pipeline.
