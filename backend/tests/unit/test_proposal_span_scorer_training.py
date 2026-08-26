import importlib.util
from pathlib import Path


def _trainer_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "train_proposal_span_scorer_v3.py"
    spec = importlib.util.spec_from_file_location("proposal_span_scorer_training", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(label: int, variant: str) -> dict:
    return {"span": {"text": "viết báo cáo" if label else "viết"}, "span_variant": variant, "semantic_score": 1.0 if label else 0.0, "ranking_score": 1.0 if label else 0.5, "nucleus_roles": ["ACTION", "AUTHORITY"], "nucleus_flags": ["ACTION_VERB"], "exact_span_label": label}


def test_trainer_fits_weighted_binary_signal_deterministically() -> None:
    module = _trainer_module()
    rows = [_row(1, "FULL_BOUNDARY") for _ in range(3)] + [_row(0, "TOKEN_PREFIX") for _ in range(9)]
    weights, intercept = module._fit(rows, epochs=30)
    metrics = module._metrics(rows, weights, intercept, 0.5)

    assert weights["variant=FULL_BOUNDARY"] > weights["variant=TOKEN_PREFIX"]
    assert metrics["matched"] == 3


def test_trainer_is_invariant_to_correlated_candidate_row_order() -> None:
    module = _trainer_module()
    rows = [_row(1, "FULL_BOUNDARY") for _ in range(3)] + [_row(0, "TOKEN_PREFIX") for _ in range(9)]

    forward = module._fit(rows, epochs=30)
    reverse = module._fit(list(reversed(rows)), epochs=30)

    assert forward == reverse
