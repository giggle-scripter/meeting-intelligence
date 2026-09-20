"""Topic segmentation and hard-bounded context retrieval tests."""

from __future__ import annotations

from backend.app.ml.contracts import EmbeddingModelMetadata
from backend.app.ml.embeddings import HashingEmbeddingModel
from backend.app.models import Clause, TaskEvent
from backend.app.retrieval import (
    ContextRetrievalConfig,
    ContextRetriever,
    TaskContextEvidence,
    TopicIndex,
    TaskLinkScoringConfig,
    evaluate_context_retrieval_shadow,
    evaluate_task_linker_shadow,
)
from backend.app.v2.models.context import NoteCue, NoteLineKind


class MappedEmbedding:
    def __init__(self, mapping: dict[str, list[float]] | None = None) -> None:
        self.mapping = mapping or {}
        self.metadata = EmbeddingModelMetadata(
            requested_model="mapped-test",
            model_name="mapped-test",
            model_version="v1",
            backend="test",
            dimension=2,
        )

    def embed(self, texts):
        return [self.mapping.get(text, [1.0, 0.0]) for text in texts]


def _clauses(texts: list[str]) -> list[Clause]:
    return [
        Clause(
            f"C-{index}", f"S-{index}", "SPK", "Lan", None, None,
            text, text.casefold(), order_index=index,
        )
        for index, text in enumerate(texts)
    ]


def test_explicit_discourse_marker_starts_a_new_topic() -> None:
    clauses = _clauses(["API payment ready", "Tiếp theo dashboard analytics", "Chart ready"])
    index = TopicIndex(clauses, MappedEmbedding(), boundary_threshold=0.20)

    assert [topic.clause_ids for topic in index.topics()] == [
        ("C-0",),
        ("C-1", "C-2"),
    ]


def test_one_short_semantic_outlier_does_not_split_topic() -> None:
    clauses = _clauses(["payment backend implementation", "ok", "payment API validation"])
    model = MappedEmbedding({
        "payment backend implementation": [1.0, 0.0],
        "ok": [0.0, 1.0],
        "payment API validation": [1.0, 0.0],
    })

    index = TopicIndex(clauses, model, boundary_threshold=0.80)

    assert len(index.topics()) == 1


def test_smoothed_turn_similarity_starts_semantic_topic() -> None:
    clauses = _clauses([
        "payment backend implementation",
        "payment API validation details",
        "analytics dashboard chart design",
    ])
    for index, clause in enumerate(clauses):
        clause.source_caption_ids = [f"CAP-{index}"]
    model = MappedEmbedding({
        clauses[0].text_raw: [1.0, 0.0],
        clauses[1].text_raw: [0.9, 0.1],
        clauses[2].text_raw: [0.0, 1.0],
    })

    index = TopicIndex(clauses, model, boundary_threshold=0.60)

    assert [topic.clause_ids for topic in index.topics()] == [
        ("C-0", "C-1"),
        ("C-2",),
    ]


def test_semantic_retrieval_ranks_topics_before_clauses() -> None:
    clauses = _clauses([
        "payment backend implementation",
        "payment API validation",
        "moving on analytics dashboard",
        "dashboard frontend chart",
    ])
    model = MappedEmbedding({
        clauses[0].text_raw: [1.0, 0.0],
        clauses[1].text_raw: [0.9, 0.1],
        clauses[2].text_raw: [0.0, 1.0],
        clauses[3].text_raw: [0.1, 0.9],
        "dashboard query": [0.0, 1.0],
    })
    index = TopicIndex(clauses, model, boundary_threshold=0.50)

    matches = index.retrieve("dashboard query", max_topics=1, max_clauses=2)

    assert {match.clause_id for match in matches} == {"C-2", "C-3"}


