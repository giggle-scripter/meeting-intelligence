# Release freeze: 2026-09-18

This is the research and runtime boundary for the release. Improvement work is
stopped at this point. The production backend remains the deterministic,
rule-first V1 pipeline. The V2.27 artifact is a private research baseline and
must not be described as the deployed inference model.

## Decision

Freeze **V2.27 DEV42 volume-adaptive policy** as the best reproducible internal
research result. Its immutable package is
`evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze/`.
The package classification is `PROMISING_INTERNAL_ONLY`; it is not a
production promotion and it has no provider or Kaggle dependency.

The reported DEV42 leave-template-out result is identity F1 **0.5704918033**
(precision 0.5240963855, recall 0.6258992806), field accuracy **0.8448275862**,
and supported-family worst F1 **0.4444444444** for OPS-FPC. The declared gates
passed: aggregate F1 >= 0.57, field accuracy no worse than the 0.03 baseline
tolerance, and supported-family F1 >= 0.40 with at least five expected tasks.

This result is development evidence only. The 42-meeting DEV42 set is
TRAIN34 plus calibration8, with 15 template-held-out folds and one fit per
fold. Diagnostic, final-dev, and outer-validation labels were not opened for
this freeze. There are no hidden holdout or generalization claims.

V2.24's DEV42 cross-fit F1 was **0.5789**, numerically above V2.27's
**0.5704918033**. V2.27 is canonical because it is the later immutable result
under the stricter nested template/family stability and inference-safe policy
protocol; this choice is not a claim that it has the highest raw F1.

## Frozen evidence and hashes

The exact manifest digests are tracked in
[`release-freeze-lock-2026-09-18.json`](release-freeze-lock-2026-09-18.json).
The V2.27 manifest SHA-256 is
`480e3648141c7d0f422ffef9dbf86d5be6505b3b8dbd8ccebc0942704d1e8099`.
Its canonical metrics SHA-256 is
`05dfff5486df84d0b9a7333a76bc4c94c813f49acdc42e30ab168f0497f9c86e`.
The manifest records immutable source and dependency hashes, zero provider
calls, zero Kaggle calls, 15 fits, and the split-access boundary.

Supporting comparison manifests are recorded without copying private content:

| package | manifest SHA-256 | disposition |
|---|---|---|
| V3.1 `v31-union-ranker-oof-06` | `eb471fbfadb87a9992e6215db9dcdc15008633c1701e04d54fb6f566e490e5ef` | development OOF comparator; not promoted |
| V3.2 `v32-frozen-promotion-shadow7-03` | `80548c5693ded33b412f3953432b5670b9ba565fb3f55350ebaba3988ba2fb8b` | shadow gate failed |
| V4.5 `v45-contextual-action-rescue-01` | `71353a9832165ba6baeca4e79864f6dc5b9647151126b7a25ecdc8e149f8ad26` | candidate oracle only; no student promotion |
| V5 `v50-deepseek-candidate-teacher-canary-01` | `fb1d13e2199c7d8aab771984087840611d6d59abdc298cad40ff51c3d75e3620` | canary stopped; human adjudication pending |

The dataset boundaries used by the later candidate experiments are DS26: 20
canonical cases, canonical hash
`5677839eee80409a16a42ce23eb19ab6903b60726b03f0904e02df937779e59b`; and DS27:
28 canonical cases, canonical hash
`c2723ebb6e0288b248444931814b5ab38a7d6522e474cf2da2bb8366523094d1`.
DS27 is split into 21 development cases and seven predeclared closed shadow
cases. The seven-case DS26 shadow was consumed in V3.2; V4 later used all 20
DS26 cases as development data. These are split facts, not a claim that either
set is a clean holdout after consumption.

Private runtime files, transcripts, labels, model weights, prompts, request
payloads, and provider responses remain private and ignored. This tracked
lock records paths and digests only.

## Gates and non-promotions

V1's historical teacher/candidate ceiling had exact-span recall **0.274**, below
the **0.75** teacher gate (the source ceiling and gate are recorded in
`docs/experiments/v28_extractive_span_design.md` and
`docs/experimental-distilled-proposal-ranker-runbook.md`). No full teacher
integration was completed; the teacher track therefore did not supply student
labels or a production path.

V3.1 reached aggregate development OOF F1 **0.6053268765** over DEV42 plus
DeepSeek DEV13, but this did not establish a clean holdout or production
readiness. The frozen V3.2 student was fit once; the `-02` run had a partial
label-open failure and stopped on
`NameError("name 'info' is not defined")`, then the `-03` run completed by
reopening those same seven labels. The append-only erratum is recorded at
`evaluation/runtime/experimental-distillation-v2/v4-deepseek-28-cases-01/v32-shadow7-audit-erratum.json`.
Its shadow F1 was
**0.3859649123** (precision 0.3142857143, recall 0.5), so its identity,
precision, and recall gates failed. The shadow set is consumed and is not a
pristine one-shot or future holdout.

V4.5 passed only a candidate-oracle ceiling gate: 1,236 frozen candidates had
oracle F1 **0.8897959184**. Student selectors V4.6 through V4.9 and the
V4.10–V4.12 cascade attempts failed their declared gates or stopped before a
usable evaluation. V4.12's persisted metrics contain base-only F1
**0.3727273**, cascade A F1 **0.4152047**, and cascade B F1 **0.4077135**.
Its status is **STOP** because a post-metric prediction step raised
`NameError("name 'oof_selected' is not defined")`; there is no complete V4.12
evaluation or manifest. The status evidence is
`evaluation/runtime/experimental-distillation-v2/v412-precision-first-cascade-01/status.json`.
The persisted numbers are diagnostic evidence only.
Oracle performance is an upper bound, not student performance.

The V5 DeepSeek teacher canary completed 12 provider attempts and returned
valid responses. Official combined identity F1 was **0.5373134328** (precision
0.5, recall 0.5806451613); the F1 and precision gates failed. V5.1 found a
large transcript/canonical-granularity mismatch, and V5.2 prepared a blinded
12-case adjudication packet. Adjudication is pending; its diagnostic adjusted
F1 must not replace the official metric or mutate canonical labels.

## Scope, risks, and re-opening rule

The freeze closes model, policy, feature, threshold, teacher, provider,
Kaggle, and holdout-improvement work. It does not claim that the research
ranker is wired into backend inference, nor that it improves production
quality. Known risks include low support in individual families, task
granularity and canonical-label mismatch, false positives, missing actions,
and date/state rendering errors. Production continues to use rule-first V1;
OpenAI is an optional fallback controlled by `OPENAI_API_KEY` and is separate
from the frozen research packages.

Re-open this boundary only with a new dated experiment, a new immutable
manifest, an explicit split/access ledger, and a separately reviewed gate.
Do not overwrite or amend the V2.27 private package in place.
