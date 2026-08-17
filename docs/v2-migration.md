# V2 pipeline migration

V1 remains the production/default path. `backend.app.v2.process_meeting_v2`
is an isolated, deterministic shadow path and must meet the quality gate before
being exposed as the default API behavior.

The V2 boundary is deliberately explicit:

- Transcript parsing happens once, producing globally ordered clauses.
- A clause is primary in exactly one `SegmentV2`; overlap is context only.
- Extraction produces exactly one `PrimaryResolution` for every primary clause.
  Provider errors are execution failures, never `NO_EVENT` decisions.
- `TaskEventV2` is immutable and validates its anchor, source IDs, target, and
  date IDs.
- The global resolver only accepts an ID, explicit label, exact alias, or a
  strong unambiguous action match. Owner, recency, and active-task count are
  never identities.
- The reducer is single-threaded and fail-closed: only `CREATE` adds an entity;
  unresolved mutations are retained as audit IDs.

Use `scripts/evaluate_dataset.py data/validation --pipeline-version v2 --report
<path>` for every shadow run. It writes report metrics and exits nonzero until
task precision is at least 0.90 and recall is at least 0.85. Promote V2 only after
running the reviewed corpus plus a separately held blind set with recorded model
fixtures; live-provider runs should remain outside deterministic CI.
