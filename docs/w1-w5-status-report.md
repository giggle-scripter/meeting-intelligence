# Báo cáo trạng thái pipeline W1–W5



Pipeline V1 chạy theo luồng:

```text
Transcript -> parse caption/turn/sentence/clause
-> rule annotation + candidate window
-> rule extraction, AI fallback cho candidate mơ hồ
-> dedup/link/reduce task
-> resolve date, evidence, summary
```

AI fallback đã có trong kiến trúc, nhưng chưa cấu hình provider nên toàn bộ run
thực tế là rule-only.

Baseline 86 case (`v1-baseline-all-rule-only.json`):

| Chỉ số | Kết quả |
|---|---:|
| Case đúng hoàn toàn | 16/86 (18.6%) |
| Case lỗi | 70/86 |
| Task precision | 32.5% |
| Task recall | 48.0% |
| Assignee accuracy trên task đã ghép | 86.5% |
| Due-date accuracy trên task đã ghép | 70.7% |
| Due-date raw-text exact accuracy | 58.6% |

Nguyên nhân chính: 517/703 candidate window cần AI nhưng không có provider;
rule-only tạo 752 event và sinh nhiều task thừa, đặc biệt ở transcript dài.

## Đã xây thêm

- Sửa evaluator: ghép task theo tên/identity trước, sau đó mới đo assignee,
  deadline và status. Sai owner không còn bị báo nhầm thành missing + unexpected.
- Thêm V2 độc lập: event operation, stable task ID, resolver fail-closed,
  reducer tuần tự, primary/context segmentation và contract decision.
- Thêm `PIPELINE_VERSION=v1|v2|shadow`.
  - `v1`: hành vi cũ.
  - `v2`: trả output V2 thử nghiệm.
  - `shadow`: API trả V1, đồng thời chạy V2 và ghi diff.
- Thêm trace opt-in: clauses, candidates, event, state, dates và final tasks.
- Đã chạy đủ 86 case qua Job API ở shadow mode. Kết quả vẫn là baseline vì
  chưa có provider AI: `shadow-api-all-no-provider.json`.

## Đang chuẩn bị chạy

1. Cấu hình OpenAI hoặc Foundry cho process Uvicorn.
2. Chạy W1 qua Job API, kiểm tra AI thực sự được gọi và trace AI hợp lệ.
3. Sửa theo first divergence; ưu tiên false-positive create, wrong target,
   duplicate, rồi mới đến deadline.
4. Chạy lại theo ladder: W1 -> W1+W2 -> W1-W3 -> W1-W4 -> W1-W5.
5. Chỉ chạy full hybrid 86 case sau khi từng wave trước xanh.

## Điều kiện hoàn tất

- Test code pass.
- 86/86 expected output pass trong recorded/deterministic regression.
- Full live-AI report có pipeline/model/prompt/config version, latency,
  provider calls và không đánh đồng provider error với `NO_EVENT`.
- V2 chỉ thay V1 sau khi shadow gate đạt.

## Report liên quan

- `evaluation/v1-baseline-all-rule-only.json`
- `evaluation/shadow-api-all-no-provider.json`
- `evaluation/traces/`
