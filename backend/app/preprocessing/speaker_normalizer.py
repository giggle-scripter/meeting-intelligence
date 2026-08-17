"""Canonicalize speaker labels while preserving human-readable names."""

import re

from ..models import Caption
from ..utils.hashing import stable_hash
from .unicode_normalizer import normalize_for_match


PREFIX_RE = re.compile(r"^(mr|ms|mrs|anh|chị|chi|em|bạn|ban|speaker)\s+", re.IGNORECASE)


def canonical_speaker(value: str) -> tuple[str, str]:
    name = re.sub(r"\s+", " ", value).strip() or "Unknown"
    display_name = PREFIX_RE.sub("", name).strip() or name
    key = normalize_for_match(display_name).strip() or "unknown"
    return f"SPK-{stable_hash(key, 8)}", display_name


def normalize_speakers(captions: list[Caption], aliases: dict[str, str] | None = None) -> list[Caption]:
    aliases = {normalize_for_match(k): v for k, v in (aliases or {}).items()}
    canonical_names: dict[str, str] = {}
    for caption in captions:
        alias_key = normalize_for_match(caption.speaker_raw)
        if alias_key in aliases:
            caption.speaker_raw = aliases[alias_key]
        speaker_id, name = canonical_speaker(caption.speaker_raw)
        canonical_names.setdefault(speaker_id, name)
        caption.speaker_raw = canonical_names[speaker_id]
    return captions
