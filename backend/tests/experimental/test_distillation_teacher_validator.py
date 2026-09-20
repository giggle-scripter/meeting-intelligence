from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from experiments.distilled_proposal_ranker.contracts import (
    GroundedSpan,
    TeacherClause,
    TeacherDecisionRecord,
    TeacherPayload,
    TeacherProposal,
    TeacherResponse,
)
from experiments.distilled_proposal_ranker.teacher_client import TeacherClient
from experiments.distilled_proposal_ranker.teacher_consensus import teacher_consensus
from experiments.distilled_proposal_ranker.teacher_prompt import prompt_hash
from experiments.distilled_proposal_ranker.teacher_validator import validate_teacher_response


MODEL = "fake-model"


def _payload() -> TeacherPayload:
    return TeacherPayload(
        payload_id="payload",
        input_hash="a" * 64,
        case_id="CASE",
        outer_fold=0,
        shard_index=0,
        clauses=[
            TeacherClause(clause_id="C1", speaker="Lan", order_index=0, text_raw="Làm báo cáo"),
            TeacherClause(clause_id="C2", speaker="Minh", order_index=1, text_raw="Đồng ý"),
        ],
        proposals=[
            TeacherProposal(
                proposal_id="P1",
                action_span=GroundedSpan(clause_id="C1", start=0, end=3, text="Làm"),
                kind="CREATE",
                authority_refs=["C2"],
                acceptance_refs=["C2"],
                owner_refs=[],
                deadline_refs=["D1"],
                negative_refs=[],
                relation_types=["ACCEPTS"],
            )
        ],
        allowed_date_mention_ids=["D1"],
        allowed_existing_task_refs=["T1"],
    )


def _response(version: str = "teacher-a-v1", decision: str = "CREATE", confidence: float = 0.95) -> TeacherResponse:
    client_hash = TeacherClient.model_hash(MODEL)
    return TeacherResponse(
        payload_id="payload",
        prompt_version=version,
        payload_hash="a" * 64,
        prompt_hash=prompt_hash(version),
        model_hash=client_hash,
        records=[
            TeacherDecisionRecord(
                proposal_id="P1",
                decision=decision,
                action_span_ref="P1",
                authority_clause_ids=["C2"],
                owner_clause_id=None,
                deadline_mention_id="D1",
                existing_task_ref="T1" if decision == "UPDATE" else None,
                confidence=confidence,
                reason_codes=["QUESTION_ACCEPTED"],
            )
        ],
    )


def test_valid_grounded_response_is_accepted() -> None:
    response = _response()
    assert validate_teacher_response(
        response,
        _payload(),
        expected_prompt_hash=prompt_hash("teacher-a-v1"),
        expected_model_hash=TeacherClient.model_hash(MODEL),
    ) is response


def test_extra_field_and_free_form_rationale_are_rejected() -> None:
    raw = _response().model_dump(mode="json")
    raw["records"][0]["unexpected"] = True
    with pytest.raises(ValidationError):
        TeacherResponse.model_validate(raw)
    raw["records"][0].pop("unexpected")
    raw["records"][0]["rationale"] = "hidden free-form text"
    with pytest.raises(ValidationError):
        TeacherResponse.model_validate(raw)


def test_unknown_deadline_and_update_target_are_rejected() -> None:
    raw = _response().model_dump(mode="json")
    raw["records"][0]["deadline_mention_id"] = "UNKNOWN"
    with pytest.raises(ValueError, match="deadline"):
        validate_teacher_response(
            TeacherResponse.model_validate(raw), _payload(),
            expected_prompt_hash=prompt_hash("teacher-a-v1"), expected_model_hash=TeacherClient.model_hash(MODEL),
        )
    response = _response(decision="UPDATE")
    raw = response.model_dump(mode="json")
    raw["records"][0]["existing_task_ref"] = "UNKNOWN"
    with pytest.raises(ValueError, match="task reference"):
        validate_teacher_response(
            TeacherResponse.model_validate(raw), _payload(),
            expected_prompt_hash=prompt_hash("teacher-a-v1"), expected_model_hash=TeacherClient.model_hash(MODEL),
        )


def test_teacher_disagreement_becomes_unresolved() -> None:
    labels = teacher_consensus([_response("teacher-a-v1", "CREATE"), _response("teacher-b-v1", "DROP")])
    assert labels[0].decision == "UNRESOLVED"
    assert labels[0].source_label == "unresolved"


def test_provider_missing_budget_fails_before_fake_network(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    called = False

    def provider(_body, _headers):
        nonlocal called
        called = True
        return {}

    monkeypatch.setenv("DISTILL_TEACHER_MODE", "provider")
    monkeypatch.setenv("DISTILL_ALLOW_PROVIDER_CALLS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.delenv("DISTILL_MAX_CALLS", raising=False)
    with pytest.raises(ValueError, match="budget"):
        TeacherClient("provider", tmp_path, provider=provider)
    assert called is False


def test_explicitly_budgeted_fake_provider_is_validated_and_cached(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for name, value in {
        "DISTILL_TEACHER_MODE": "provider",
        "DISTILL_ALLOW_PROVIDER_CALLS": "1",
        "OPENAI_API_KEY": "fake",
        "DISTILL_TEACHER_MODEL": MODEL,
        "DISTILL_MAX_CALLS": "1",
        "DISTILL_MAX_INPUT_CHARS": "20000",
        "DISTILL_MAX_ESTIMATED_USD": "10",
        "DISTILL_INPUT_USD_PER_1M": "1",
        "DISTILL_CACHED_INPUT_USD_PER_1M": "1",
        "DISTILL_OUTPUT_USD_PER_1M": "1",
    }.items():
        monkeypatch.setenv(name, value)
    calls = []

    def provider(body, headers):
        calls.append((body, headers))
        return _response().model_dump(mode="json")

    client = TeacherClient("provider", tmp_path, provider=provider)
    response = client.get(_payload(), "teacher-a-v1", model=MODEL)
    assert response is not None
    assert client.calls == 1
    assert calls[0][0]["store"] is False
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_cache_hash_mismatch_is_not_reused(tmp_path) -> None:
    payload = _payload()
    client = TeacherClient("cache-only", tmp_path)
    request_hash = client.request_hash(payload, "teacher-a-v1", MODEL)
    wrong = _response().model_copy(update={"payload_hash": "b" * 64})
    (tmp_path / f"{request_hash}.json").write_text(wrong.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="payload hash mismatch"):
        client.get(payload, "teacher-a-v1", model=MODEL)
