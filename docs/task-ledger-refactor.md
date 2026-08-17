# V1 Task Ledger refactor — implementation report

Date: 2026-08-13

V1 remains the production path. Power Automate remains orchestration-only.
The refactor introduced stable task identity, alias-aware mutation resolution,
semantic event deduplication, mutation-only AI, deterministic final
reconciliation, trace replay, and resumable benchmark tooling.

## Behavior completed in this iteration

- `RULE_CONTEXT` and `AI` are update-only sources and cannot mint a task.
- Owner assignment unions owners, reassignment replaces them, and commitment
  no longer erases an existing owner.
- Mutation-only AI routing excludes generic commitments, assignments, and
  confirmations. Deterministic accepted coreference uses a concrete pending
  candidate before promotion.
- Deterministic reconciliation now builds and applies guarded merge operations;
  explicit final-list siblings and incompatible owner/deadline states are not
  merged.
- Recap pruning requires `recap_scope == "AUTHORITATIVE"` and at least two
  rows. Generic recap cannot prune by owner.
- Ledger checkpoints and task state retain deadline event/mention history; the
  serializer selects the best explicit phrase for the effective deadline.
- Strong existing-task labels now create internal provisional identities.
  They are eligible for mutation candidate retrieval but hidden from public
  output until deterministic owner/deadline/active evidence promotes them.
  AI deadline mutations are update-only and cannot promote a provisional task.
- AI deadline mutations fail closed when one supplied task ID already contains
  conflicting concrete aliases. This prevents a correct mutation from being
  applied to an identity that previously merged two sibling work items.
- Final active snapshot filtering checks `recap_scope == "AUTHORITATIVE"`
  directly, so the invariant no longer depends on separate regex callers.

## Verification

- Automated tests: 200 passed.
- Local with notes: 86/86 executed, 15 passed, precision 0.4005, recall 0.5668,
  field accuracy 0.8376, 235 unexpected tasks, 0 provider calls.
- Local without notes: 86/86 executed, 16 passed, precision 0.3793, recall
  0.5162, field accuracy 0.8473, 234 unexpected tasks, 0 provider calls.
- Replay source: 82 traces from
  `evaluation/live-v1-20260807T131248Z/traces`.
- Replay result: 82/82 executed, 9 passed, precision 0.4196, recall 0.5732,
  field accuracy 0.8392, 195 unexpected tasks, 0 provider calls.

Legacy replay is no longer a blocking quality gate because its generic AI
creation events pre-date the mutation-only contract. It passes the new safety
gate: 0 execution errors, 0 AI-only minted tasks, and 0 terminal task reopen.
Its precision/recall remain telemetry only.

A provider-free contract dry-run over the four smoke cases produced 17 calls,
always with non-empty candidates, and 0 contract or unknown-ID rejections.

The first live OpenAI smoke was executed on 2026-08-10 with `gpt-5-mini`,
`medium` reasoning and 17/17 successful provider calls (108,129 total tokens).
Against its matched four-case rule-only baseline, precision changed from
0.4545 to 0.4762, recall stayed at 0.7692, field accuracy changed from 0.8833
to 0.8667, and unexpected tasks decreased from 12 to 11. The original runner
correctly stopped before Gate B because total contract rejection was 1 and
unresolved mutation count stayed 21 to 21.

Post-smoke diagnosis found that an accepted target-backed AI mutation and its
unbound rule precursor were both retained. Deduplication now suppresses the
unbound precursor only when the same event type and anchor resolve to exactly
one task ID. Native replay of the paid traces reduced unresolved mutations from
21 to 15 with no new provider call and passed the pinned quality/safety gate:
precision 0.4545, recall 0.7692, field accuracy 0.8667, 12 unexpected tasks,
0 execution errors, 0 unknown task IDs and 0 terminal reopen. Report:
`evaluation/live-v1-20260810T075449Z/native-replay-after-resolution.json`.

Contract diagnostics now split hard structural rejection (invalid source,
anchor or supplied task ID) from safe semantic rejection (for example, an
invalid assignee or non-concrete owner action). The smoke gate remains
zero-tolerance for structural/provider/schema failures; semantic rejections are
reported separately and remain subject to output-quality gates. Reason-level
counters identify invalid source, invalid anchor, unknown task ID, non-concrete
action and invalid assignee.

The second live smoke at `evaluation/live-v1-20260810T082324Z` passed the
quantitative gate. All 17 provider calls succeeded. Precision and recall were
equal to the matched baseline (0.4545 and 0.7692), field accuracy remained
within the allowed smoke drop (0.8833 to 0.8667), unexpected tasks stayed at
12, and unresolved mutations decreased from 21 to 16. There were 0 structural,
provider or execution errors. The one rejected event was a safe semantic
`non_concrete_action` rejection. The run used 109,210 total tokens, including
49,280 cached input tokens.

