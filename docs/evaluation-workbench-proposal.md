# Evaluation Workbench: đề xuất cho Meeting Task Pipeline

## Kết luận

Nên phát triển một **Evaluation Workbench nhỏ**, nhưng không nên thay comparator
canonical bằng một hệ thống “AI chấm AI”. Comparator deterministic hiện tại vẫn
là gate bắt buộc và là nguồn số liệu precision, recall, field accuracy.

Workbench bổ sung phần mà report JSON còn thiếu: căn chỉnh task, xem evidence,
phân loại lỗi và ghi nhận quyết định review. Mục tiêu là giúp con người đánh giá
đúng và nhanh hơn, không phải làm metric đẹp hơn.

## Ba lớp đánh giá

```text
Pipeline output
  -> deterministic comparator (blocking gate)
  -> semantic alignment suggestion (non-blocking)
  -> human adjudication (versioned decision)
```

1. **Deterministic comparator**
   - So expected/actual theo contract đã review.
   - Tính missing, unexpected, field mismatch, precision, recall và field
     accuracy.
   - Kết quả này là số canonical để pass/fail gate.
2. **Semantic alignment suggestion**
   - Gợi ý actual task nào có thể tương ứng expected task nào khi câu chữ khác
     nhau.
   - Không tự đổi ground truth, không tự bỏ false positive và không được dùng làm
     blocking gate.
3. **Human adjudication**
   - Người review xem transcript, Meeting Note, evidence, event ledger và trace.
   - Quyết định giữ expected, sửa expected sau review, hoặc tạo issue cho rule,
     linker, reducer, date resolver hay provider.

## MVP nên có

- Chọn run/case/wave/label/provider/note mode.
- Hiển thị expected và actual cạnh nhau.
- Căn hàng task theo deterministic match; semantic matcher chỉ đề xuất các cặp
  còn lại và hiển thị confidence.
- Với mỗi mismatch, mở được transcript clause, evidence, event ledger và task
  state cuối.
- Phân loại lỗi chuẩn: false create, missed create, wrong identity, wrong owner,
  wrong date, mutation target, cancellation/rejection, recap, note grounding,
  provider/schema và evaluator alignment.
- Ghi reviewer, timestamp, quyết định và lý do; không ghi đè trực tiếp
  `expected_output.json`.
- Export file review patch/audit để người phụ trách corpus duyệt riêng.
- Dashboard theo slice: wave, semantic label, transcript length, with/without
  note, rule/AI, model, prompt version và pipeline version.

## Không nên làm trong MVP

- Không dùng một điểm “LLM correctness” duy nhất để pass/fail.
- Không cho AI tự sửa expected output.
- Không coi Meeting Note là complete snapshot.
- Không che unresolved windows hoặc provider errors bằng semantic similarity.
- Không xây multi-user platform lớn trước khi taxonomy lỗi và workflow review
  ổn định.

## Dữ liệu tối thiểu của một review record

```json
{
  "run_id": "...",
  "case_id": "...",
  "pipeline_version": "v1",
  "context_mode": "assist",
  "note_mode": "with-notes",
  "provider": "openai",
  "model": "gpt-5-mini",
  "prompt_version": "...",
  "expected_task_index": 0,
  "actual_task_index": 1,
  "suggested_match_score": 0.86,
  "decision": "MATCH_CONFIRMED",
  "error_category": "WRONG_DEADLINE",
  "reviewer": "...",
  "reason": "...",
  "created_at": "..."
}
```

## Thứ tự triển khai đề xuất

1. Chuẩn hóa error taxonomy và review record.
2. Làm viewer read-only cho report JSON và trace.
3. Thêm task alignment suggestion, luôn hiển thị lý do và confidence.
4. Thêm adjudication + export audit patch.
5. Chỉ sau khi workflow ổn định mới thêm persistence, auth và collaboration.

Với chất lượng canonical hiện tại, lợi ích lớn nhất đến từ việc hiểu nhanh
`unexpected`, task identity và long-distance mutation; chỉ làm comparator mới sẽ
không giải quyết được ba nhóm lỗi này.
