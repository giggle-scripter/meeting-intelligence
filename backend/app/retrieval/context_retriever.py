"""Hard-bounded context assembly with task memory before broad semantics."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.models import Clause
from backend.app.v2.models.context import NoteCue

from .topic_index import TopicIndex


class ContextBundle(BaseModel):
    """Stable context contract consumed by later mutation checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_clause_ids: tuple[str, ...]
    local_clause_ids: tuple[str, ...]
    topic_clause_ids: tuple[str, ...]
    related_task_ids: tuple[str, ...]
    task_history_event_ids: tuple[str, ...]
    note_cue_ids: tuple[str, ...]
    total_clause_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_clause_count(self) -> "ContextBundle":
        selected = set(self.local_clause_ids) | set(self.topic_clause_ids)
        if self.total_clause_count != len(selected):
            raise ValueError("total_clause_count must match unique selected clauses")
        if not set(self.primary_clause_ids).issubset(selected):
            raise ValueError("primary clauses must be present in the selected context")
        return self


class ContextRetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_clauses: int = Field(default=30, gt=0, le=30)
    max_characters: int = Field(default=12_000, gt=0, le=12_000)
    max_tasks: int = Field(default=5, gt=0, le=5)
    local_before: int = Field(default=3, ge=0)
    local_after: int = Field(default=5, ge=0)
    max_topic_clauses: int = Field(default=12, ge=0, le=30)
    max_topics: int = Field(default=3, gt=0, le=12)
    max_history_events_per_task: int = Field(default=3, ge=0, le=10)
    version: str = Field(default="context-retriever-v1", min_length=1)


class TaskContextEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    source_clause_ids: tuple[str, ...] = ()
    mutation_history_event_ids: tuple[str, ...] = ()
    mutation_history_clause_ids: tuple[str, ...] = ()


class ContextSelection(BaseModel):
    """Bundle plus bounded telemetry; clause contents never enter diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle: ContextBundle
    total_character_count: int = Field(ge=0)
    tier_clause_counts: dict[str, int]
    clause_cap_hit: bool = False
    character_cap_hit: bool = False


class ContextRetriever:
    VERSION = "bounded-context-retriever-v1"

    def __init__(
        self,
        clauses: list[Clause],
        topic_index: TopicIndex,
        config: ContextRetrievalConfig | None = None,
    ) -> None:
        self.clauses = sorted(
            clauses,
            key=lambda item: (item.order_index, item.clause_id),
        )
        self.clauses_by_id = {item.clause_id: item for item in self.clauses}
        self.position_by_id = {
            item.clause_id: index
            for index, item in enumerate(self.clauses)
        }
        self.topic_index = topic_index
        self.config = config or ContextRetrievalConfig()

    def retrieve(
        self,
        primary_clause_ids: tuple[str, ...] | list[str],
        query_text: str,
        *,
        task_evidence: tuple[TaskContextEvidence, ...] | list[TaskContextEvidence] = (),
        note_cues_by_clause: dict[str, tuple[NoteCue, ...]] | None = None,
    ) -> ContextSelection:
        primary = tuple(dict.fromkeys(primary_clause_ids))
        if not primary or any(
            clause_id not in self.clauses_by_id for clause_id in primary
        ):
            raise ValueError("all primary_clause_ids must identify transcript clauses")

        selected: list[str] = []
        selected_set: set[str] = set()
        local: list[str] = []
        support: list[str] = []
        total_characters = 0
        clause_cap_hit = False
        character_cap_hit = False
        tier_counts = {"local": 0, "task_source": 0, "topic": 0, "history": 0}

        def add(clause_id: str, *, tier: str, local_tier: bool = False) -> bool:
            nonlocal total_characters, clause_cap_hit, character_cap_hit
            if clause_id in selected_set or clause_id not in self.clauses_by_id:
                return clause_id in selected_set
            text_length = len(self.clauses_by_id[clause_id].text_raw)
            if len(selected) >= self.config.max_clauses:
                clause_cap_hit = True
                return False
            if total_characters + text_length > self.config.max_characters:
                character_cap_hit = True
                return False
            selected.append(clause_id)
            selected_set.add(clause_id)
            total_characters += text_length
            (local if local_tier else support).append(clause_id)
            tier_counts[tier] += 1
            return True

        for clause_id in primary:
            if not add(clause_id, tier="local", local_tier=True):
                raise ValueError("primary clauses exceed the configured context caps")
        local_candidates: set[str] = set(primary)
        for clause_id in primary:
            position = self.position_by_id[clause_id]
            start = max(0, position - self.config.local_before)
            stop = min(len(self.clauses), position + self.config.local_after + 1)
            local_candidates.update(item.clause_id for item in self.clauses[start:stop])
        for clause in self.clauses:
            if clause.clause_id in local_candidates:
                add(clause.clause_id, tier="local", local_tier=True)

        bounded_tasks = list(task_evidence)[: self.config.max_tasks]
        for task in bounded_tasks:
            for clause_id in task.source_clause_ids:
                add(clause_id, tier="task_source")

        for match in self.topic_index.retrieve(
            query_text,
            max_topics=self.config.max_topics,
            max_clauses=self.config.max_topic_clauses,
            exclude_clause_ids=selected_set,
        ):
            add(match.clause_id, tier="topic")

        included_history_events: list[str] = []
        for task in bounded_tasks:
            event_clause_pairs = list(
                zip(
                    task.mutation_history_event_ids,
                    task.mutation_history_clause_ids,
                    strict=False,
                )
            )[-self.config.max_history_events_per_task :]
            for event_id, clause_id in event_clause_pairs:
                if add(clause_id, tier="history"):
                    included_history_events.append(event_id)

        cues = note_cues_by_clause or {}
        note_cue_ids = tuple(
            dict.fromkeys(
                cue.note_line_id
                for clause_id in selected
                for cue in cues.get(clause_id, ())
                if cue.ai_usable
            )
        )
        bundle = ContextBundle(
            primary_clause_ids=primary,
            local_clause_ids=tuple(
                sorted(
                    local,
                    key=lambda clause_id: (
                        self.clauses_by_id[clause_id].order_index,
                        clause_id,
                    ),
                )
            ),
            # This is the bounded non-local support payload. Task/history provenance
            # remains explicit in related_task_ids and task_history_event_ids.
            topic_clause_ids=tuple(support),
            related_task_ids=tuple(task.task_id for task in bounded_tasks),
            task_history_event_ids=tuple(dict.fromkeys(included_history_events)),
            note_cue_ids=note_cue_ids,
            total_clause_count=len(selected),
        )
        return ContextSelection(
            bundle=bundle,
            total_character_count=total_characters,
            tier_clause_counts=tier_counts,
            clause_cap_hit=clause_cap_hit,
            character_cap_hit=character_cap_hit,
        )
