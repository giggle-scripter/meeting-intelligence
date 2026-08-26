import importlib.util
from pathlib import Path


def _builder_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "build_proposal_supervision_corpus_v3.py"
    spec = importlib.util.spec_from_file_location("proposal_supervision_corpus", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corpus_builder_preserves_wave_split_and_exact_label() -> None:
    module = _builder_module()
    evidence = [{"case_id": "W1-CASE", "action_evidence": {"clause_id": "C1", "start": 0, "end": 4, "text": "làm x"}}]
    traces = {"W1-CASE": {
        "proposal_clusters_v3": {"records": [{"cluster_id": "CL1", "nucleus_seed_id": "S1"}]},
        "proposal_evidence_seeds_v3": {"records": [{"seed_id": "S1", "roles": ["ACTION"], "flags": []}]},
        "proposal_semantic_scores_v3": {"records": [{"identity_key": "P1", "primary_clause_id": "C1", "score": 0.9, "reasons": []}]},
        "proposal_ranking_v3": {"records": [{"identity_key": "P1", "primary_clause_id": "C1", "score": 0.9, "reasons": []}]},
        "proposal_span_identities_v3": {"records": [{"identity_key": "P1", "cluster_id": "CL1", "primary_clause_id": "C1", "action_span": {"clause_id": "C1", "start": 0, "end": 4, "text": "làm x"}}]},
    }}

    rows, errors = module.build_rows(
        evidence, traces, train_waves={"W1"}, calibration_waves={"W4"}, test_waves={"W5"},
    )

    assert errors == []
    assert rows[0]["split"] == "train"
    assert rows[0]["exact_span_label"] == 1
    assert rows[0]["source_clause_label"] == 1
