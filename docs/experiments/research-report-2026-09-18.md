# Experimental research report: 2026-09-18

This report records the completed research line and its evidence boundary. It
is a reproducibility and decision record, not a claim that any experimental
ranker is deployed.

## Research question and evaluation contract

The experiments asked whether a bounded candidate pool and a cheap selector
could improve meeting-task identity matching while preserving fields. The
official task metric is identity matching by the repository evaluator:
precision, recall, and F1 over expected and emitted tasks, with field accuracy
reported separately. Candidate coverage/oracle F1 is an upper bound because it
uses gold matching to ask whether a candidate exists; it is never student F1.

All OOF selectors fit and tuned on training portions only. Template/family
grouped folds hold out complete nested families where the package says so.
Provider, teacher, Kaggle, final-dev, and outer labels are counted explicitly
in each manifest. A consumed shadow set is not a future holdout.

## Data and split provenance

The main V2.27 development boundary is DEV42: TRAIN34 plus calibration8,
42 meetings, 15 sorted-template folds, and 27 families. Each held-out template
includes nested families. The source pool is the V2.26
`final_plus_bridge_plus_intermediate` candidate union. Diagnostic, final-dev,
and outer-validation labels were closed for the V2.27 freeze.

Later V3–V4 development packages combine DEV42 with the private DeepSeek DS26
development set (20 meetings) and DS27-dev21 (21 meetings) for 83 development
meetings. The V3.1 package also has a 13-meeting DeepSeek development cohort;
its seven-case DS26 shadow was held back until V3.2, then consumed; V4 later
used all 20 DS26 cases as development data. DS26 has 20 canonical cases with
canonical hash
`5677839eee80409a16a42ce23eb19ab6903b60726b03f0904e02df937779e59b`.
The DS27 bundle has 28 canonical cases with canonical hash
`c2723ebb6e0288b248444931814b5ab38a7d6522e474cf2da2bb8366523094d1`; its
split is 21 development cases and seven predeclared closed shadow cases.
V4.5 uses the frozen 83-meeting candidate cache (1,236 candidates). V5 selects 12
meetings, four each from DEV42, DS26, and DS27-dev21, for a teacher canary.
Final-dev18 and outer17 stay closed throughout these packages.

The public and private runtime paths, manifest hashes, and access boundaries
are listed in [`release-freeze-lock-2026-09-18.json`](release-freeze-lock-2026-09-18.json).
No transcript, expected-output, prompt, provider response, or model-weight
content is copied into this report.

## Chronology and results

### V2 development line

Early span, boundary, classical-ranker, and neural smoke experiments exposed
candidate-coverage, boundary, and task-identity bottlenecks. V2.21's bounded
neural repair stopped at held-out identity F1 0.3783783784 against a 0.50
threshold. V2.24 then passed its DEV42 cross-fit gate (reranked identity F1
0.5789; field accuracy 0.8723), but subsequent template/family stability and
policy work established a stricter reproducibility boundary.

V2.26 introduced nested template/family holdout. V2.27 added an inference-safe
volume-adaptive policy over candidate count, source mix, score distribution,
and field completeness. It fits a sparse hashed 768-feature logistic ranker
for one epoch per fold and tunes policy on fit meetings only. The immutable
V2.27 canonical result is:

| metric | value | gate/evidence |
|---|---:|---|
| identity precision | 0.5240963855 | reported DEV42 OOF |
| identity recall | 0.6258992806 | reported DEV42 OOF |
| identity F1 | **0.5704918033** | passes >= 0.57 |
| field accuracy | 0.8448275862 | baseline 0.8333333333; within 0.03 |
| worst supported-family F1 | 0.4444444444 | OPS-FPC; passes >= 0.40 |

This is the selected freeze because it is the strongest immutable, correctly
bounded research result. V2.24's raw F1 is numerically higher, but V2.27 is
canonical under the stricter nested template/family stability and
inference-safe policy protocol; this is a protocol choice, not a highest-raw-F1
claim. It remains `PROMISING_INTERNAL_ONLY`.

The V1 teacher/candidate ceiling had exact-span recall **0.274**, below the
declared **0.75** teacher gate (source ceiling and gate:
`docs/experiments/v28_extractive_span_design.md` and
`docs/experimental-distilled-proposal-ranker-runbook.md`). No full teacher
integration was completed, so the teacher track did not provide student labels
or a production path.

### V3.1 and V3.2 student line

V3.1 reused the V2.27 sparse ranker and policy as Variant A in grouped OOF.
Aggregate OOF F1 was **0.6053268765** over DEV42 plus DeepSeek DEV13
(precision 0.5387931034, recall 0.6906077348). DEV42 alone was F1
0.5657894737; the aggregate includes private DeepSeek development cases and is
not a clean final or outer holdout.

