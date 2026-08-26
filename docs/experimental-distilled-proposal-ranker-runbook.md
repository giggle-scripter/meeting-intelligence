# Runbook thực thi: experimental distilled proposal ranker V1

Tài liệu này là đặc tả thực thi cho code bot. Code bot phải làm đúng thứ tự,
không tự đổi kiến trúc, metric, split, threshold, model, đường dẫn hoặc gate. Nếu
một điều kiện `STOP` xảy ra, bot phải dừng, ghi báo cáo nguyên nhân và không tự
nghĩ ra phương án thay thế.

Mục tiêu của experiment là tăng task-identity F1 thực sự bằng hai model local:

1. một neural action-span extractor không bị giới hạn bởi regex/span lattice V3;
2. một multilingual cross-encoder xếp hạng proposal theo action, authority,
   owner, deadline và negative evidence.

LLM teacher là nguồn pseudo-label tùy chọn để distill thêm quan hệ và hard
negative. Experiment vẫn phải chạy được ở `cache-only` mà không gọi provider.

Đây không phải PR và không phải production integration. Tất cả code nằm trên
branch `experimental/distilled-proposal-ranker-v1`; bot tự commit từng checkpoint
nhưng không push, không mở PR và không merge.

---

## 1. Snapshot và giới hạn tuyên bố

Snapshot dùng để viết runbook này:

- base branch: `dev`;
- base commit: `a04e815`;
- baseline Q2: task-identity F1 `0.5338`, `166` matched, `111` missing,
  `179` unexpected;
- evidence: `277/277` task đã `HUMAN_CONFIRMED` và source-grounded;
- evidence nằm trong `78` meeting có ít nhất một expected task;
- `8` meeting còn lại là zero-task negative cases, nên full corpus vẫn là `86`;
- exact-span lattice recall: `50.2%`;
- logistic scorer hiện tại: train F1 `0.195`, W4 `0.100`, W5 `0.022`;
- `data/quality/task-evidence-v2.jsonl` là evidence canonical đã commit;
- `evaluation/runtime/pr38-traces` là trace source tương ứng;
- `evaluation/runtime/pr29-q2-traces` là trace baseline Q2 khóa;
- W1-W5 đều đã được xem/tune ở các vòng trước.

Do W1-W5 không còn blind, báo cáo mới chỉ được gọi là **development nested-CV
estimate**. Không được gọi nó là blind-test, production-proof hoặc unbiased
generalization. Không tạo thêm human-labelled data theo yêu cầu hiện tại.

### 1.1 Definition of done

Experiment chỉ được kết luận `PROMISING` khi đồng thời đạt:

- OOF exact action-span recall `>= 0.70`;
- OOF token-level span F1 `>= 0.80`;
- OOF proposal recall@30 `>= 0.85`;
- OOF task-identity precision `>= 0.48`;
- OOF task-identity recall `>= 0.63`;
- OOF task-identity F1 `>= 0.57`;
- cải thiện tuyệt đối so với Q2 ít nhất `0.0362` F1;
- mọi safety counter ở mục 16 bằng `0`;
- không outer fold nào thấp hơn baseline fold tương ứng quá `0.03` F1;
- kết quả ensemble ba seed đạt gate, không chỉ một seed tốt nhất.

Nếu thiếu bất kỳ gate nào, kết luận là `NOT_READY`. Bot vẫn commit code và báo
cáo experiment, nhưng không nối model vào runtime.

Report có hai field riêng:

- `readiness`: `PROMISING` hoặc `NOT_READY`;
- `decision`: mã nguyên nhân chi tiết ở mục 16.

---

## 2. Luật bất biến

Các luật này áp dụng cho mọi file, script, test, prompt và artifact.

### 2.1 Git và phạm vi thay đổi

- Chỉ làm trên `experimental/distilled-proposal-ranker-v1`.
- Không sửa, reset, rebase, force-update hoặc xóa `dev`.
- Không dùng `git reset --hard`, `git clean`, `git checkout --`, `git restore .`,
  `Remove-Item -Recurse` hoặc lệnh xóa hàng loạt tương đương.
- Không push, không tạo PR, không merge.
- Không amend commit đã tạo.
- Không dùng `git add -A` hoặc `git add .`; chỉ add đường dẫn explicit của stage.
- Không đưa file runtime, trace, cache, checkpoint, model weights, secret hoặc
  transcript raw mới vào commit.
- Nếu xuất hiện thay đổi tracked ngoài scope, `STOP_UNRELATED_TRACKED_CHANGE`.
- Nếu branch đã tồn tại nhưng không trỏ đúng base/chuỗi commit experiment,
  `STOP_EXISTING_BRANCH_CONFLICT`; không xóa branch để làm lại.

### 2.2 Data và leakage

- Không sửa bất kỳ `data/validation/**/expected_output.json` nào.
- Không sửa transcript, metadata hoặc Meeting Note để làm metric tăng.
- Không sửa `data/quality/task-evidence-v2.jsonl`.
- Không dùng `expected task name`, expected assignee, expected due date hoặc final
  expected task object làm runtime feature, teacher input hoặc candidate text.
- Human evidence của outer validation fold chỉ dùng để chấm điểm fold đó. Nó
  không được dùng để train, chọn threshold, chọn hyperparameter, tạo prompt,
  mine negative hoặc sửa candidate của fold đó.
- Split phải theo `meeting/case_id`; không được split theo clause, span, candidate
  hoặc row.
- Mọi preprocessing có state, sampler weight, calibration, threshold và model
  selection phải fit trên outer-train hoặc inner-train tương ứng.
- Không xem lỗi outer fold rồi sửa rule/model và chạy lại cùng protocol. Nếu cần
  iteration khác, tạo protocol V2 với hash mới và ghi rõ đây là một experiment
  mới đã dùng kết quả V1 để thiết kế.
- Gold span chỉ được inject vào candidate pool của training để model có positive.
  Không inject gold vào candidate pool dùng để tính recall hoặc outer validation.

### 2.3 Authority và task semantics

- Không hardcode case ID, wave ID, exact transcript wording hoặc task name vào
  model logic/prompt/rule.
- Không dùng Meeting Note như complete snapshot.
- Meeting Note không tự tạo authority. Note chỉ được dùng khi claim đã grounded
  vào transcript clause; transcript chronology luôn thắng.
- Không xóa raw clause, safety clause hoặc negative clause để giảm false positive.
- CREATE phải có action span và authority evidence source-grounded.
- Question, suggestion, hypothetical, brainstorm, progress update, past-completed,
  future discussion, admin follow-up, rejection và cancellation không được tạo
  CREATE nếu không có acceptance/commitment hợp lệ sau đó.
- UPDATE/REFERENCE không được mint task identity mới.
- Mutation không được biến thành CREATE chỉ vì có action verb.
- Recap không được tạo duplicate của task đã có.
- Không merge sibling tasks chỉ vì lexical/embedding similarity cao.
- Một clause có hai action độc lập phải giữ hai proposal độc lập.
- AI/model không tính calendar date, không tạo clause ID, date mention ID, event
  ID hoặc task ID, và không quyết định final ledger state.
- Deadline chỉ tham chiếu `date_mention_id` đã tồn tại. Python resolver hiện tại
  tiếp tục là nơi duy nhất tính ngày.
