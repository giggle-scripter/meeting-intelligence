from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
LEDGER = ROOT / "docs/experiments/v227_split_ledger.md"


def _load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _sha256(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_v227_split_ledger_matches_recorded_scope_and_hashes() -> None:
    ledger = LEDGER.read_text(encoding="utf-8")
    expected_hashes = {
        "evaluation/runtime/experimental-distillation-v2/v227-dev42-volume-adaptive-policy/metrics.json": "05dfff5486df84d0b9a7333a76bc4c94c813f49acdc42e30ab168f0497f9c86e",
        "evaluation/runtime/experimental-distillation-v2/v227-dev42-volume-adaptive-policy/split-access-audit.json": "8011cd721eb5e82b7e55dae337b51108ac48894c90bed054b474d0bcaeeeb132",
        "evaluation/runtime/experimental-distillation-v2/v224-dev42-crossfit-gate/split-access-audit.json": "47a6c95b88771651a60bca20d1e5d2048762456f74c384306c69b27c2cf5acce",
    }
    for relative, expected in expected_hashes.items():
        assert _sha256(relative) == expected
        assert expected in ledger

    v227 = _load(
        "evaluation/runtime/experimental-distillation-v2/"
        "v227-dev42-volume-adaptive-policy/metrics.json"
    )
    assert v227["scope"]["development_meetings"] == 42
    assert v227["scope"]["diagnostic_opened"] is False
    assert v227["scope"]["final_dev_opened"] is False
    assert v227["scope"]["outer_validation_opened"] is False
    assert v227["split_access"]["diagnostic_case_ids_read"] == []
    assert v227["split_access"]["final_dev_case_ids_read"] == []
    assert v227["split_access"]["outer_validation_case_ids_read"] == []


def test_later_split_history_is_consistent_with_the_ledger() -> None:
    ledger = LEDGER.read_text(encoding="utf-8")
    v224 = _load(
        "evaluation/runtime/experimental-distillation-v2/"
        "v224-dev42-crossfit-gate/split-access-audit.json"
    )
    assert v224["read_counts"] == {
        "train34": 34,
        "calibration8": 8,
        "development42": 42,
        "diagnostic9": 9,
        "final_dev": 0,
        "outer_validation": 0,
    }
    v228 = _load(
        "evaluation/runtime/experimental-distillation-v2/"
        "v228-frozen-promotion/metrics.json"
    )
    assert v228["diagnostic9"]["opened"] is True
    assert v228["diagnostic9"]["prior_consumption"] == "V2.24 diagnostic9 postmortem"
    assert v228["split_access"]["final_dev_case_ids_read"] == []
    assert v228["split_access"]["outer_validation_case_ids_read"] == []
    v229 = _load(
        "evaluation/runtime/experimental-distillation-v2/"
        "v229-dev51-final-gate/metrics.json"
    )
    assert v229["scope"]["development_meetings"] == 51
    assert v229["scope"]["diagnostic_retired_into_development"] is True
    assert len(v229["split_access"]["diagnostic_case_ids_read"]) == 9
    assert v229["split_access"]["final_dev_case_ids_read"] == []
    assert v229["split_access"]["outer_validation_case_ids_read"] == []
    for phrase in (
        "V2.24 evaluated diagnostic9 once",
        "V2.28 evaluated it",
        "once again as a confirmation",
        "V2.29 includes it",
        "in DEV51",
        "Final-dev18 and outer17 remain unopened",
    ):
        assert phrase in ledger
