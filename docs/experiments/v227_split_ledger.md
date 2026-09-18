# Current split ledger for the V2.27 freeze

This ledger clarifies split provenance across the completed V2.27 freeze and
the later protocol runs. It is documentation outside the immutable
`evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze/`
package and does not alter that package or its hashes.

## Boundary and chronology

| Run | Development scope | Diagnostic9 | Final-dev18 | Outer17 | Evidence |
|---|---|---|---|---|---|
| V2.24 | DEV42 = TRAIN34 + calibration8 | Read once | 0 reads | 0 reads | [`v224 metrics`](../../evaluation/runtime/experimental-distillation-v2/v224-dev42-crossfit-gate/metrics.json) (`sha256: ec957859797fa686d6bccab20b6487fd6fce685b9fc402064abc335f3ad4e4da`); [`v224 split audit`](../../evaluation/runtime/experimental-distillation-v2/v224-dev42-crossfit-gate/split-access-audit.json) (`sha256: 47a6c95b88771651a60bca20d1e5d2048762456f74c384306c69b27c2cf5acce`) |
| V2.27 source run | DEV42 = TRAIN34 + calibration8 | 0 reads | 0 reads | 0 reads | [`v227 metrics`](../../evaluation/runtime/experimental-distillation-v2/v227-dev42-volume-adaptive-policy/metrics.json) (`sha256: 05dfff5486df84d0b9a7333a76bc4c94c813f49acdc42e30ab168f0497f9c86e`); [`v227 split audit`](../../evaluation/runtime/experimental-distillation-v2/v227-dev42-volume-adaptive-policy/split-access-audit.json) (`sha256: 8011cd721eb5e82b7e55dae337b51108ac48894c90bed054b474d0bcaeeeb132`) |
| V2.28 | DEV42 | Read once as confirmation; prior consumption is V2.24 | 0 reads | 0 reads | [`v228 metrics`](../../evaluation/runtime/experimental-distillation-v2/v228-frozen-promotion/metrics.json) (`sha256: 668029ad1cf1c36e662df7ed03ab56272101f9bf44e9cb9036af1e8ecd33e500`); [`v228 manifest`](../../evaluation/runtime/experimental-distillation-v2/v228-frozen-promotion/manifest.json) (`sha256: 7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3`) |
| V2.29 | DEV51 = TRAIN34 + calibration8 + diagnostic9 | Included in development (9 reads) | 0 reads | 0 reads | [`v229 metrics`](../../evaluation/runtime/experimental-distillation-v2/v229-dev51-final-gate/metrics.json) (`sha256: 8a302d35b6d227ac9520e43d113fee2b7743b83b32633d2a96d666c942bbe9bb`); [`v229 manifest`](../../evaluation/runtime/experimental-distillation-v2/v229-dev51-final-gate/manifest.json) (`sha256: 7b8a5cdc52ce5307b89422290977abe56edab0c0bcdbc84a1e4174f18921fac5`) |

In chronological terms, V2.24 evaluated diagnostic9 once; V2.28 evaluated it
once again as a confirmation of that prior consumption; and V2.29 includes it
in DEV51. Final-dev18 and outer17 remain unopened.

The V2.27 canonical package remains bounded to its source run: DEV42
leave-template-out, with diagnostic9, final-dev18, and outer17 closed. The
canonical package manifest is [`manifest.json`](../../evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze/manifest.json)
(`sha256: 480e3648141c7d0f422ffef9dbf86d5be6505b3b8dbd8ccebc0942704d1e8099`).
The later V2.24 and V2.28 diagnostic reads, and V2.29's DEV51 inclusion, are
chronological split history rather than V2.27 validation evidence. Final-dev18
and outer17 remain unopened throughout this sequence.

The focused consistency check is
[`tests/test_v227_split_ledger.py`](../../tests/test_v227_split_ledger.py).
