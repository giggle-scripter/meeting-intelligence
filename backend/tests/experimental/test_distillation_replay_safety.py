from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.candidate.proposal import TaskCreateProposal
from backend.app.models import Clause, ClauseAnnotation
from backend.app.verification.proposal_validator import validate_task_create_proposal
from experiments.distilled_proposal_ranker.contracts import GroundedSpan, ProposalRecord, TypedRelation
from experiments.distilled_proposal_ranker.hashing import completed_manifest_reusable
from experiments.distilled_proposal_ranker.replay import experimental_create_guard, replay_case, structured_acceptance_overrides
from experiments.distilled_proposal_ranker.safety import SAFETY_COUNTERS, SafetyReport, enforce_zero_safety


def _clauses() -> dict[str, Clause]:
    return {
        "C1": Clause("C1", "S1", "A", "Lan", 0, 1, "Làm báo cáo?", "làm báo cáo?", [], 0),
        "C2": Clause("C2", "S2", "B", "Minh", 2, 3, "Đồng ý", "đồng ý", [], 1),
        "CX": Clause("CX", "SX", "A", "Lan", 1, 2, "Kiểm tra việc khác", "kiểm tra việc khác", [], 1),
    }


def _proposal(kind: str = "CREATE", flags: list[str] | None = None) -> tuple[ProposalRecord, dict]:
    relation = TypedRelation(relation_type="ACCEPTS", nucleus_clause_id="C1", support_clause_id="C2", distance=1, chronology="AFTER")
    proposal = ProposalRecord(
        proposal_id="P1", input_hash="0" * 64, case_id="CASE", kind=kind,
        cluster_identity="CL1", action_span=GroundedSpan(clause_id="C1", start=0, end=3, text="Làm"),
        authority_refs=["C2"], acceptance_refs=["C2"], owner_refs=[], deadline_refs=[], negative_refs=[],
        relations=[relation], deterministic_score=1, deterministic_reasons=[], neural_span_score=1,
        source_type="BOTH", ambiguity_flags=[], order_index=0,
    )
    trace = {
        "clauses": [item.__dict__ for item in _clauses().values()],
        "annotations": {
            "C1": {"flags": flags or ["ROOT_QUESTION"]},
            "C2": {"flags": ["CONFIRMATION"]},
            "CX": {"flags": ["ACTION_VERB"]},
        },
        "proposal_evidence_seeds_v3": {"records": [
            {"seed_id": "S-C1", "clause_id": "C1", "order_index": 0, "roles": ["ACTION"]},
            {"seed_id": "S-C2", "clause_id": "C2", "order_index": 1, "roles": []},
        ]},
    }
    return proposal, trace


def _candidate() -> TaskCreateProposal:
    return TaskCreateProposal(
        source_clause_ids=["C1", "C2"], action_span="Làm", owner_span=None,
        deadline_mention_id=None, commitment_type="CONFIRMED_ACTION", confidence=1.0,
    )


def test_production_default_is_invariant_and_question_stays_rejected() -> None:
    clauses = _clauses()
    annotations = {"C1": ClauseAnnotation("C1", {"ROOT_QUESTION"}), "C2": ClauseAnnotation("C2", {"CONFIRMATION"})}
    default = validate_task_create_proposal(_candidate(), clauses_by_id=clauses, annotations=annotations, mentions={}, start_sequence=0)
    explicit_none = validate_task_create_proposal(_candidate(), clauses_by_id=clauses, annotations=annotations, mentions={}, start_sequence=0, structured_acceptance_overrides=None)
    assert default == explicit_none
    assert default.accepted is False and "QUESTION" in default.reasons


@pytest.mark.parametrize("primary_flag", ["ROOT_QUESTION", "SUGGESTION_ONLY"])
def test_structured_later_acceptance_can_override_only_question_or_suggestion(primary_flag: str) -> None:
    proposal, trace = _proposal(flags=[primary_flag])
    overrides = structured_acceptance_overrides(proposal, trace)
    reason = "QUESTION" if primary_flag == "ROOT_QUESTION" else "SUGGESTION"
    assert reason in overrides["C1"]
    assert overrides["C1"] <= {"QUESTION", "SUGGESTION"}
    annotations = {key: ClauseAnnotation(key, set(value["flags"])) for key, value in trace["annotations"].items()}
    result = validate_task_create_proposal(
        _candidate(), clauses_by_id=_clauses(), annotations=annotations, mentions={}, start_sequence=0,
        required_primary_clause_ids={"C1"}, allowed_source_clause_ids={"C1", "C2"}, structured_acceptance_overrides=overrides,
    )
    assert result.accepted


