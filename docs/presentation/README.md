# Meeting Task Pipeline presentation

Deck chính:

- `meeting-intelligent-project-overview.pptx`
- `meeting-intelligent-project-overview.pdf` (bản đọc/chia sẻ nhanh)

Deck được sinh bằng các shape và text có thể sửa trực tiếp trong PowerPoint;
không dùng ảnh chụp hoặc font nhúng. Build lại bằng:

```powershell
.\.venv\Scripts\python.exe -m pip install python-pptx
.\.venv\Scripts\python.exe scripts\build_project_presentation.py
```

## Phạm vi deck

Deck bao quát:

- bài toán, input/output contract và ranh giới Python-first, AI-last;
- codebase topology và flow V1;
- candidate routing, Meeting Context, Meeting Note và AI fallback;
- Task Ledger, reducer, terminal replay guard và date semantics;
- FastAPI, async jobs, hosting và provider selection;
- Power Automate flow, Unicode-safe headers, Lists upsert, security và review;
- corpus, test strategy, quality snapshot và Gate C;
- đề xuất Evaluation Workbench;
- limitations, roadmap và demo runbook.

## Nguồn số liệu

Số liệu trong deck được chốt theo artifact và tài liệu tại ngày 2026-08-17:

- `docs/project-context.md`;
- `evaluation/current-v1-with-notes.json`;
- `evaluation/current-v1-without-notes.json`;
- `evaluation/live-v1-20260813T034446Z/native-replay-gate-c-fixes-final.json`;
- automated tests trong `backend/tests`.

Khi report canonical thay đổi, cập nhật số liệu trong
`scripts/build_project_presentation.py` rồi build lại deck.