- Owner phải grounded vào speaker/owner span hoặc typed relation; không suy ra từ
  expected final state.
- Public output hiện tại phải giữ backward compatible; experiment không được
  sửa serializer hoặc API response.

### 2.4 Provider, privacy và cost

- Default teacher mode là `cache-only`; mode này không được gửi network request.
- Chỉ gọi provider khi đồng thời có:
  `DISTILL_TEACHER_MODE=provider`, `DISTILL_ALLOW_PROVIDER_CALLS=1`, API key,
  model explicit, `DISTILL_MAX_CALLS`, `DISTILL_MAX_INPUT_CHARS` và
  `DISTILL_MAX_ESTIMATED_USD` là số dương.
- Không có pricing đầy đủ thì provider mode phải fail closed trước call đầu tiên.
- `--dry-run` chỉ xuất manifest/payload hash, không xuất prompt chứa transcript.
- Request phải đặt `store=false` nếu provider hỗ trợ.
- Không log API key, authorization header, raw full prompt hoặc raw full response.
- Teacher cache là runtime-sensitive, nằm dưới
  `evaluation/runtime/experimental-distillation-v1/teacher-cache/` và không commit.
- Không lưu chain-of-thought/free-form rationale. Chỉ lưu decision, grounded
  spans, confidence và reason code trong allowlist.
- Mỗi response phải qua strict schema và source-grounding validator trước khi
  trở thành pseudo-label.

### 2.5 Test discipline

- Mỗi positive behavior phải có ít nhất một negative test tương ứng.
- Test không được phụ thuộc network, GPU hoặc model download.
- Model tests dùng tiny fake tokenizer/model hoặc fixture local deterministic.
- Full `pytest backend/tests -q` phải pass trước mọi commit.
- `git diff --check` phải pass trước mọi commit.

---

## 3. Cleanup và tạo branch

Phần này phải chạy trước khi sửa source code.

### 3.1 Kiểm tra repo và base

Chạy từ repo root bằng PowerShell:

```powershell
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path ".").Path
git rev-parse --show-toplevel
git fetch --prune origin
git switch dev
$localDev = (git rev-parse dev).Trim()
$remoteDev = (git rev-parse origin/dev).Trim()
$expectedBase = "a04e815"
$resolvedExpectedBase = (git rev-parse $expectedBase).Trim()
if ($localDev -ne $remoteDev) { throw "STOP_DEV_NOT_EQUAL_ORIGIN_DEV" }
if ($localDev -ne $resolvedExpectedBase) { throw "STOP_BASE_MOVED_FROM_A04E815" }
```

Không chạy `git pull` vì base đã được khóa bằng SHA. Nếu base đã đổi, bot dừng và
báo đúng ba SHA: `dev`, `origin/dev`, `a04e815`; không tự rebase runbook.

### 3.2 Inventory artifact runtime, không xóa

Các file `pr30-*` đến `pr40-*`, review queue và các bản
`task-evidence-reviewed*.jsonl` là audit evidence, không phải rác để xóa. Bot chỉ
lập inventory, checksum và ẩn chúng khỏi local Git status bằng
`.git/info/exclude`. `.git/info/exclude` là local-only và không được commit.

```powershell
$preflightDir = Join-Path $repoRoot "evaluation/runtime/experimental-distillation-v1/preflight"
New-Item -ItemType Directory -Force -Path $preflightDir | Out-Null

$untrackedRuntime = @(
  git ls-files --others --exclude-standard -- evaluation/runtime |
    Where-Object { $_ -and $_.Trim() }
)
$untrackedRuntime | Set-Content -LiteralPath (Join-Path $preflightDir "untracked-runtime-files.txt") -Encoding utf8

$hashRows = foreach ($relative in $untrackedRuntime) {
  $absolute = Join-Path $repoRoot $relative
  if (Test-Path -LiteralPath $absolute -PathType Leaf) {
    $item = Get-Item -LiteralPath $absolute
    $hash = Get-FileHash -LiteralPath $absolute -Algorithm SHA256
    [pscustomobject]@{
      path = $relative
      bytes = $item.Length
      sha256 = $hash.Hash.ToLowerInvariant()
      last_write_utc = $item.LastWriteTimeUtc.ToString("o")
    }
  }
}
$hashRows | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $preflightDir "runtime-file-hashes.json") -Encoding utf8

$excludePath = (git rev-parse --git-path info/exclude).Trim()
$existingExclude = if (Test-Path -LiteralPath $excludePath) { @(Get-Content -LiteralPath $excludePath) } else { @() }
foreach ($relative in $untrackedRuntime) {
  $entry = "/" + ($relative -replace "\\", "/")
  if ($existingExclude -notcontains $entry) {
    Add-Content -LiteralPath $excludePath -Value $entry -Encoding utf8
    $existingExclude += $entry
  }
}
```

Sau đó:

```powershell
$remaining = @(git status --porcelain=v1 --untracked-files=all)
$allowedGuide = "?? docs/experimental-distilled-proposal-ranker-runbook.md"
$unexpected = @($remaining | Where-Object { $_ -ne $allowedGuide })
if ($unexpected.Count -ne 0) {
  $unexpected | Set-Content -LiteralPath (Join-Path $preflightDir "unexpected-worktree-status.txt") -Encoding utf8
  throw "STOP_WORKTREE_NOT_CLEAN_AFTER_LOCAL_EXCLUDES"
}
```

File runbook này được phép là untracked tại thời điểm handoff và sẽ được add
explicit trong Commit 1. Ngoài đúng file đó, không có thay đổi nào khác được
phép còn lại.

Không move, rename hoặc delete artifact runtime. Không add toàn bộ
`evaluation/runtime` vào `.gitignore`, vì thư mục này hiện có report baseline đã
track.

### 3.3 Tạo môi trường Python

```powershell
if (-not (Test-Path -LiteralPath ".venv/Scripts/python.exe")) {
  py -3.11 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,ml-train]"
& .\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Nếu Python không phải `>=3.11`, dependency install fail, hoặc full tests fail:
`STOP_BASELINE_ENVIRONMENT_FAILURE`. Không sửa production test để vượt gate.

### 3.4 Xác minh canonical evidence

Không dùng các file review runtime làm training input. Dùng duy nhất file đã
commit `data/quality/task-evidence-v2.jsonl` sau khi gate lại với trace PR38:

```powershell
& .\.venv\Scripts\python.exe scripts\verify_task_evidence.py `
  --evidence data\quality\task-evidence-v2.jsonl `
  --traces evaluation\runtime\pr38-traces `
  --output evaluation\runtime\experimental-distillation-v1\preflight\task-evidence-gate.json
```

Parse JSON và assert chính xác:

- `passed == true`;
- `expected_task_count == 277`;
- `reviewed_task_count == 277`;
- `trace_count == 78`;
- `errors == []`.

`verify_task_evidence.py` chỉ load trace cho case xuất hiện trong evidence rows.
Vì evidence là per-expected-task, tám meeting có zero expected task không có row
và không được tính trong field `trace_count` này. Do đó `78` là giá trị đúng của
**evidence trace count**, không phải thiếu tám trace.

Sau đó hash các input khóa:

```powershell
Get-FileHash data\quality\task-evidence-v2.jsonl -Algorithm SHA256
Get-FileHash data\quality\proposal-span-supervision-v3.jsonl -Algorithm SHA256
git rev-parse HEAD
```

