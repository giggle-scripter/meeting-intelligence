# Blind corpus contract

`blind_test` is intentionally empty until an independently reviewed corpus is
available. Do not copy cases from `data/validation` here and call them blind.

A release blind split must contain at least 20 reviewed meetings and a manifest
with `split_kind: "BLIND"`, immutable SHA-256 digests, `tuned_against: false`,
a named independent reviewer, and a freeze timestamp. Run
`scripts/verify_quality_gate.py --require-blind` only after the corpus and its
evaluator report have been frozen.

Each independent meeting is a folder named exactly as `metadata.case_id` and
contains `metadata.json`, `transcript.txt`, and `expected_output.json`.
`metadata.ground_truth.available` must be true and declare the reviewer who
verified the expected output. Do not run tuning after this reviewer sees the
frozen cases.

After at least 20 cases have been reviewed, create (but do not hand-edit) the
manifest:

```powershell
.\.venv\Scripts\python.exe scripts\freeze_blind_split.py data\blind_test `
  --split-id blind-task-evidence-v1 `
  --reviewer "Independent reviewer" `
  --frozen-at "2026-08-26T10:00:00+07:00" `
  --output data\evaluation_splits\blind-task-evidence-v1.json
```

The command computes per-file and corpus digests. Any later source change makes
the manifest fail verification and requires a new independent review/freeze.

To review span/authority evidence for the scorer, first generate shadow traces
for the blind corpus, then export a queue for the existing
`data/quality/review-queue/task-evidence-review.html` form. The queue contains
only reviewed expected tasks and transcript context; its clause suggestions are
explicitly not ground truth.

```powershell
.\.venv\Scripts\python.exe scripts\build_blind_task_evidence_review_queue.py data\blind_test `
  --traces evaluation\runtime\blind-traces `
  --output evaluation\runtime\blind-task-evidence-review-queue.jsonl `
  --manifest evaluation\runtime\blind-task-evidence-review-queue.manifest.json
```
