# Task evidence review

`task-evidence-v2.jsonl` is created only through the review import workflow.
Suggestions are not human evidence and must remain `SUGGESTED`; the gate accepts
only grounded `HUMAN_CONFIRMED` rows with reviewer and timestamp metadata.

Open `review-queue/task-evidence-review.html` in a browser, load an exported
queue JSONL, review the transcript evidence, then download the reviewed JSONL
for `scripts/import_task_evidence_reviews.py`.

`proposal-span-supervision-v3.jsonl` is generated only from human-confirmed
task evidence plus immutable shadow traces. Its rows declare one of three
disjoint splits: `train` (W1–W3), `calibration` (W4), and `test` (W5).
Fit model weights only on `train`; W4 may select a threshold or extraction rule;
W5 is final-only and must not influence either decision. Use
`scripts/audit_action_proposals_v3.py --waves W4` before opening W5, then run it
once with `--waves W5` to record the final candidate-coverage result.
