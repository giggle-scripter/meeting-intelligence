# Task evidence review

`task-evidence-v2.jsonl` is created only through the review import workflow.
Suggestions are not human evidence and must remain `SUGGESTED`; the gate accepts
only grounded `HUMAN_CONFIRMED` rows with reviewer and timestamp metadata.

Open `review-queue/task-evidence-review.html` in a browser, load an exported
queue JSONL, review the transcript evidence, then download the reviewed JSONL
for `scripts/import_task_evidence_reviews.py`.

`proposal-span-supervision-v3.jsonl` is generated only from human-confirmed
task evidence plus immutable shadow traces. Its rows declare either `train` or
`held_out`; do not tune extraction/ranking rules on the held-out split.