Bot còn phải tạo một trace manifest bằng cách enumerate đúng 86 file
`evaluation/runtime/pr38-traces/*-v1-*.json`, sort theo relative path, lưu
`path`, `bytes`, `sha256`, rồi hash canonical JSON của cả manifest. Thiếu/thừa
trace, duplicate `meeting_id`, hoặc trace `meeting_id` không trùng `case_id` là
`STOP_TRACE_INVENTORY_INVALID`.

Phải assert thêm:

- distinct evidence case IDs `== 78`;
- full validation case IDs `== 86`;
- zero-task case IDs `== 8`;
- `evidence_case_ids` là subset của `validation_case_ids`;
- `validation_case_ids - evidence_case_ids` chính xác là tập case có
  `expected_output.tasks == []`;
- cả 86 validation case IDs đều có đúng một PR38 trace.

Nếu evidence gate trả `trace_count=78` và full PR38 manifest có 86 trace, preflight
phải tiếp tục. Không được phát `STOP_TASK_EVIDENCE_TRACE_COUNT_78`.

Lặp lại cùng kiểm tra cho `evaluation/runtime/pr29-q2-traces`. Hai trace set phải
cùng đủ 86 `meeting_id`. Q2 baseline aggregate từ trace/output phải đúng `277`
expected, `345` actual, `166` matched, `111` missing, `179` unexpected và F1
`0.533762...` (display `0.5338`). Nếu không đúng, `STOP_Q2_BASELINE_DRIFT`.

Corpus supervision hiện tại phải có SHA256
`f22bb5a0fa52b8b33333050be15700b62383bb712085033192988d7f36aaebef`.
Nếu khác, `STOP_SUPERVISION_CORPUS_DRIFT`.

#### Tiếp tục sau stop report `STOP_TASK_EVIDENCE_TRACE_COUNT_78`

Stop report này được tạo do phiên bản runbook cũ nhầm evidence-case count với
full-corpus count. Giữ nguyên report làm audit artifact; không xóa hoặc sửa nó.
Nếu trạng thái hiện tại đồng thời là:

- branch `dev` tại `a04e815` và bằng `origin/dev`;
- baseline tests pass;
- evidence gate `passed=true`, `277/277`, `trace_count=78`;
- PR38 trace manifest đủ 86 unique meeting IDs;
- PR29 Q2 trace manifest đủ 86 unique meeting IDs;
- tám case không có evidence đều có `expected_output.tasks == []`;
- không có thay đổi tracked ngoài scope;

thì bot được resume trực tiếp từ phần trace-manifest/Q2 baseline assertions ở
mục này, sau đó chạy mục 3.5. Không chạy lại human review, không tạo evidence
mới và không coi tám zero-task case là missing evidence.

### 3.5 Tạo branch

```powershell
if (git show-ref --verify --quiet refs/heads/experimental/distilled-proposal-ranker-v1) {
  throw "STOP_EXISTING_BRANCH_CONFLICT"
}
git switch -c experimental/distilled-proposal-ranker-v1 a04e815
git status --short --branch
```

Expected status chỉ có branch header và đúng một untracked file:
`docs/experimental-distilled-proposal-ranker-runbook.md`.

---

## 4. Cấu trúc file bắt buộc

Không đặt experimental inference vào `backend/app/pipeline.py`, router, ledger,
API hoặc serializer. Tạo đúng cấu trúc sau:

```text
experiments/
  __init__.py
  distilled_proposal_ranker/
    __init__.py
    README.md
    config/
      protocol-v1.json
    contracts.py
    hashing.py
    inventory.py
    folds.py
    trace_reader.py
    context_builder.py
    teacher_client.py
    teacher_prompt.py
    teacher_validator.py
    teacher_consensus.py
    span_dataset.py
    span_model.py
    span_decode.py
    candidate_pool.py
    hard_negatives.py
    reranker_dataset.py
    reranker_model.py
    calibration.py
    crossfit.py
    replay.py
    metrics.py
    reports.py
scripts/
  experimental_distillation/
    preflight.py
    build_folds.py
    export_teacher_payloads.py
    generate_teacher_labels.py
    build_span_dataset.py
    train_span_student.py
    build_reranker_dataset.py
    train_proposal_reranker.py
    run_nested_crossfit.py
    evaluate_experiment.py
backend/tests/experimental/
  __init__.py
  test_distillation_contracts.py
  test_distillation_folds.py
  test_distillation_teacher_validator.py
  test_distillation_span_dataset.py
  test_distillation_span_decode.py
  test_distillation_candidate_pool.py
  test_distillation_hard_negatives.py
  test_distillation_reranker_dataset.py
  test_distillation_calibration.py
  test_distillation_replay_safety.py
  test_distillation_reports.py
docs/experiments/
  distilled-proposal-ranker-v1-results.md
```

Runtime outputs bắt buộc nằm dưới:

```text
evaluation/runtime/experimental-distillation-v1/
  preflight/
  manifests/
  folds/
  teacher-payloads/
  teacher-cache/
  datasets/
  checkpoints/
  oof/
  reports/
```

Model weights nằm dưới:

```text
artifacts/models/experimental-distillation-v1/
```

Hai root output trên đã thuộc vùng không commit. Mọi script phải từ chối output
path nằm ngoài hai root này, trừ report Markdown cuối cùng đã aggregate và không
chứa transcript.

---

## 5. Protocol V1 phải khóa trước khi train

Tạo `experiments/distilled_proposal_ranker/config/protocol-v1.json` với đúng các
giá trị logic sau. JSON phải được Pydantic validate với `extra="forbid"`.

```json
{
  "schema_version": "distilled-proposal-ranker-protocol-v1",
  "base_commit": "a04e815",
  "evidence_path": "data/quality/task-evidence-v2.jsonl",
  "trace_path": "evaluation/runtime/pr38-traces",
  "baseline_trace_path": "evaluation/runtime/pr29-q2-traces",
  "expected_cases": 86,
  "expected_evidence_cases": 78,
  "expected_zero_task_cases": 8,
  "zero_task_case_ids": [
    "W2-SHORT-C2-N0-IT-BRST-006",
    "W2-SHORT-C2-N0-OPS-BRST-024",
    "W2-SHORT-C2-N1-IT-REJT-010",
    "W2-SHORT-C2-N2-IT-PAST-026",
    "W2-SHORT-C2-N2-OPS-PAST-008",
    "W2-SHORT-C3-N0-PROD-CANC-027",
    "W2-SHORT-C3-N0-SW-CANC-009",
    "W2-SHORT-C3-N1-PROD-SUGG-007"
  ],
  "expected_tasks": 277,
  "baseline_expected": 277,
  "baseline_actual": 345,
  "baseline_matched": 166,
  "outer_folds": 5,
  "inner_folds": 4,
  "fold_seed": 1729,
  "training_seeds": [17, 29, 43],
  "span_model_name": "FacebookAI/xlm-roberta-base",
  "reranker_model_name": "FacebookAI/xlm-roberta-base",
  "span_max_length": 384,
  "reranker_max_length": 512,
  "local_context_before": 3,
  "local_context_after": 5,
  "max_context_clauses": 30,
  "max_context_characters": 12000,
  "max_span_candidates_per_clause": 3,
  "max_proposals_per_meeting": 60,
  "proposal_recall_k": 30,
  "teacher_mode_default": "cache-only",
  "teacher_prompt_versions": ["teacher-a-v1", "teacher-b-v1"],
  "label_weights": {
    "human_confirmed": 1.0,
    "teacher_consensus": 0.35,
    "teacher_single": 0.15,
    "deterministic_hard_negative": 0.25,
    "weak_regex": 0.05
  },
  "span_learning_rates": [0.00002, 0.00003],
  "reranker_learning_rates": [0.00001, 0.00002],
  "weight_decay": 0.01,
  "warmup_ratio": 0.1,
  "max_epochs": 8,
  "early_stopping_patience": 2,
  "gradient_clip_norm": 1.0,
  "threshold_grid": [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90],
  "gates": {
    "span_exact_recall": 0.70,
    "span_token_f1": 0.80,
    "proposal_recall_at_30": 0.85,
    "task_identity_precision": 0.48,
    "task_identity_recall": 0.63,
    "task_identity_f1": 0.57,
    "max_fold_f1_regression": 0.03,
    "safety_violations": 0
  }
}
```

