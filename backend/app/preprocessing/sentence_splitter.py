"""Rule-based sentence splitting for Vietnamese, English and mixed text."""

import re

from ..models import Sentence, SpeakerTurn
from ..utils.ids import make_id
from .unicode_normalizer import normalize_text


BOUNDARY_MARKERS = (
    "khoan", "chốt lại", "sửa lại", "không phải", "đổi thành", "chuyển sang",
    "dời sang", "lùi sang", "tiếp theo", "còn phần", "về deadline", "riêng phần",
    "hold on", "to clarify", "correction", "change it to", "move it to",
)


def _protect(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    patterns = [r"https?://\S+", r"\b\d+\.\d+\b", r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b"]
    protected = text
    for pattern in patterns:
        for match in list(re.finditer(pattern, protected)):
            token = f"__PROTECTED_{len(placeholders)}__"
            placeholders[token] = match.group(0)
            protected = protected.replace(match.group(0), token, 1)
    return protected, placeholders


def _restore(text: str, placeholders: dict[str, str]) -> str:
    for token, value in placeholders.items():
        text = text.replace(token, value)
    return text


def _split_long(value: str, max_tokens: int) -> list[str]:
    words = value.split()
    if len(words) <= max_tokens:
        return [value]
    parts: list[str] = []
    remaining = value
    while len(remaining.split()) > max_tokens:
        words = remaining.split()
        prefix = " ".join(words[:max_tokens])
        split_at = max(prefix.rfind(", "), prefix.rfind(" but "), prefix.rfind(" nhưng "), prefix.rfind(" and "), prefix.rfind(" và "))
        if split_at < len(prefix) // 2:
            split_at = len(prefix)
        parts.append(remaining[:split_at].strip(" ,"))
        remaining = remaining[split_at:].strip(" ,")
    if remaining:
        parts.append(remaining)
    return parts


def split_turn_text(text: str, max_tokens: int = 60) -> list[str]:
    protected, placeholders = _protect(text)
    marker_pattern = "|".join(re.escape(marker) for marker in BOUNDARY_MARKERS)
    protected = re.sub(rf"(?i)(?<!^)(?=\b(?:{marker_pattern})\b)", "\n", protected)
    rough_parts = re.split(r"(?<=[!?;])\s+|(?<=\.)\s+(?=[A-ZÀ-Ỹ])|\n+", protected)
    sentences: list[str] = []
    for part in rough_parts:
        restored = _restore(part, placeholders).strip()
        if restored:
            sentences.extend(_split_long(restored, max_tokens))
    return sentences


def split_sentences(turns: list[SpeakerTurn], max_tokens: int = 60) -> list[Sentence]:
    sentences: list[Sentence] = []
    for turn in turns:
        for text in split_turn_text(turn.text_raw, max_tokens=max_tokens):
            sentences.append(Sentence(make_id("SENT", len(sentences) + 1), turn.turn_id, turn.speaker_id, turn.speaker_name, turn.start_ms, turn.end_ms, text, normalize_text(text), list(turn.source_caption_ids), len(sentences)))
    return sentences