def test_task_source_precedes_topic_and_history_under_clause_cap() -> None:
    clauses = _clauses([f"clause {index} enough words" for index in range(10)])
    model = MappedEmbedding({
        **{clause.text_raw: [1.0, 0.0] for clause in clauses},
        "mutation query": [1.0, 0.0],
    })
    topic_index = TopicIndex(clauses, model, boundary_threshold=0.0)
    retriever = ContextRetriever(
        clauses,
        topic_index,
        ContextRetrievalConfig(
            max_clauses=5,
            max_characters=12_000,
            local_before=0,
            local_after=0,
            max_topic_clauses=5,
        ),
    )

    selection = retriever.retrieve(
        ("C-5",),
        "mutation query",
        task_evidence=[
            TaskContextEvidence(
                task_id="TASK-1",
                source_clause_ids=("C-9",),
                mutation_history_event_ids=("E-OLD",),
                mutation_history_clause_ids=("C-8",),
            )
        ],
    )

    assert selection.bundle.local_clause_ids == ("C-5",)
    assert selection.bundle.topic_clause_ids[0] == "C-9"
    assert "C-8" not in selection.bundle.topic_clause_ids
    assert selection.bundle.total_clause_count == 5
    assert selection.clause_cap_hit is True


def test_local_context_defaults_to_three_before_and_five_after() -> None:
    clauses = _clauses([f"local clause {index}" for index in range(12)])
    topic_index = TopicIndex(clauses, MappedEmbedding(), boundary_threshold=0.0)
    retriever = ContextRetriever(
        clauses,
        topic_index,
        ContextRetrievalConfig(max_topic_clauses=0),
    )

    selection = retriever.retrieve(("C-5",), "query")

    assert selection.bundle.local_clause_ids == tuple(
        f"C-{index}" for index in range(2, 11)
    )


def test_character_task_and_note_caps_are_enforced() -> None:
    clauses = _clauses(["a" * 10, "b" * 10, "c" * 10])
    topic_index = TopicIndex(clauses, MappedEmbedding(), boundary_threshold=0.0)
    retriever = ContextRetriever(
        clauses,
        topic_index,
        ContextRetrievalConfig(
            max_characters=20,
            max_tasks=1,
            local_before=0,
            local_after=0,
            max_topic_clauses=3,
        ),
    )
    cue = NoteCue(
        clause_id="C-0",
        note_line_id="NOTE-1",
        kind=NoteLineKind.ACTION_HINT,
        status="GROUNDED",
        grounding_score=0.9,
        score_margin=0.2,
    )

    selection = retriever.retrieve(
        ("C-0",),
        "query",
        task_evidence=[
            TaskContextEvidence(task_id="TASK-1", source_clause_ids=("C-1",)),
            TaskContextEvidence(task_id="TASK-2", source_clause_ids=("C-2",)),
        ],
        note_cues_by_clause={"C-0": (cue,)},
    )

    assert selection.total_character_count == 20
    assert selection.bundle.related_task_ids == ("TASK-1",)
    assert selection.bundle.note_cue_ids == ("NOTE-1",)
    assert selection.character_cap_hit is True


def test_shadow_history_is_chronological_and_never_contains_current_event() -> None:
    clauses = _clauses([
        "Tạo dashboard analytics",
        "Dời dashboard analytics sang thứ Hai",
        "Đổi owner dashboard analytics cho Minh",
    ])
    clauses_by_id = {clause.clause_id: clause for clause in clauses}
    events = [
        TaskEvent(
            "E-1", "TASK_CREATE", ["C-0"], "dashboard analytics", "Lan",
            extraction_source="RULE", order_index=0,
        ),
        TaskEvent(
            "E-2", "DEADLINE_REPLACE", ["C-1"],
            related_task_hint="dashboard analytics", extraction_source="RULE",
            order_index=1,
        ),
        TaskEvent(
            "E-3", "OWNER_REASSIGN", ["C-2"],
            related_task_hint="dashboard analytics", assignee="Minh",
            extraction_source="RULE", order_index=2,
        ),
    ]
    embedding = HashingEmbeddingModel(32)
    _, linker_records = evaluate_task_linker_shadow(
        events,
        clauses_by_id,
        embedding_model=embedding,
        config=TaskLinkScoringConfig(),
    )
    topic_index = TopicIndex(clauses, embedding, boundary_threshold=0.0)
    retriever = ContextRetriever(
        clauses,
        topic_index,
        ContextRetrievalConfig(
            local_before=0,
            local_after=0,
            max_topic_clauses=0,
        ),
    )

    summary, records = evaluate_context_retrieval_shadow(
        events,
        clauses_by_id,
        linker_records,
        retriever=retriever,
    )

    assert summary.error_count == 0
    assert records[0].bundle.task_history_event_ids == ()
    assert records[1].bundle.task_history_event_ids == ("E-2",)
    assert "E-3" not in records[1].bundle.task_history_event_ids
    assert records[1].tier_clause_counts["history"] == 1