Bot không được thêm hyperparameter grid khác sau khi thấy outer result. Batch
size là resource setting, không phải tuned hyperparameter:

- CUDA: train batch `16`, eval batch `32`, FP16 nếu GPU support;
- CPU: train batch `4`, eval batch `8`, FP32;
- gradient accumulation phải đưa effective train batch về `16`;
- không silent fallback sang model khác;
- nếu model không download/cache được, `STOP_MODEL_UNAVAILABLE`.

Mỗi process training phải set seed cho `random`, NumPy, PyTorch CPU và tất cả
CUDA devices; đặt `PYTHONHASHSEED`, `CUBLAS_WORKSPACE_CONFIG=:4096:8`,
`torch.use_deterministic_algorithms(True)` và tắt cuDNN benchmark. Nếu một op
không deterministic, fail rõ tên op; không đổi sang `warn_only`.

Commit protocol trước mọi training run. Sau commit, report phải lưu SHA256 của
file protocol; mọi stage phải từ chối resume nếu hash khác.

---

## 6. Contract dữ liệu

Tất cả model Pydantic dùng `ConfigDict(extra="forbid")`. Các record JSONL phải
có `schema_version`, stable ID và `input_hash`.

### 6.1 Clause target

Mỗi action-span example gồm:

- `case_id`, `fold_id`;
- `target_clause_id`, `target_clause_text`;
- bounded chronological context chứa clause ID, speaker và raw text;
- `target_start_in_context`, `target_end_in_context`;
- danh sách gold spans chỉ khi record thuộc training/evaluation label view;
- source label và weight;
- SHA256 của canonical JSON input.

Model input không chứa expected task name. Gold span giữ offset theo
`Clause.text_raw`; context builder phải có mapping hai chiều giữa raw clause
offset và tokenizer offset.

### 6.2 Span prediction

Mỗi prediction gồm:

- `case_id`, `target_clause_id`;
- raw `start`, `end`, `text` trong target clause;
- `start_logit`, `end_logit`, `span_score`, `no_action_score`;
- `model_name`, `outer_fold`, `training_seed`, `checkpoint_hash`;
- `input_hash`.

Validator reject nếu:

- clause ID không tồn tại;
- `start < 0`, `end <= start` hoặc `end > len(text_raw)`;
- returned text khác exact substring;
- span vượt target clause;
- span chỉ là punctuation/whitespace;
- duplicate exact span trong cùng case.

### 6.3 Proposal record

Mỗi proposal có:

- stable `proposal_id = SHA256(case_id, action span, cluster identity)`;
- `kind` trong `CREATE`, `UPDATE`, `REFERENCE`, `DROP`, `UNRESOLVED`;
- primary action span;
- authority, acceptance, owner, deadline support refs;
- negative refs;
- typed relation list;
- deterministic V3 score/reasons;
- neural span score;
- source type `LATTICE`, `NEURAL`, hoặc `BOTH`;
- no gold-derived field.

Reranker output chỉ có `create_score` và allowed reason diagnostics. Nó không
tạo final task object.

### 6.4 Teacher response

Teacher chỉ được chọn trong bounded payload và trả:

- `payload_id`, `prompt_version`;
- list record gồm `proposal_id`, `decision`, `action_span_ref`,
  `authority_clause_ids`, `owner_clause_id`, `deadline_mention_id`,
  `confidence`, `reason_codes`;
- `decision` thuộc `CREATE`, `UPDATE`, `REFERENCE`, `DROP`, `UNRESOLVED`;
- `reason_codes` chỉ thuộc:
  `DIRECT_ASSIGNMENT`, `SELF_COMMITMENT`, `QUESTION_ACCEPTED`,
  `ASSIGNMENT_ACKNOWLEDGED`, `ACTION_CONTINUATION`, `OWNER_CONTINUATION`,
  `DEADLINE_CONTINUATION`, `SUGGESTION_ONLY`, `QUESTION_UNACCEPTED`,
  `HYPOTHETICAL`, `PAST_COMPLETED`, `PROGRESS_ONLY`, `RECAP_DUPLICATE`,
  `MUTATION_ONLY`, `REFERENCE_ONLY`, `INSUFFICIENT_EVIDENCE`,
  `SIBLING_AMBIGUITY`.

Teacher không được trả task name, resolved date, new clause/date/task ID,
free-form rationale hoặc field ngoài schema.

---

## 7. Fold builder và chống leakage

Tạo một fold manifest duy nhất cho 86 meeting.

### 7.1 Thuật toán fold deterministic

1. Tạo case table từ `data/validation`: `case_id`, wave, length class, expected
   task count, clause count và hashes của metadata/transcript/expected output.
2. `case_id` là group bất khả phân.
3. Sort cases theo expected task count giảm dần, rồi
   `sha256("1729:" + case_id)` tăng dần.
4. Với mỗi case, chọn fold làm nhỏ nhất tuple:
   `(same_wave_count, same_length_count, total_expected_tasks, total_cases,
   fold_id)`.
5. Assert mỗi case xuất hiện đúng một outer fold, không overlap và union là 86.
6. Với từng outer-train, tạo bốn inner folds bằng cùng thuật toán và seed
   `1729 + outer_fold_id + 1`.
7. Ghi fold manifest kèm dataset/evidence/trace/protocol hashes.

Wave/length chỉ dùng để cân bằng split; không đưa wave/case ID vào model feature.
Tám zero-task meeting phải được phân phối giữa các fold và tuyệt đối không bị bỏ
do thiếu evidence row. Fold manifest phải ghi `expected_task_count=0` cho chúng.

### 7.2 Luật nested cross-fit

Với mỗi outer fold:

1. `outer_valid` bị khóa hoàn toàn.
2. Trên `outer_train`, train span model theo bốn inner folds và tạo inner-OOF
   span candidates cho toàn bộ `outer_train`.
3. Dùng inner-OOF candidates để train/calibrate reranker; không dùng span
   predictions in-sample.