The evaluator was also corrected to aggregate the new rejection categories
into the final report. The original case checkpoint already contained the
correct values, so the report was regenerated with `--resume` and no additional
provider calls.

The first Gate B run at `evaluation/live-v1-20260810T085248Z` executed all
24 cases with 137/137 successful provider calls and 873,552 total tokens. It
correctly failed: recall improved from 0.5636 to 0.5727 and field accuracy from
0.8091 to 0.8122, but precision decreased from 0.3584 to 0.3580 and unexpected
tasks increased from 111 to 113. Unresolved mutations decreased from 142 to
123; there were 0 structural/provider errors and five safe
`non_concrete_action` rejections.

Only five cases changed public output. AI fixed one missing task and six fields,
but deadline mutations promoted weak provisional labels such as relationship,
status and question sentences into public tasks. Promotion is now fail-closed:
broad provisional IDs remain for replay/navigation, while only concrete
action/deliverable labels enter AI candidate memory or become public. Native
replay of the 137 paid responses then reached precision 0.3663, recall 0.5727,
field accuracy 0.8122 and 109 unexpected tasks without another provider call.

That replay also exposed seven ledger-level unknown task IDs already present in
the original live flow. Contextual/recap deterministic events were previously
added after candidate IDs had been supplied to AI, even when their chronology
preceded the AI window, shifting sequential task IDs in final reduction. V1 now
completes the deterministic identity universe before candidate retrieval, and
the live gate treats any ledger-level unknown supplied ID as a hard contract
failure. Old traces cannot validate the reordered candidate IDs, so a new smoke
must pass before Gate B is repeated. Gate C remains unexecuted.

A fresh Gate B run at `evaluation/live-v1-20260811T020357Z` then completed all
24 cases with 102 provider attempts and 625,120 total tokens. It had zero hard
contract errors and four safe `non_concrete_action` rejections. Recall improved
from 0.5636 to 0.5818 and precision from 0.3669 to 0.3721, but the live runner
correctly failed because unexpected tasks changed from 107 to 108.

Trace diagnosis showed that two deadline-only AI events had promoted concrete
but merely referenced provisional identities into public tasks. AI deadline
events can now enrich ledger history but cannot supply creation authority.
The same run also exposed a confirmed ID containing two distinct sibling
aliases (`tài liệu triển khai` and `kịch bản kiểm thử triển khai`); deadline
updates now fail closed for such an ambiguous ID. Cancellation/rejection remain
allowed and were not broadened by this guard.

Native replay of the exact 102 paid responses on the corrected reducer passes
the blocking Gate B thresholds without another provider call: precision 0.3690,
recall 0.5636, field accuracy 0.8118, unexpected tasks 106 and unresolved
mutations 140, versus baseline 0.3669/0.5636/0.8091/107/142. Contract safety
also passes with zero execution errors, unknown task IDs, AI creation events or
terminal reopen. Canonical evidence:
`evaluation/live-v1-20260811T020357Z/native-replay-safe-deadline-final.json`.
Because both fixes run only after provider output is received, candidate input
and the paid model responses are unchanged; repeating Gate B would only repay
the same provider stage. The guard blocked 22 deadline mutations across 8/24
cases, which shows that sibling work items are still being conflated before AI
candidate retrieval. Gate C remains unexecuted and should wait for a
deterministic identity-splitting improvement; the current Gate B gain is too
small to justify a full paid run.

The deterministic identity-splitting follow-up is now implemented. Creation
assertions use a stricter identity boundary, explicit numbered list rows remain
separate, and candidate-memory reduction runs guarded reconciliation before
ranking. `LedgerTask` now separates authoritative `identity_aliases` from the
larger evidence/linking alias set. Provider candidates expose only the
canonical label and close identity-safe paraphrases; internally conflicting IDs
are excluded from candidate memory altogether.

The new provider-free preflight `scripts/audit_ai_candidate_identity.py` is
also mandatory in `test_v1_live_full.ps1` before Uvicorn/provider startup. On
the exact 24-case Gate B subset it inspected 102 would-be requests and 849
candidate entries with 0 conflicting aliases, 0 empty candidate requests and
0 duplicate IDs. Report:
`evaluation/identity-split-gate-b-candidate-audit.json`.

Full local regression remains controlled. With notes, precision improves from
0.4005 to 0.4040 and recall from 0.5668 to 0.5776; missing tasks decrease from
120 to 117, while unexpected tasks increase by one (235 to 236). Field correct
counts increase from 789 to 800 over an expanded matched-field denominator
(942 to 960), so the ratio changes from 0.8376 to 0.8333. Without notes, all
public metrics remain unchanged. Reports:
`evaluation/identity-split-v1-final-with-notes.json` and
`evaluation/identity-split-v1-final-without-notes.json`.

