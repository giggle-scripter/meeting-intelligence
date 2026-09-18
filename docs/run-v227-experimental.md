# Run the frozen V2.27 experimental ranker

This path is opt in and local CPU only. It runs the existing V1 rule pipeline
with the AI client disabled, writes a fresh in memory V1 trace, builds the
`final_plus_bridge_plus_intermediate` candidate pool from that trace, and
applies the frozen V2.27 full fit model and modal policy packaged under the
private V2.28 promotion directory.

The command enables V1's deterministic action-candidate and commitment-router
shadow records solely so the frozen runtime candidate chain has its required
trace fields; both remain provider-free and the public V1 extraction is not
replaced.

The result is explicitly experimental. The V2.28 diagnostic gate failed, so
the output includes `experimental.experimental_not_validated: true` and must
not be described as a validated production model. V1 remains the default
route and this command never makes provider calls.

The private artifact directory must contain `model.json`, `frozen-policy.json`,
and `manifest.json` from:

```text
evaluation/runtime/experimental-distillation-v2/v228-frozen-promotion/
```

The runner verifies the model and policy SHA256 values against the V2.28
manifest before processing any transcript. If the directory is absent or an
artifact changes, it stops. These files are intentionally ignored by Git;
retain them in the private runtime directory (or pass a private copy with
`--artifact-directory`) and never add them to source control.

PowerShell example:

```powershell
python scripts/experimental_distillation/run_v227_experimental.py `
  .\new-meeting.vtt `
  --meeting-date 2026-09-18 `
  --meeting-title "Weekly delivery review" `
  --output .\backend\outputs\new-meeting-v227.json
```

`.txt`, `.vtt`, and `.srt` inputs are accepted. `--meeting-date` is preferred;
when it is omitted, the command accepts an explicit ISO or day/month/year date
found on a meeting/date context line in the transcript and otherwise stops. It never uses a case ID,
validation metadata, expected output, or a hardcoded date fallback.

The JSON keeps the normal meeting/task fields and adds experimental diagnostics
including artifact hashes, fresh trace hash, candidate and selected counts,
policy context, `provider_call_count: 0`, and the validation warning.