4. Chọn learning rate, epoch và threshold chỉ bằng aggregate inner-OOF metrics.
5. Retrain span model trên toàn `outer_train` bằng hyperparameter đã chọn.
6. Predict neural spans trên `outer_valid`; không inject gold.
7. Retrain reranker trên toàn outer-train candidate data; predict outer-valid.
8. Lưu outer predictions một lần. Code không có option `--rerun-fold-after-inspect`.

Ba training seed dùng cùng fold manifest. Ensemble score là arithmetic mean của
ba calibrated probabilities. Model selection/gate dùng ensemble OOF; report vẫn
phải có mean, standard deviation và từng seed.

---

## 8. Action-span student

### 8.1 Candidate ceiling phải bị phá đúng cách

Không chỉ chạy span model trên V3 seed. Span extractor phải target **mọi raw
transcript clause**. Mỗi target dùng context `-3/+5` clause, sau đó cắt theo caps
30 clauses/12,000 characters; target clause không bao giờ bị cắt.

Điều này cho phép model sinh exact span ngoài lattice 50.2%, nhưng prediction vẫn
bị giới hạn trong một source clause có thật.

### 8.2 Label construction

- Positive chính: 277 human-confirmed `action_evidence`, weight `1.0`, nhưng chỉ
  ở train fold.
- Clause không chứa gold action trong train fold là no-action candidate; không
  mặc định coi tất cả là strong negative.
- Với tám zero-task meeting: mọi clause vẫn được đưa qua span inference và metric.
  Trong training, chỉ clause có deterministic negative reason mới nhận hard-
  negative weight `0.25`; clause còn lại là unlabeled/no-action weight thấp,
  không tự nâng toàn meeting thành strong negative.
- Strong deterministic hard negative chỉ khi có reason code rõ như question
  unaccepted, suggestion-only, past completed, recap, mutation-only;
  weight `0.25`.
- Weak regex negative/positive weight `0.05`.
- Teacher consensus label weight `0.35`; single valid teacher label `0.15`.
- Human label luôn override teacher/heuristic nếu cùng span/clause mâu thuẫn.
- Không pseudo-label outer-valid bằng teacher rồi dùng để train model của fold đó.

BIO tagging dùng fast tokenizer `offset_mapping`. Special token và context token
ngoài target clause có label `-100`. Nếu một clause có nhiều gold spans không
overlap, tag tất cả. Nếu spans overlap/nested, duplicate example theo từng span
và gắn `overlap_group_id`; không merge boundary.

### 8.3 Architecture và loss

- Backbone: `FacebookAI/xlm-roberta-base`.
- Head 1: token BIO classifier (`O`, `B-ACTION`, `I-ACTION`).
- Head 2: clause-level `HAS_ACTION` classifier.
- Loss:
  `weighted_token_cross_entropy + 0.30 * weighted_clause_bce`.
- Token `O` weight `1.0`, `B-ACTION` và `I-ACTION` weight được tính trên
  inner-train và cap ở `20.0`.
- Optimizer AdamW; LR grid protocol; weight decay/warmup/epochs/patience theo
  protocol; gradient clipping `1.0`.
- Early stopping metric: inner validation token F1, tie-break bằng exact-span
  recall, rồi epoch nhỏ hơn.

### 8.4 Decode

1. Tính probability BIO và `HAS_ACTION`.
2. Nếu `HAS_ACTION < 0.20`, không emit span.
3. Sinh contiguous `B/I` spans; sửa illegal leading `I` thành `B`.
4. Map token offsets về exact raw offsets.
5. Trim whitespace/punctuation ở hai đầu nhưng không trim ký tự nằm trong quote
   pair cân bằng.
6. Sinh tối đa ba span/target clause.
7. NMS exact character overlap IoU `>=0.80`: giữ score cao hơn; nếu tie giữ span
   dài hơn; tiếp tục tie giữ `(start, end)` nhỏ hơn.
8. Không canonicalize text trước khi lưu grounded span.

### 8.5 Span metrics

Report bắt buộc:

- token precision/recall/F1;
- exact-span precision/recall/F1;
- source-clause recall;
- overlap recall ở char IoU `>=0.5`;
- recall theo W1-W5, length class và authority type;
- recall của `LATTICE`, `NEURAL`, `UNION`;
- count invalid/duplicate/out-of-clause prediction.

Gate span chạy trên union OOF outer-valid predictions, không phải train metrics.

---

## 9. Proposal pool và hard negatives

### 9.1 Union candidate pool

Mỗi fold dùng union:

- toàn bộ grounded `proposal_span_identities_v3` từ trace;
- neural spans của đúng cross-fitted model;
- typed support từ seed/relation/cluster hiện có;
- support search bổ sung trong bounded topic/window, nhưng không sửa raw trace.

Dedup bằng `(case_id, clause_id, start, end, text)`. Nếu span có ở cả hai nguồn,
đặt source `BOTH`; không tạo hai proposal.

Giữ tối đa 60 proposals/meeting bằng pre-rank không học:

1. `BOTH` trước `NEURAL` trước `LATTICE`;
2. có authority/acceptance trước không có;
3. neural span score giảm dần;
4. deterministic V3 score giảm dần;
5. chronological/source offsets để tie-break.

Pre-rank này phải được khóa trước evaluation và không dùng gold. Recall@30 được
đo trước reranker; nếu pool recall@30 dưới `0.85`, reranker không thể qua gate và
stage phải kết luận `CANDIDATE_GATE_FAILED`.

### 9.2 Typed support expansion

Với mỗi action proposal, tìm support theo thứ tự:

- same clause;
- response turns trong `+3` clauses;
- local window `-3/+5`;
- same-speaker follow-up tối đa 24 clauses, nhưng dừng tại action/reference seed
  xen giữa;
- same topic trong cap tổng.

Không link support qua topic boundary nếu không có exact task reference. Mỗi
support giữ relation type, distance, chronology và raw clause ID. Deadline chỉ
link tới existing deadline mention. Multiple owners/deadlines mâu thuẫn phải giữ
ambiguity flag, không tự chọn bằng expected final state.

### 9.3 Hard-negative taxonomy

Mỗi positive trong reranker batch phải đi cùng tối đa tám hard negatives theo
ưu tiên:

1. same-topic sibling action;
2. suggestion gần commitment;
3. question không có acceptance;
4. past-completed/progress-only;
5. recap duplicate;
6. mutation/reference candidate;
7. owner-only hoặc deadline-only fragment;
8. baseline false-create của outer-train do evaluator hiện tại xác định và có
   source span grounded; expected task text chỉ dùng tạo label, không serialize
   vào model feature;
9. lexical-near random negative cùng meeting;
10. cross-meeting negative chỉ khi không đủ negative nội meeting.

Không dùng error review của outer-valid để mine training negative. Không chọn
negative bằng expected task name similarity.

---

## 10. Multilingual proposal reranker

### 10.1 Input serialization

Cross-encoder nhận một string deterministic, không chứa gold:

```text
[ACTION] <speaker> | <action clause with span markers>
[AUTHORITY] <typed support clauses or NONE>
[ACCEPTANCE] <typed support clauses or NONE>
[OWNER] <grounded owner support or NONE>
[DEADLINE] <raw deadline mention and ID or NONE>
[NEGATIVE] <negative support or NONE>
[RELATIONS] <sorted relation types and distances>
[CONTEXT] <bounded chronological clauses>
```