def test_unaccepted_rejected_and_cross_task_acceptance_fail_closed() -> None:
    proposal, trace = _proposal()
    trace["annotations"]["C2"]["flags"] = ["REJECTION", "CONFIRMATION"]
    assert structured_acceptance_overrides(proposal, trace) == {}
    trace["annotations"]["C2"]["flags"] = ["CONFIRMATION"]
    trace["clauses"][1]["order_index"] = 2
    trace["proposal_evidence_seeds_v3"]["records"].insert(1, {"seed_id": "S-X", "clause_id": "CX", "order_index": 1, "roles": ["ACTION"]})
    trace["proposal_evidence_seeds_v3"]["records"][-1]["order_index"] = 2
    proposal = proposal.model_copy(update={"relations": [proposal.relations[0].model_copy(update={"distance": 2})]})
    assert structured_acceptance_overrides(proposal, trace) == {}


@pytest.mark.parametrize(
    ("kind", "flags", "authority", "ambiguity", "expected"),
    [
        ("CREATE", ["ROOT_QUESTION"], True, False, "QUESTION_OR_SUGGESTION_WITHOUT_ACCEPTANCE"),
        ("CREATE", ["SUGGESTION_ONLY"], True, False, "QUESTION_OR_SUGGESTION_WITHOUT_ACCEPTANCE"),
        ("CREATE", ["PAST_COMPLETED"], True, False, "NEGATIVE_LIFECYCLE_GUARD"),
        ("CREATE", ["RECAP_ITEM"], True, False, "NEGATIVE_LIFECYCLE_GUARD"),
        ("UPDATE", [], True, False, "NON_CREATE_CANNOT_MINT_IDENTITY"),
        ("CREATE", [], False, False, "MISSING_TRANSCRIPT_AUTHORITY"),
        ("CREATE", [], True, True, "SIBLING_AMBIGUITY"),
    ],
)
def test_negative_create_semantics_are_rejected(kind, flags, authority, ambiguity, expected) -> None:
    proposal, trace = _proposal(kind=kind, flags=flags)
    if not authority:
        proposal = proposal.model_copy(update={"authority_refs": [], "acceptance_refs": []})
    if ambiguity:
        proposal = proposal.model_copy(update={"ambiguity_flags": ["SIBLING_AMBIGUITY"]})
    if flags in (["ROOT_QUESTION"], ["SUGGESTION_ONLY"]):
        trace["annotations"]["C2"]["flags"] = []
    assert experimental_create_guard(proposal, trace) == expected


@pytest.mark.parametrize("counter", SAFETY_COUNTERS)
def test_every_safety_counter_fails_closed(counter: str) -> None:
    report = SafetyReport()
    report.violation(counter, "fixture")
    with pytest.raises(ValueError, match="SAFETY_GATE_FAILED"):
        enforce_zero_safety(report.model_dump())


def test_checkpoint_signature_mismatch_is_not_resumed(tmp_path) -> None:
    path = tmp_path / "run-manifest.json"
    path.write_text(json.dumps({"status": "complete", "signature": {"code_sha": "old"}}), encoding="utf-8")
    assert completed_manifest_reusable(path, {"code_sha": "new"}) is False
    assert completed_manifest_reusable(path, {"code_sha": "old"}) is True


def test_offline_replay_without_challenger_is_q2_task_invariant() -> None:
    root = Path.cwd()
    safety = SafetyReport()
    for path in sorted((root / "evaluation/runtime/pr29-q2-traces").glob("*-v1-*.json")):
        trace = json.loads(path.read_text(encoding="utf-8-sig"))
        public, _sidecar = replay_case(root, trace["meeting_id"], trace, [], safety)
        assert public["tasks"] == trace["final_tasks"]
    enforce_zero_safety(safety.model_dump())
