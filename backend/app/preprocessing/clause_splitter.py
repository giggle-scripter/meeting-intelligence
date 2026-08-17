"""Split independent subject-action pairs without splitting compound deliverables."""

import re

from ..models import Clause, Sentence
from ..utils.ids import make_id
from .unicode_normalizer import normalize_text


INDEPENDENT_CONNECTOR_RE = re.compile(r"\s*(?:,|;)?\s+((?:còn|trong khi|riêng)\s+[^,;]+)", re.IGNORECASE)
NAMED_ACTION_RE = re.compile(r"\b(?:và|nhưng|and|but)\s+([A-ZÀ-Ỹ][\wÀ-ỹ'-]+)\s+(?=(?:sẽ|phụ trách|kiểm tra|chuẩn bị|gửi|cập nhật|will|owns?|checks?|prepares?|sends?|updates?)\b)")


def split_sentence_clauses(text: str) -> list[str]:
    boundaries: list[int] = []
    for match in re.finditer(r"\b(?:còn|trong khi|riêng)\s+(?=[A-ZÀ-Ỹ\wÀ-ỹ'-]+\s+)", text, flags=re.IGNORECASE):
        boundaries.append(match.start())
    for match in NAMED_ACTION_RE.finditer(text):
        boundaries.append(match.start())
    if not boundaries:
        return [text.strip()]
    parts: list[str] = []
    start = 0
    for boundary in sorted(set(boundaries)):
        part = text[start:boundary].strip(" ,;")
        if part:
            parts.append(part)
        start = boundary
    tail = text[start:].strip(" ,;")
    if tail:
        parts.append(tail)
    return parts


def split_clauses(sentences: list[Sentence]) -> list[Clause]:
    clauses: list[Clause] = []
    for sentence in sentences:
        for text in split_sentence_clauses(sentence.text_raw):
            clauses.append(Clause(make_id("CLAUSE", len(clauses) + 1), sentence.sentence_id, sentence.speaker_id, sentence.speaker_name, sentence.start_ms, sentence.end_ms, text, normalize_text(text), list(sentence.source_caption_ids), len(clauses)))
    return clauses