Marker action là `[ACT]...[/ACT]`. Text được truncate từ context xa trước;
action, authority và negative evidence không được truncate. Nếu ba phần bắt buộc
đã vượt 512 token, `STOP_OVERSIZED_CORE_EVIDENCE` cho record đó và report error;
không silently cut action span.

### 10.2 Labels và loss

- Positive `CREATE=1` khi proposal exact-match human action span của train fold.
- Candidate cùng source clause nhưng sai boundary là hard negative, trừ khi char
  IoU `>=0.8`; trường hợp đó label soft bằng IoU, chỉ ở training.
- UPDATE/REFERENCE/DROP là `0` cho create score.
- Loss chính: weighted BCE on logits.
- Thêm pairwise margin loss `0.20 * max(0, 0.20 - positive + negative)` cho
  hard-negative pairs cùng meeting.
- Sample weights theo protocol; class `pos_weight` fit trên train fold và cap 20.
- Early stopping: inner PR-AUC; tie-break proposal recall@30, rồi epoch nhỏ hơn.

### 10.3 Calibration và selection

- Fit temperature scaling trên inner-OOF logits, không outer-valid.
- Chọn threshold từ fixed grid để maximize inner task-identity F1 với constraint
  precision `>=0.48`; nếu không threshold nào đạt, chọn threshold có precision
  cao nhất, tie-break recall rồi threshold cao hơn, và đánh dấu gate fail.
- Mỗi meeting select tối đa proposal count do inner calibration chọn trong tập
  `{3, 5, 8}`, cũng bằng inner-OOF only.
- Không dùng W4/W5-specific threshold.
- Không dùng threshold khác theo wave/case/length.

Report PR-AUC, ROC-AUC chỉ khi cả hai class tồn tại, calibration error, Brier
score, precision/recall/F1 và recall@K `K={1,3,5,10,30}`.

---

## 11. LLM teacher tùy chọn

Teacher không thay human evidence và không phải điều kiện để code hoàn tất.

### 11.1 Payload

Mỗi payload là một bounded topic bundle gồm:

- clause IDs, speaker, chronology và raw text;
- candidate proposal IDs và grounded action spans;
- typed support/relation/negative flags;
- allowed date mention IDs;
- allowed existing task references cho UPDATE;
- tuyệt đối không có expected task/final output.

Một payload tối đa 30 clauses, 12,000 characters và 60 proposals. Nếu topic vượt
cap, split theo chronological boundary với overlap ba clauses; same proposal chỉ
thuộc shard có primary action clause.

### 11.2 Hai prompt độc lập

`teacher-a-v1` đánh giá authority-first. `teacher-b-v1` đánh giá
negative/lifecycle-first. Cả hai dùng cùng strict schema nhưng instruction order
khác nhau. Temperature/reasoning config phải cố định trong run manifest.

Consensus khi cả hai response valid và cùng `decision` + cùng action span ref.
Nếu khác nhau, label là `teacher_single` chỉ khi một response valid và confidence
`>=0.90`; nếu cả hai valid nhưng disagree, `UNRESOLVED`, không pseudo-label.

### 11.3 Teacher validator

Validator phải reject response nếu:

- payload/prompt/model hash không khớp;
- proposal/clause/span/date/task reference không thuộc allowlist input;
- action substring không exact raw text;
- CREATE thiếu authority hoặc chỉ có note authority;
- UPDATE target không nằm trong allowed existing task refs;
- confidence ngoài `[0,1]`;
- reason code ngoài allowlist;
- duplicate proposal decision;
- schema có extra field;
- output chứa task name/resolved date/new ID/free-form rationale.

Rejected response không retry bằng prompt chứa thêm gold. Provider retry chỉ cho
HTTP transient/schema failure, tối đa hai lần và vẫn tính budget.

### 11.4 Teacher ceiling trước distill

Nếu provider mode được phép, đánh giá teacher predictions như một model độc lập
bằng outer folds. Teacher phải đạt:

- exact-span recall `>=0.75`;
- proposal recall@30 `>=0.90`;
- accepted-band hard-negative CREATE count `0`;
- task-identity F1 `>=0.60`.

Nếu không đạt, teacher labels không được đưa vào student; local human-only track
vẫn tiếp tục. Nếu `cache-only` không có đủ cache, teacher stage ghi
`SKIPPED_NO_VALID_CACHE` và weight teacher bằng zero; không gọi provider.

---

## 12. End-to-end cross-fitted replay

Không sửa production pipeline để test experiment. `replay.py` là pure offline
challenger adapter.

Metric matching bắt buộc tái sử dụng `backend.app.evaluation.compare_case` và
`backend.app.evaluation.aggregate_results`. Grounded CREATE promotion tái sử
dụng `backend.app.verification.proposal_validator.validate_task_create_proposal`;
event replay tái sử dụng reducer/ledger hiện tại. Không clone hoặc viết lại
task-identity matcher.

### 12.1 Challenger V1

Challenger chỉ thêm/suppress CREATE proposals. Không cho model tự thực hiện
UPDATE, owner change, deadline change, cancel hoặc complete ở experiment V1.

Cho mỗi outer-valid meeting:

1. Chạy/load Q2 baseline output tại base commit.
2. Lấy selected OOF CREATE proposals.
3. Reject proposal thiếu grounded action/authority hoặc vi phạm negative guard.
4. Dedup với baseline bằng exact source span trước, sau đó existing safe identity
   comparator; ambiguous sibling luôn giữ riêng hoặc reject, không merge.
5. Chỉ suppress baseline task khi selected model proposal map exact cùng source
   span và classifier xác định non-CREATE với calibrated confidence; default là
   không suppress nếu mapping ambiguous.
6. Promote proposal qua existing Python proposal validator/reducer contract;
   model không trực tiếp serialize final task.
7. Owner/deadline chỉ lấy từ existing grounded relation/date mention và existing
   resolver. Không có evidence thì để pipeline hiện tại xử lý/để trống.
8. Serialize bằng production serializer nhưng chỉ trong offline process.

Mọi output record phải gắn provenance `BASELINE` hoặc
`EXPERIMENTAL_OOF_CREATE`; provenance chỉ nằm trong diagnostic sidecar, không sửa
public response schema.

### 12.2 Structured acceptance exception

Validator hiện tại reject mọi source clause có `QUESTION`/`SUGGESTION`. Để test
question → acceptance mà vẫn fail-closed, Commit 6 được phép có đúng một thay
đổi production-adjacent: thêm optional keyword-only parameter mặc định `None`
vào `validate_task_create_proposal`. Production callers không truyền parameter
này nên behavior phải byte-for-byte invariant ở full baseline.

Parameter chỉ cho phép bỏ qua guard `QUESTION` hoặc `SUGGESTION` cho một primary
action clause khi experimental adapter đã independently chứng minh tất cả:

- có typed relation `ACCEPTS` hoặc `AUTHORIZES`;
- support clause nằm sau action clause và distance `<=3`;
- support clause có `CONFIRMATION`, `DIRECT_ASSIGNMENT` hoặc
  `FIRST_PERSON_COMMITMENT`;
- support không có `REJECTION`, `CANCELLATION`, `HYPOTHETICAL` hoặc
  `PAST_COMPLETED`;
- action span vẫn exact-grounded ở primary clause;
- authority clause nằm trong bounded source IDs;
- relation không đi qua action/reference seed khác.

