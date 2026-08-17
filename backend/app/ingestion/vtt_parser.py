"""WebVTT parser."""

import re

from ..models import Caption
from ..utils.ids import make_id
from .common import split_speaker, timestamp_to_ms


TIMING_RE = re.compile(r"^((?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})")


class VttParser:
    def parse(self, transcript: str) -> list[Caption]:
        lines = transcript.replace("\r\n", "\n").split("\n")
        captions: list[Caption] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip().lstrip("\ufeff")
            match = TIMING_RE.match(line)
            if not match:
                index += 1
                continue
            start_ms = timestamp_to_ms(match.group(1))
            end_ms = timestamp_to_ms(match.group(2))
            index += 1
            text_lines: list[str] = []
            while index < len(lines) and lines[index].strip():
                text_lines.append(lines[index].strip())
                index += 1
            speaker, text = split_speaker(" ".join(text_lines))
            if text:
                captions.append(Caption(make_id("CAP", len(captions) + 1), speaker, start_ms, end_ms, text))
        return captions