Because candidate IDs and payload aliases changed before provider execution,
the old 102 responses cannot be used as a native quality replay for this
version. The replacement live smoke at
`evaluation/live-v1-20260811T035647Z` passed its quantitative gate. Its
provider-free preflight inspected 8 would-be requests and 57 candidate entries
with no conflict, empty request, duplicate ID or execution error. All 8
`gpt-5-mini` calls succeeded with 0 structural/semantic rejection. Compared
with the matched rule-only baseline, precision and recall stayed at 0.5000 and
0.8462, field accuracy improved from 0.8485 to 0.8636, unexpected tasks stayed
at 11, and unresolved mutations decreased from 21 to 16. Total usage was
51,736 tokens. Prompt and Power Automate remain unchanged.

The replacement Gate B at `evaluation/live-v1-20260811T041054Z` passed all
blocking thresholds: precision 0.3721 to 0.3832, recall held at 0.5818, field
accuracy 0.8021 to 0.8073, unexpected tasks 108 to 103, and unresolved
mutations 142 to 117. All 102 provider calls succeeded.

Gate C then executed all 86 cases at
`evaluation/live-v1-20260813T034446Z` (134 successful calls, 801,996 tokens).
The raw result failed because precision and recall fell from 0.4040/0.5776 to
0.4015/0.5668. The regression was confined to post-provider reduction:
discussion-section closure was treated as task cancellation, same-anchor AI
deadlines overrode local recap identity, and dedup discarded deterministic
deadline authority after AI supplied the target ID.

The reducer now rejects discourse-only cancellation, treats a same-anchor local
recap deadline as authoritative, and records rule corroboration without giving
AI standalone creation authority. A corroborated AI deadline may promote a
provisional task only after at least two deterministic references. Since these
guards do not change candidate payloads, prompt, or paid responses, native
replay is valid and passes the full blocking thresholds: precision 0.4091,
recall 0.5848, field accuracy 0.8333, unexpected 234, unresolved mutations 173,
and no contract-safety failure. Report:
`evaluation/live-v1-20260813T034446Z/native-replay-gate-c-fixes-final.json`.
The post-fix provider-free audit still has the same 86 cases, 134 requests,
949 candidate entries and per-case audit payload as the pre-paid-run artifact,
with no conflicts, empty candidates, duplicates or execution errors. Report:
`evaluation/post-gate-c-full-candidate-audit.json`. The backend suite now
passes 204/204 tests. Power Automate remains unchanged.

The live runner pins model, reasoning effort and prompt version into both the
matched baseline and live report. It also enforces field-accuracy floors:
maximum tolerated drop is 0.02 for smoke, 0.01 for Gate B and 0 for full.
Gate B additionally requires a strict improvement in at least one public
quality metric (precision, recall, field accuracy or unexpected-task count),
not only a reduction in the internal unresolved counter.

Full deterministic A/B regression after the promotion guard keeps recall and
field accuracy unchanged and removes four false positives in each mode. Reports:
`evaluation/post-gate-b-guard-with-notes.json` and
`evaluation/post-gate-b-guard-without-notes.json`.

## Important interpretation

The old traces contain correct tasks that exist only as generic AI task-create
events or context-only owner assignments. They remain useful for reducer safety,
but not for contract-native quality. A live trace created after this refactor
must be replayed with `--contract-mode native` and pinned thresholds.

The critical regression `W4-LONG-C5-N1-SW-STATE-009` now passes its targeted
rule-only evaluation: it links the Vietnamese/English dataset aliases, cancels
the correct task, unions both document owners, applies the replacement deadline
and full raw deadline phrase, and retains monitoring/release-plan work.

## Commands

```powershell
.\.venv\Scripts\python.exe scripts\replay_v1_traces.py `
  --trace-dir evaluation\live-v1-20260807T131248Z\traces `
  --contract-mode legacy `
  --report evaluation\legacy-replay-provisional-safety.json

.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 --context-mode assist `
  --report evaluation\provisional-v1-final-with-notes.json

.\.venv\Scripts\python.exe scripts\evaluate_dataset.py data\validation `
  --pipeline-version v1 --context-mode assist --without-meeting-notes `
  --report evaluation\provisional-v1-final-without-notes.json

# Requires OPENAI_API_KEY; rerun for the new candidate-ID contract:
.\scripts\test_v1_live_full.ps1 -Scope smoke

# Run only after the new smoke passes:
.\scripts\test_v1_live_full.ps1 -Scope gate-b

# Run only after the new Gate B passes:
.\scripts\test_v1_live_full.ps1 -Scope full
```