Không bao giờ override `HYPOTHETICAL`, `PAST_COMPLETED`, `PROGRESS_ONLY`,
`REJECTED_PROPOSAL` hoặc `FUTURE_DISCUSSION`. Override map do Python relation
validator tạo, không lấy trực tiếp từ model output. Phải có positive test cho
accepted question/suggestion và negative tests cho unaccepted/rejected/cross-
task cases.

### 12.3 So sánh công bằng

- Baseline và challenger dùng cùng base SHA, dataset, note mode và pipeline mode.
- Primary result dùng đúng Q2 configuration đã khóa trong PR29.
- Chạy thêm sensitivity with-note/without-note nếu Q2 artifacts cho phép, nhưng
  không dùng sensitivity run để chọn threshold.
- Metric aggregate từ union outer-valid outputs; không average F1 theo fold để
  thay cho global F1.
- Report cả global và per-fold matched/missing/unexpected.
- Dùng evaluator hiện tại cho task identity/field metrics; không viết matching
  logic mới nếu evaluator đã có hàm tương đương.

---

## 13. Safety counters bắt buộc

`reports/safety.json` phải có các counter sau và tất cả bằng zero để pass:

- `unknown_clause_id_count`;
- `invalid_action_span_count`;
- `unknown_date_mention_id_count`;
- `unknown_task_reference_count`;
- `model_resolved_date_count`;
- `model_minted_task_id_count`;
- `update_minted_identity_count`;
- `note_only_create_count`;
- `question_without_acceptance_create_count`;
- `suggestion_only_create_count`;
- `past_completed_create_count`;
- `recap_duplicate_count`;
- `mutation_only_create_count`;
- `sibling_unsafe_merge_count`;
- `cross_fold_training_leak_count`;
- `gold_feature_leak_count`;
- `provider_budget_violation_count`;
- `teacher_schema_error_accepted_count`;
- `public_output_schema_change_count`;
- `runtime_artifact_committed_count`.

Mỗi counter phải có unit test tạo violation tương ứng và chứng minh validator
reject/fail-closed.

---

## 14. Stage thực thi và commit

Bot phải thực hiện đúng bảy commit. Trước mỗi commit: chạy targeted tests, full
tests, `git diff --check`, kiểm tra staged diff và dùng `git add` explicit.

### Commit 1 — protocol và preflight

Implement:

- package skeleton;
- runbook này;
- strict protocol contract/config;
- hashing/inventory/preflight;
- dependency extra `ml-distill` trong `pyproject.toml` gồm `torch>=2.4`,
  `transformers>=4.45`, `sentence-transformers>=5.0`, `numpy>=2.0`,
  `scikit-learn>=1.5`;
- offline/no-network unit tests.

Không đổi dependency production mặc định; chỉ thêm optional extra.

Sau khi sửa `pyproject.toml`, cài đúng extra trước test:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,ml-distill]"
```

Commit message:

```text
chore(experiment): lock distilled proposal ranker protocol
```

### Commit 2 — fold và datasets

Implement:

- trace reader;
- deterministic outer/inner fold manifests;
- bounded context builder;
- span dataset/offset mapping;
- candidate union và hard-negative dataset builder;
- leakage tests.

Chạy builders hai lần và assert output hashes giống nhau.

Commit message:

```text
feat(experiment): build leakage-safe distillation datasets
```

### Commit 3 — teacher pipeline

Implement:

- payload exporter;
- two prompts;
- strict client modes `disabled`, `cache-only`, `provider`;
- budget gate;
- validator/consensus/cache;
- fake-provider tests.

Không gọi provider trong test hoặc commit stage.

Commit message:

```text
feat(experiment): add grounded teacher distillation pipeline
```

### Commit 4 — neural span extractor

Implement:

- model, weighted losses, training loop, deterministic seeds;
- decode/raw offset grounding;
- checkpoint manifest;
- metrics and unit tests.

Smoke train trên fixture tiny, không dùng outer result để đổi code.

Commit message:

```text
feat(experiment): add neural action span student
```

### Commit 5 — cross-encoder reranker

Implement:

- serialization;
- weighted BCE + pairwise loss;
- hard-negative sampler;
- temperature calibration;
- fixed threshold/top-k selector;
- metrics/tests.

Commit message:

```text
feat(experiment): add proposal cross encoder reranker
```

### Commit 6 — nested cross-fit và replay

Implement:

- end-to-end orchestrator with resumable stages;
- fold/seed-specific checkpoints;
- OOF-only ensemble;
- offline replay through Python validators;
- safety report and no-leak assertions;
- crash-safe manifest writing: temp file rồi atomic replace.

Resume chỉ reuse artifact khi code SHA, protocol hash, data hash, fold hash,
model name và seed đều khớp. Mismatch phải recompute artifact đó; không reuse.

Commit message:

```text
feat(experiment): add nested crossfit quality replay
```

### Commit 7 — kết quả

Chạy full protocol một lần. Generate
`docs/experiments/distilled-proposal-ranker-v1-results.md` chỉ từ aggregate
reports, không chứa transcript, prompt, raw evidence hoặc per-person content.

Report phải ghi:

- branch/commit/protocol/data/trace/fold hashes;
- Python/package/model/device versions;
- provider mode/model/prompt/calls/tokens/cost hoặc `cache-only`;
- per-stage gate;
- per-fold/per-seed/global metrics;
- paired case-level bootstrap 95% CI của F1 delta so với Q2, 10,000 resamples,
  seed `1729`; CI chỉ để báo cáo, không thay gate point-estimate;
- Q2 delta;
- safety counters;
- failure list;
- final decision `PROMISING` hoặc `NOT_READY`;
- tuyên bố rõ đây là development nested-CV, không phải blind test.

Commit message:

```text
docs(experiment): record distilled proposal ranker results
```

---

## 15. Lệnh chạy chuẩn

Sau Commit 6, chạy:

```powershell
$python = ".\.venv\Scripts\python.exe"
$protocol = "experiments\distilled_proposal_ranker\config\protocol-v1.json"
$runRoot = "evaluation\runtime\experimental-distillation-v1"

& $python scripts\experimental_distillation\preflight.py `
  --protocol $protocol `
  --output-root $runRoot

& $python scripts\experimental_distillation\build_folds.py `
  --protocol $protocol `
  --output "$runRoot\folds\fold-manifest.json"

& $python scripts\experimental_distillation\export_teacher_payloads.py `
  --protocol $protocol `
  --folds "$runRoot\folds\fold-manifest.json" `
  --output-root "$runRoot\teacher-payloads"

& $python scripts\experimental_distillation\generate_teacher_labels.py `
  --protocol $protocol `
  --mode cache-only `
  --payload-root "$runRoot\teacher-payloads" `
  --cache-root "$runRoot\teacher-cache" `
  --report "$runRoot\reports\teacher.json"

& $python scripts\experimental_distillation\run_nested_crossfit.py `
  --protocol $protocol `
  --folds "$runRoot\folds\fold-manifest.json" `
  --teacher-cache "$runRoot\teacher-cache" `
  --output-root $runRoot `
  --model-root "artifacts\models\experimental-distillation-v1" `
  --resume

