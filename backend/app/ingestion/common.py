"""Shared subtitle parsing helpers."""

import html
import re


TAG_RE = re.compile(r"<[^>]+>")
VOICE_RE = re.compile(r"^<v\s+([^>]+)>(.*)$", re.IGNORECASE)
SPEAKER_RE = re.compile(r"^([^:]{1,80}):\s*(.+)$")


def timestamp_to_ms(value: str) -> int:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Invalid timestamp: {value}")
    seconds_parts = seconds.split(".", 1)
    whole_seconds = int(seconds_parts[0])
    milliseconds = int((seconds_parts[1] if len(seconds_parts) == 2 else "0").ljust(3, "0")[:3])
    return ((int(hours) * 60 + int(minutes)) * 60 + whole_seconds) * 1000 + milliseconds


def clean_caption_text(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ")
    value = TAG_RE.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def split_speaker(value: str) -> tuple[str, str]:
    voice = VOICE_RE.match(value.strip())
    if voice:
        return clean_caption_text(voice.group(1)), clean_caption_text(voice.group(2))
    cleaned = clean_caption_text(value)
    match = SPEAKER_RE.match(cleaned)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "Unknown", cleaned
