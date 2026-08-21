# Blind corpus contract

`blind_test` is intentionally empty until an independently reviewed corpus is
available. Do not copy cases from `data/validation` here and call them blind.

A release blind split must contain at least 20 reviewed meetings and a manifest
with `split_kind: "BLIND"`, immutable SHA-256 digests, `tuned_against: false`,
a named independent reviewer, and a freeze timestamp. Run
`scripts/verify_quality_gate.py --require-blind` only after the corpus and its
evaluator report have been frozen.