& $python scripts\experimental_distillation\evaluate_experiment.py `
  --protocol $protocol `
  --run-root $runRoot `
  --output "$runRoot\reports\final.json" `
  --markdown docs\experiments\distilled-proposal-ranker-v1-results.md

& $python -m pytest backend/tests -q
git diff --check
```

`--resume` không được bỏ qua failed fold. Orchestrator chỉ đánh dấu complete khi
artifact final có checksum và mọi required record đủ.

### 15.1 Provider mode, chỉ khi đã có authority explicit

Không chạy block này mặc định. Khi người dùng đã cho phép paid call và cung cấp
budget:

```powershell
$env:DISTILL_TEACHER_MODE = "provider"
$env:DISTILL_ALLOW_PROVIDER_CALLS = "1"
$env:DISTILL_TEACHER_MODEL = "<explicit-model>"
$env:DISTILL_MAX_CALLS = "<positive-integer>"
$env:DISTILL_MAX_INPUT_CHARS = "<positive-integer>"
$env:DISTILL_MAX_ESTIMATED_USD = "<positive-decimal>"
$env:DISTILL_INPUT_USD_PER_1M = "<rate>"
$env:DISTILL_CACHED_INPUT_USD_PER_1M = "<rate>"
$env:DISTILL_OUTPUT_USD_PER_1M = "<rate>"

& $python scripts\experimental_distillation\generate_teacher_labels.py `
  --protocol $protocol `
  --mode provider `
  --payload-root "$runRoot\teacher-payloads" `
  --cache-root "$runRoot\teacher-cache" `
  --report "$runRoot\reports\teacher.json"
```

Script phải in preflight count/estimated maximum và yêu cầu đủ env; không được
interactive-confirm trong batch bot. Thiếu một biến thì fail trước network.

---

## 16. Gate logic không được diễn giải tùy ý

`evaluate_experiment.py` quyết định theo thứ tự:

1. input/evidence/fold/protocol integrity;
2. leakage counters;
3. span gate;
4. proposal pool recall@30 gate;
5. reranker metrics;
6. end-to-end task identity metrics;
7. per-fold regression;
8. safety counters.

Decision chi tiết:

- `INVALID_RUN`: integrity/leakage thiếu hoặc sai;
- `SPAN_GATE_FAILED`: exact recall/token F1 fail;
- `CANDIDATE_GATE_FAILED`: recall@30 fail;
- `RANKER_GATE_FAILED`: ranker/e2e precision-recall-F1 fail;
- `SAFETY_GATE_FAILED`: một safety counter khác zero;
- `PROMISING`: tất cả gate pass.

`readiness=PROMISING` chỉ khi `decision=PROMISING`; mọi decision khác map thành
`readiness=NOT_READY`.

Không dùng weighted average tự đặt để override gate. Không làm tròn trước khi so
gate; chỉ làm tròn khi display sáu chữ số.

Nếu teacher stage fail/skip nhưng local experiment valid, report teacher status
riêng và tiếp tục với weight teacher bằng zero. Nếu model training crash hoặc
outer fold thiếu prediction, run là `INVALID_RUN`, không tính metric từ partial
folds.

---

## 17. Test cases tối thiểu

Ngoài test happy path, phải có các negative tests sau:

- same case không xuất hiện ở train và validation;
- duplicated evidence row bị reject;
- human-confirmed thiếu reviewer/timestamp bị reject;
- span offset lệch một ký tự bị reject;
- Unicode Vietnamese mapping qua tokenizer vẫn quay lại exact raw substring;
- leading illegal `I-ACTION` decode thành boundary hợp lệ;
- prediction vượt target clause bị reject;
- question không acceptance không CREATE;
- suggestion gần deadline vẫn không CREATE;
- past-completed có action verb vẫn không CREATE;
- recap task label không duplicate;
- mutation action không mint identity;
- note-only claim không CREATE;
- sibling lexical-near không merge;
- deadline ID không tồn tại bị reject;
- UPDATE target ngoài allowlist bị reject;
- teacher extra JSON field bị reject;
- teacher free-form rationale bị reject;
- teacher disagree thành `UNRESOLVED`;
- provider mode thiếu budget fail trước fake network call;
- cache hash mismatch không reuse;
- checkpoint code/protocol/data hash mismatch không resume;
- gold task name không xuất hiện trong serialized model input;
- outer-valid gold span không được inject vào candidate pool;
- top-60 prune deterministic;
- threshold chỉ fit từ inner-OOF;
- public serializer output không đổi khi experiment package không được import;
- import production app không import `torch`/`transformers` eagerly;
- model artifact/runtime report không nằm trong Git staged files.

---

## 18. Kiểm tra trước mỗi commit

Bot chạy đúng sequence:

```powershell
& .\.venv\Scripts\python.exe -m pytest backend/tests/experimental -q
& .\.venv\Scripts\python.exe -m pytest backend/tests -q
git diff --check
git status --short
git diff --stat
git diff
```

Sau khi add explicit paths:

```powershell
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Bot phải scan staged paths và fail nếu match:

```text
evaluation/runtime/
artifacts/models/
.env
*.key
*.pem
*teacher-cache*
*checkpoint*
```

Không commit nếu tests fail, diff có whitespace error, staged diff rỗng hoặc có
unrelated file.

---

## 19. Cleanup sau experiment

Không xóa checkpoints/cache vì cần reproducibility. Chỉ làm:

1. ghi `run-manifest.json` và SHA256 cho mọi final aggregate report;
2. ghi inventory model artifact gồm path, bytes, SHA256, model/config hash;
3. xác nhận raw artifacts vẫn ở ignored runtime/model roots;
4. xác nhận branch có đúng bảy commit dự kiến;
5. xác nhận worktree sạch sau local excludes;
6. không push/PR/merge.

Lệnh kết thúc:

```powershell
git status --short --branch
git log --oneline a04e815..HEAD
git diff --check a04e815..HEAD
git diff --name-only a04e815..HEAD
```

Nếu staged/committed file chứa runtime/model artifact hoặc secret, dừng và báo
`STOP_COMMIT_SCOPE_VIOLATION`; không tự rewrite history.

---

## 20. Báo cáo cuối bot phải trả cho người dùng

Bot báo ngắn gọn nhưng đủ các trường:

```text
Branch: experimental/distilled-proposal-ranker-v1
Base: a04e815
Commits: <7 SHA + subject>
Protocol SHA256: <hash>
Teacher: disabled/cache-only/provider; model; prompt versions; calls; cost
Span OOF: token F1; exact precision/recall/F1; lattice vs neural vs union
Proposal OOF: recall@30; PR-AUC; selected count
Task OOF: expected; actual; matched; unexpected; missing; precision; recall; F1
Delta vs Q2 F1 0.5338: <signed delta>
Per-fold minimum delta: <value>
Safety violations: <count>
Readiness: NOT_READY/PROMISING
Decision: INVALID_RUN/SPAN_GATE_FAILED/CANDIDATE_GATE_FAILED/RANKER_GATE_FAILED/SAFETY_GATE_FAILED/PROMISING
Runtime report: <path>
Committed result summary: docs/experiments/distilled-proposal-ranker-v1-results.md
Push/PR/Merge: not performed
```

Nếu `PROMISING`, bước tiếp theo chỉ là đề xuất một experiment/shadow integration
riêng. Runbook này không cấp quyền sửa production runtime.
