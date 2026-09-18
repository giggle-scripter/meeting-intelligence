# V2.27 canonical internal baseline

The canonical V2.27 baseline is the immutable package at
`evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze/`.
Runtime artifacts remain private and ignored by Git. The package is created by
`scripts/experimental_distillation/freeze_v227_baseline.py`, which refuses to
write into a non-empty output directory and records SHA-256 hashes for the
source chain, dependency manifest, split manifest, and copied artifacts.

Classification: **PROMISING_INTERNAL_ONLY**.

The frozen result is a DEV42 leave-template-out experiment. It uses the V2.25
candidate construction (`final_plus_bridge_plus_intermediate`), the V2.26
nested template/family holdout, and the V2.27 volume-adaptive policy. Each
template has one sparse hashed ranker fit (768 features, one epoch, learning
rate 0.15). Policy tuning uses fit meetings only and can use meeting-local
candidate volume, source mix, score distribution, and assignee/due/status
completeness. Template, family, case ID, and expected labels are forbidden
policy features. Candidate identity uses one meeting-local Hungarian match.

DEV42 metrics are identity precision **0.52410**, identity recall **0.62590**,
identity F1 **0.57049**, field accuracy **0.84483**, and supported-family worst
F1 **0.44444** for `OPS-FPC` (10 supported families; minimum support is five
expected tasks). The aggregate identity gate is 0.57, the supported-family
gate is 0.40, and field accuracy may regress by at most 0.03 against the
expanded-pool baseline. All three gates passed.

The split audit is DEV42 only: 34 train meetings plus 8 calibration meetings,
15 held-out templates, and 27 families. Diagnostic9, final-dev, and outer
validation were not opened; their results are not validation evidence for this
frozen baseline. The run made zero teacher, provider, or Kaggle calls.

## Audit clarification

The current cross-version split history is recorded in the separate
[V2.27 current split ledger](v227_split_ledger.md). The V2.27 source run is
DEV42 only and did not open diagnostic9, final-dev18, or outer17. Later
protocols consumed diagnostic9: V2.24 evaluated it once, V2.28 evaluated it
once again as a confirmation of that prior consumption, and V2.29 includes it
in DEV51. Those later reads do not change the V2.27 freeze boundary. Final-dev18
and outer17 remain unopened.

Canonical source pointers and hashes are in `source-hashes.json`; copied
metrics, coverage, fold policies, split access, tests, status, and report are
under `canonical/`. Verify the package with:

```text
python scripts/experimental_distillation/freeze_v227_baseline.py --verify
```

The retained `evaluation/runtime/experimental-distillation-v2/v227-debug4/`
directory is explicitly marked noncanonical for debugging and must not be
cited as the baseline.