V3.2 predeclared Variant A and fit it once on DEV42 plus DeepSeek DEV13. The
`v32-frozen-promotion-shadow7-02` run had a partial label-open failure and
stopped on `NameError("name 'info' is not defined")`; the `-03` run then
completed by reopening those same seven labels. The append-only erratum is
recorded at
`evaluation/runtime/experimental-distillation-v2/v4-deepseek-28-cases-01/v32-shadow7-audit-erratum.json`.
The resulting shadow F1 was **0.3859649123**, precision
0.3142857143, recall 0.5, field accuracy 0.7121212121, and 24 unexpected
tasks. Identity F1 >= 0.57, precision >= 0.50, and recall >= 0.55 all failed.
The shadow7 set is consumed and is not a pristine one-shot or future holdout;
V3.2 is therefore not promoted.

### V4 candidate rescue and student failures

V4.5 assembled a frozen 1,236-candidate cache from DEV42, DS26, and DS27-dev21
(83 meetings). Its candidate oracle had recall 0.8014705882 and oracle F1
**0.8897959184**, with zero candidate-empty cases. This only proves that some
gold-matching rows are present in the pool.

Student selectors could not recover that ceiling. V4.6's best variant reached
F1 0.4949832776 (precision 0.4539877301, recall 0.5441176471). V4.7 reached
F1 0.3076923077; V4.8's best variant reached 0.4714; V4.9's best reached
0.4891. V4.10 stopped before evaluation because the feature array was empty,
and V4.11 stopped during JSON serialization of `UnionProposal` objects. V4.12
persisted base-only F1 **0.3727273**, cascade A F1 **0.4152047**, and cascade B
F1 **0.4077135**, but its status is **STOP** because a post-metric prediction
step raised `NameError("name 'oof_selected' is not defined")`. There is no
complete V4.12 evaluation or manifest; the status evidence is
`evaluation/runtime/experimental-distillation-v2/v412-precision-first-cascade-01/status.json`.
These values are diagnostic evidence only.
These failures show selector/representation loss after candidate generation;
the V4.5 oracle cannot be used as a production result.

### V5 DeepSeek teacher canary and adjudication

The V5.0 canary froze requests and candidate payloads before 12 DeepSeek
provider attempts, opened the 12 labels once afterward, and made no shadow,
final-dev, outer, Kaggle, or second evaluation call. The selected teacher
output had official combined F1 **0.5373134328**, precision **0.5**, recall
**0.5806451613**, field accuracy 0.7592592593, and 36 emitted tasks for 31
expected tasks. F1 >= 0.60 and precision >= 0.55 failed; recall >= 0.58,
response validity, selected-gold ratio, and unknown-ID gates passed.

V5.1 was a read-only alignment audit, not a re-score or relabel. Its
transcript-plus-candidate semantic audit estimated adjusted F1 0.8986 after
deduplication and granularity normalization, while official F1 remained 0.5373.
The adjusted value is diagnostic and cannot replace the official evaluator.
The audit found canonical granularity mismatch, malformed owner spans,
duplicate/split equivalents, omitted actions, and state/date representation
errors. V5.2 validated a blinded 12-case adjudication packet (31 canonical,
36 teacher-selected, 67 deduped union rows); reviewer adjudication is pending.

## Claims supported by the record

The record supports these bounded claims:

1. V2.27 is the best frozen internal research baseline under its DEV42
   template-held-out protocol and declared gates.
2. Candidate generation in V4.5 had a high oracle ceiling, but the tested
   student selectors did not realize it.
3. V3.1 development OOF and V5 teacher output do not transfer to clean
   holdout or production claims; V3.2's consumed shadow is negative evidence
   against that promotion path.
4. The V5 official score is affected by a measurable teacher/canonical contract
   mismatch, which motivates adjudication rather than another unbounded canary.

The record does not support claims of generalization to final-dev18 or outer17,
production uplift, deployed V2.27 inference, human-agreed truth, or superiority
of DeepSeek. No hidden holdout claim is made. Rule-first V1 remains the
production backend; OpenAI fallback is an optional runtime capability and is
not part of the research model freeze.

## Limitations and next permissible action

The datasets are small, family support is uneven, and the task contract is
granularity-sensitive. Oracle metrics conflate availability with selection;
development OOF can still be optimistic under repeated research; and the V5
adjusted audit changes the identity contract. Private artifacts also limit
independent replay from this repository alone.

The freeze permits only a separately dated, separately hashed follow-up. The
next bounded research action is the already specified human adjudication of
the V5 12-case packet, with two independent reviewers, an agreement target of
at least 0.90, at most one unresolved case, and no mutation of canonical
expected outputs. It does not authorize a new provider call or deployment.
