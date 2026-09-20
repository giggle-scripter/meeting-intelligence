"""Deterministic clause features shared by dataset and future inference code."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import TypeAlias

from backend.app.models import Clause, ClauseAnnotation


FeatureValue: TypeAlias = bool | int | float

ACTION_FEATURE_NAMES = (
    "has_commitment",
    "has_assignment",
    "has_correction",
    "has_cancel",
    "has_rejection",
    "has_date",
    "has_owner_name",
    "is_question",
    "is_hypothetical",
    "is_suggestion",
    "is_past_completed",
    "is_progress_only",
    "speaker_changed",
    "first_person",
    "second_person",
    "note_supported",
    "rule_score",
)

_FIRST_PERSON_RE = re.compile(
    r"\b(?:toi|mình|minh|em|anh|chi|chung toi|chung mình|chung minh|i|we)\b",
    re.IGNORECASE,
)
_SECOND_PERSON_RE = re.compile(
    r"\b(?:ban|cac ban|em|anh|chi|you|your)\b",
    re.IGNORECASE,
)


def normalize_feature_text(value: str) -> str:
    """Normalize text for stable, accent-insensitive lexical features."""

    text = value.casefold().replace("đ", "d")
    text = "".join(
        character
        for character in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"\w+", text))


def build_action_features(
    clause: Clause,
    annotation: ClauseAnnotation,
    *,
    previous_clause: Clause | None = None,
    speaker_names: Iterable[str] = (),
    note_supported: bool = False,
) -> dict[str, FeatureValue]:
    """Build the version-1 handcrafted action feature set."""

    flags = annotation.flags
    normalized_text = normalize_feature_text(clause.text_raw)
    normalized_speakers = {
        normalize_feature_text(name)
        for name in speaker_names
        if normalize_feature_text(name)
    }
    has_owner_name = any(
        re.search(rf"\b{re.escape(name)}\b", normalized_text)
        for name in normalized_speakers
    )
    return {
        "has_commitment": "FIRST_PERSON_COMMITMENT" in flags,
        "has_assignment": "DIRECT_ASSIGNMENT" in flags,
        "has_correction": "CORRECTION" in flags,
        "has_cancel": "CANCELLATION" in flags,
        "has_rejection": "REJECTION" in flags,
        "has_date": "DATE_MENTION" in flags,
        "has_owner_name": has_owner_name,
        "is_question": "ROOT_QUESTION" in flags or clause.text_raw.rstrip().endswith("?"),
        "is_hypothetical": "HYPOTHETICAL" in flags,
        "is_suggestion": "SUGGESTION_ONLY" in flags,
        "is_past_completed": "PAST_COMPLETED" in flags,
        "is_progress_only": "PROGRESS_UPDATE" in flags,
        "speaker_changed": bool(
            previous_clause
            and previous_clause.speaker_id
            and previous_clause.speaker_id != clause.speaker_id
        ),
        "first_person": bool(_FIRST_PERSON_RE.search(normalized_text)),
        "second_person": bool(_SECOND_PERSON_RE.search(normalized_text)),
        "note_supported": note_supported,
        "rule_score": annotation.score,
    }


def action_feature_vector(features: dict[str, FeatureValue]) -> list[float]:
    """Convert named features to the stable training/inference column order."""

    missing = [name for name in ACTION_FEATURE_NAMES if name not in features]
    if missing:
        raise ValueError(f"missing action features: {', '.join(missing)}")
    return [float(features[name]) for name in ACTION_FEATURE_NAMES]


def build_action_semantic_text(
    clause: Clause,
    *,
    previous_clauses: Iterable[str] = (),
    next_clauses: Iterable[str] = (),
) -> str:
    """Build the versioned local context representation used by the model."""

    previous = "\n".join(previous_clauses)
    following = "\n".join(next_clauses)
    focus = (
        f"{clause.speaker_name}: {clause.text_raw}"
        if clause.speaker_name
        else clause.text_raw
    )
    return f"[PREV]\n{previous}\n\n[FOCUS]\n{focus}\n\n[NEXT]\n{following}"
