"""Build bounded speaker turns from captions."""

from ..models import Caption, SpeakerTurn
from ..utils.ids import make_id
from .speaker_normalizer import canonical_speaker


def _gap(end_ms: int | None, start_ms: int | None) -> int:
    if end_ms is None or start_ms is None:
        return 0
    return start_ms - end_ms


def build_turns(captions: list[Caption], max_gap_ms: int = 2000, max_characters: int = 1000) -> list[SpeakerTurn]:
    turns: list[SpeakerTurn] = []
    for caption in captions:
        speaker_id, speaker_name = canonical_speaker(caption.speaker_raw)
        previous = turns[-1] if turns else None
        can_merge = bool(previous and previous.speaker_id == speaker_id and _gap(previous.end_ms, caption.start_ms) <= max_gap_ms and len(previous.text_raw) + len(caption.text_raw) + 1 <= max_characters)
        if can_merge:
            previous.text_raw = f"{previous.text_raw} {caption.text_raw}".strip()
            previous.end_ms = caption.end_ms if caption.end_ms is not None else previous.end_ms
            previous.source_caption_ids.extend(cid for cid in caption.source_caption_ids if cid not in previous.source_caption_ids)
        else:
            turns.append(SpeakerTurn(make_id("TURN", len(turns) + 1), speaker_id, speaker_name, caption.start_ms, caption.end_ms, caption.text_raw, list(caption.source_caption_ids)))
    return turns
