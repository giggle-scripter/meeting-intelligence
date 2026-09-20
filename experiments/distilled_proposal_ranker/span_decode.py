"""Grounded BIO decoding, balanced-quote-safe trimming, and character NMS."""

from __future__ import annotations

from dataclasses import dataclass
import string
from typing import Iterable


TRIM_CHARACTERS = set(string.whitespace + string.punctuation + "，。；：！？…")
QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "«": "»"}


@dataclass(frozen=True)
class DecodedSpan:
    start: int
    end: int
    text: str
    score: float


def _trim(raw_text: str, start: int, end: int) -> tuple[int, int]:
    if end - start >= 2 and raw_text[start] in QUOTE_PAIRS and raw_text[end - 1] == QUOTE_PAIRS[raw_text[start]]:
        return start, end
    while start < end and raw_text[start] in TRIM_CHARACTERS:
        start += 1
    while end > start and raw_text[end - 1] in TRIM_CHARACTERS:
        end -= 1
    return start, end


def character_iou(left: DecodedSpan, right: DecodedSpan) -> float:
    intersection = max(0, min(left.end, right.end) - max(left.start, right.start))
    union = max(left.end, right.end) - min(left.start, right.start)
    return intersection / union if union else 0.0


def nms_spans(spans: Iterable[DecodedSpan], threshold: float = 0.80, limit: int = 3) -> list[DecodedSpan]:
    ordered = sorted(spans, key=lambda item: (-item.score, -(item.end - item.start), item.start, item.end))
    kept: list[DecodedSpan] = []
    for span in ordered:
        if any(character_iou(span, existing) >= threshold for existing in kept):
            continue
        kept.append(span)
        if len(kept) == limit:
            break
    return kept


def decode_bio_spans(
    labels: list[int],
    token_scores: list[float],
    offset_mapping: list[tuple[int, int]],
    *,
    raw_text: str,
    target_start_in_context: int,
    target_end_in_context: int,
    has_action_probability: float,
) -> list[DecodedSpan]:
    if has_action_probability < 0.20:
        return []
    candidates: list[DecodedSpan] = []
    active: list[int] = []

    def emit() -> None:
        nonlocal active
        if not active:
            return
        context_start = offset_mapping[active[0]][0]
        context_end = offset_mapping[active[-1]][1]
        start = context_start - target_start_in_context
        end = context_end - target_start_in_context
        if start >= 0 and end <= len(raw_text) and context_end <= target_end_in_context:
            start, end = _trim(raw_text, start, end)
            if end > start and raw_text[start:end].strip():
                candidates.append(DecodedSpan(start, end, raw_text[start:end], sum(token_scores[index] for index in active) / len(active)))
        active = []

    for index, label in enumerate(labels):
        start, end = offset_mapping[index]
        in_target = start < end and start >= target_start_in_context and end <= target_end_in_context
        if not in_target or label == 0:
            emit()
            continue
        if label == 1:
            emit()
            active = [index]
        elif label == 2:
            if not active:  # illegal leading I is repaired to B
                active = [index]
            else:
                active.append(index)
    emit()
    return nms_spans(candidates)
