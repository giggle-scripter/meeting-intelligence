"""Exact-first and margin-gated semantic task retrieval tests."""

from __future__ import annotations

from backend.app.ml.contracts import EmbeddingModelMetadata
from backend.app.ml.embeddings import HashingEmbeddingModel
from backend.app.models import Clause, TaskEvent
from backend.app.retrieval import (
    MutationQuery,
    TaskIndex,
    TaskLinkScoringConfig,
    TaskRepresentation,
    TaskRetriever,
)
from backend.app.retrieval.shadow import evaluate_task_linker_shadow


class KeywordEmbedding:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.metadata = EmbeddingModelMetadata(
            requested_model="keyword-test",
            model_name="keyword-test",
            model_version="v1",
            backend="test",
            dimension=2,
        )

    def embed(self, texts):
        values = list(texts)
        self.calls.append(values)
        result = []
        for text in values:
            normalized = text.casefold()
            if "analytics" in normalized:
                result.append([1.0, 0.0])
            elif "frontend" in normalized:
                result.append([0.98, 0.02])
            else:
                result.append([0.0, 1.0])
        return result


def _task(
    task_id: str,
    action: str,
    *,
    aliases: tuple[str, ...] | None = None,
    order: int = 1,
) -> TaskRepresentation:
    return TaskRepresentation(
        task_id=task_id,
        action=action,
        aliases=aliases or (action,),
        owners=("Lan",),
        topic_ids=("metrics",),
        entities=tuple(action.casefold().split()),
        created_order_index=order,
        last_order_index=order,
    )


def _query(**overrides) -> MutationQuery:
    values = {
        "action_ref": "analytics dashboard",
        "mutation_text": "deadline analytics dashboard chuyển sang thứ Hai",
        "speaker": "Lan",
        "owner_refs": ("Lan",),
        "topic_ids": ("metrics",),
        "order_index": 10,
    }
    values.update(overrides)
    return MutationQuery(**values)


def test_exact_task_id_precedes_all_similarity() -> None:
    index = TaskIndex(KeywordEmbedding())
    index.sync([_task("TASK-1", "payment API"), _task("TASK-2", "dashboard analytics")])

    result = TaskRetriever(index).retrieve(
        _query(explicit_task_id="TASK-1")
    )

    assert result.status == "DIRECT_LINK"
    assert result.task_id == "TASK-1"
    assert result.reason == "EXACT_TASK_ID"


def test_unique_exact_alias_precedes_embedding() -> None:
    model = KeywordEmbedding()
    index = TaskIndex(model)
    index.sync([_task("TASK-1", "dashboard analytics")])
    calls_before_query = len(model.calls)

    result = TaskRetriever(index).retrieve(
        _query(action_ref="dashboard analytics")
    )

    assert result.reason == "EXACT_ALIAS"
    assert result.task_id == "TASK-1"
    assert len(model.calls) == calls_before_query


def test_semantic_link_requires_strong_score_and_margin() -> None:
    index = TaskIndex(KeywordEmbedding())
    index.sync(
        [
            _task("TASK-1", "dashboard analytics"),
            _task("TASK-2", "payment API"),
        ]
    )

    result = TaskRetriever(index).retrieve(_query())

    assert result.status == "DIRECT_LINK"
    assert result.task_id == "TASK-1"
    assert result.reason in {"LEXICAL_DIRECT_LINK", "SEMANTIC_DIRECT_LINK"}
    assert result.margin >= 0.12


def test_close_sibling_scores_never_auto_link() -> None:
    index = TaskIndex(KeywordEmbedding())
    index.sync(
        [
            _task("TASK-1", "dashboard analytics"),
            _task("TASK-2", "dashboard frontend"),
        ]
    )

    result = TaskRetriever(index).retrieve(_query())

    assert result.status == "AI_MUTATION_CHECK"
    assert result.task_id is None
    assert result.margin < 0.12


def test_duplicate_exact_alias_is_ambiguous_not_merged() -> None:
    index = TaskIndex(KeywordEmbedding())
    index.sync(
        [
            _task("TASK-1", "dashboard analytics"),
            _task("TASK-2", "dashboard frontend", aliases=("dashboard frontend", "dashboard analytics")),
        ]
    )

    result = TaskRetriever(index).retrieve(
        _query(action_ref="dashboard analytics")
    )

    assert result.status == "AI_MUTATION_CHECK"
    assert result.task_id is None
    assert result.reason == "MULTIPLE_EXACT_ALIAS_MATCHES"


def test_index_embeds_only_new_or_changed_task_representations() -> None:
    model = KeywordEmbedding()
    index = TaskIndex(model)
    original = _task("TASK-1", "dashboard analytics")

    index.sync([original])
    index.sync([original])
    changed = original.model_copy(update={"owners": ("Lan", "Minh")})
    index.sync([changed])

    assert [len(batch) for batch in model.calls] == [1, 1]


def test_invalid_weight_sum_is_rejected() -> None:
    try:
        TaskLinkScoringConfig(semantic_weight=0.50)
    except ValueError as exc:
        assert "sum to one" in str(exc)
    else:
        raise AssertionError("invalid weights must fail")


def test_shadow_replay_never_retrieves_a_future_sibling_task() -> None:
    clauses = {
        f"C-{index}": Clause(
            f"C-{index}",
            f"S-{index}",
            "SPK",
            "Lan",
            None,
            None,
            text,
            text.casefold(),
            order_index=index,
        )
        for index, text in (
            (1, "Tạo dashboard analytics"),
            (2, "Dời dashboard analytics"),
            (3, "Tạo dashboard analytics bản khác"),
        )
    }
    events = [
        TaskEvent(
            "E-1", "TASK_CREATE", ["C-1"], "dashboard analytics", "Lan",
            extraction_source="RULE", order_index=1,
        ),
        TaskEvent(
            "E-2", "DEADLINE_REPLACE", ["C-2"],
            related_task_hint="dashboard analytics", extraction_source="RULE",
            order_index=2,
        ),
        TaskEvent(
            "E-3", "TASK_CREATE", ["C-3"], "dashboard analytics bản khác", "Lan",
            extraction_source="RULE", order_index=3,
        ),
    ]

    summary, results = evaluate_task_linker_shadow(
        events,
        clauses,
        embedding_model=HashingEmbeddingModel(32),
        config=TaskLinkScoringConfig(),
    )

    assert summary.query_count == 1
    assert results[0].status == "DIRECT_LINK"
    assert results[0].task_id == "TASK-000001"
    assert results[0].reason == "EXACT_ALIAS"
