"""Task representations and a meeting-local embedding cache."""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.ml.contracts import EmbeddingModel, EmbeddingVector
from backend.app.preprocessing.unicode_normalizer import normalize_for_match


_ENTITY_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_ENTITY_STOPWORDS = {
    "a", "an", "and", "cho", "cua", "for", "giu", "la", "lại", "of", "phan",
    "the", "thi", "to", "va", "và", "voi", "với",
}


def extract_identity_entities(values: Iterable[str]) -> tuple[str, ...]:
    """Extract stable content tokens without making them identity authority."""

    tokens: set[str] = set()
    for value in values:
        for token in _ENTITY_TOKEN_RE.findall(normalize_for_match(value)):
            if len(token) >= 3 and token not in _ENTITY_STOPWORDS:
                tokens.add(token)
    return tuple(sorted(tokens))


class TaskRepresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    task_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    created_order_index: int = 0
    last_order_index: int = 0

    @model_validator(mode="after")
    def aliases_include_action(self) -> "TaskRepresentation":
        normalized = {normalize_for_match(item) for item in self.aliases}
        if normalize_for_match(self.action) not in normalized:
            raise ValueError("aliases must include the canonical action")
        return self

    def embedding_text(self) -> str:
        return "\n".join(
            (
                f"ACTION: {self.action}",
                f"ALIASES: {' | '.join(self.aliases)}",
                f"OWNERS: {' | '.join(self.owners)}",
                f"TOPIC: {' | '.join(self.topic_ids)}",
                f"ENTITIES: {' | '.join(self.entities)}",
            )
        )


class MutationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    explicit_task_id: str = ""
    action_ref: str = ""
    mutation_text: str = Field(min_length=1)
    speaker: str = ""
    owner_refs: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()
    order_index: int = 0

    def embedding_text(self) -> str:
        return "\n".join(
            (
                f"ACTION REF: {self.action_ref}",
                f"MUTATION: {self.mutation_text}",
                f"SPEAKER: {self.speaker}",
                f"TOPIC: {' | '.join(self.topic_ids)}",
            )
        )


class TaskIndex:
    """Keep embeddings outside ledger tasks and update only changed entries."""

    VERSION = "task-embedding-index-v1"

    def __init__(self, embedding_model: EmbeddingModel) -> None:
        self.embedding_model = embedding_model
        self._entries: dict[str, TaskRepresentation] = {}
        self._vectors: dict[str, EmbeddingVector] = {}
        self._fingerprints: dict[str, str] = {}

    @property
    def embedding_model_version(self) -> str:
        metadata = self.embedding_model.metadata
        return f"{metadata.model_name}:{metadata.model_version}"

    def sync(self, entries: Iterable[TaskRepresentation]) -> None:
        incoming = {entry.task_id: entry for entry in entries}
        removed = set(self._entries) - set(incoming)
        for task_id in removed:
            self._entries.pop(task_id, None)
            self._vectors.pop(task_id, None)
            self._fingerprints.pop(task_id, None)

        changed: list[TaskRepresentation] = []
        for task_id in sorted(incoming):
            entry = incoming[task_id]
            fingerprint = entry.embedding_text()
            if self._fingerprints.get(task_id) != fingerprint:
                changed.append(entry)
        if changed:
            vectors = self.embedding_model.embed(
                [entry.embedding_text() for entry in changed]
            )
            for entry, vector in zip(changed, vectors, strict=True):
                self._vectors[entry.task_id] = vector
                self._fingerprints[entry.task_id] = entry.embedding_text()
        self._entries = incoming

    def entries(self) -> tuple[TaskRepresentation, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def vector(self, task_id: str) -> EmbeddingVector:
        return self._vectors[task_id]

    def embed_query(self, query: MutationQuery) -> EmbeddingVector:
        return self.embedding_model.embed([query.embedding_text()])[0]
