"""Remove adjacent live-caption updates without deleting real repetition."""

from ..models import Caption
from ..utils.text_similarity import similarity
from .unicode_normalizer import normalize_for_match


def is_caption_update(previous: Caption, current: Caption, max_gap_ms: int = 1500) -> bool:
    if normalize_for_match(previous.speaker_raw) != normalize_for_match(current.speaker_raw):
        return False
    if previous.end_ms is not None and current.start_ms is not None:
        if current.start_ms - previous.end_ms > max_gap_ms:
            return False
    left = normalize_for_match(previous.text_raw)
    right = normalize_for_match(current.text_raw)
    if not left or not right:
        return False
    return left in right or right in left or similarity(left, right) >= 0.85


def deduplicate_caption_updates(captions: list[Caption]) -> list[Caption]:
    deduplicated: list[Caption] = []
    for caption in captions:
        if deduplicated and is_caption_update(deduplicated[-1], caption):
            previous = deduplicated[-1]
            text = caption.text_raw if len(caption.text_raw) >= len(previous.text_raw) else previous.text_raw
            previous.text_raw = text
            previous.start_ms = min(v for v in (previous.start_ms, caption.start_ms) if v is not None) if any(v is not None for v in (previous.start_ms, caption.start_ms)) else None
            previous.end_ms = max(v for v in (previous.end_ms, caption.end_ms) if v is not None) if any(v is not None for v in (previous.end_ms, caption.end_ms)) else None
            previous.source_caption_ids.extend(cid for cid in caption.source_caption_ids if cid not in previous.source_caption_ids)
        else:
            deduplicated.append(caption)
    return deduplicated
