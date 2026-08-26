# Distilled proposal ranker V1

This package is an offline challenger only. It must not be imported by the
production pipeline, API, ledger, or serializer. The locked protocol is
`config/protocol-v1.json`; runtime data belongs under
`evaluation/runtime/experimental-distillation-v1`, and model artifacts belong
under `artifacts/models/experimental-distillation-v1`.

The experiment uses meeting-grouped nested cross-validation. Human evidence in
an outer validation fold is evaluation-only. Teacher operation defaults to
`cache-only` and therefore performs no network request.
