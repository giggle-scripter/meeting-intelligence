from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from experiments.distilled_proposal_ranker.contracts import DistillationProtocol, load_protocol
from experiments.distilled_proposal_ranker.hashing import (
    canonical_json_hash,
    require_model_output,
    require_runtime_output,
)


PROTOCOL = Path("experiments/distilled_proposal_ranker/config/protocol-v1.json")


def test_locked_protocol_loads_and_hashes() -> None:
    snapshot = load_protocol(PROTOCOL)
    assert snapshot.protocol.expected_cases == 86
    assert len(snapshot.sha256) == 64
    assert canonical_json_hash({"b": 1, "a": "đ"}) == canonical_json_hash({"a": "đ", "b": 1})


def test_protocol_rejects_extra_and_changed_grid() -> None:
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        DistillationProtocol.model_validate(payload)
    payload.pop("unexpected")
    payload["threshold_grid"] = [0.5]
    with pytest.raises(ValidationError, match="threshold_grid"):
        DistillationProtocol.model_validate(payload)


def test_output_paths_fail_closed_outside_locked_roots(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    assert require_runtime_output(repo, Path("evaluation/runtime/experimental-distillation-v1/reports/a.json"))
    assert require_model_output(repo, Path("artifacts/models/experimental-distillation-v1/a.bin"))
    with pytest.raises(ValueError):
        require_runtime_output(repo, Path("evaluation/runtime/other/a.json"))
    with pytest.raises(ValueError):
        require_model_output(repo, Path("models/a.bin"))


def test_experiment_import_does_not_eagerly_import_ml_frameworks() -> None:
    for name in [name for name in sys.modules if name == "torch" or name.startswith("transformers")]:
        sys.modules.pop(name)
    module = importlib.reload(importlib.import_module("experiments.distilled_proposal_ranker"))
    assert module is not None
    assert "torch" not in sys.modules
    assert not any(name.startswith("transformers") for name in sys.modules)
