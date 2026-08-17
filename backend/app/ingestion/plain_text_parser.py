"""Plain speaker-prefixed transcript parser."""

import re

from ..models import Caption
from ..utils.ids import make_id
from .common import split_speaker, timestamp_to_ms


LINE_RE = re.compile(
    r"^(?:T\d+\s+)?\[(\d{2}:\d{2}(?::\d{2})?)\]\s*(.*)$",
    re.IGNORECASE,
)
HEADER_RE = re.compile(
    r"^(?:MEETING_ID|PART|CASE_LABELS|MEETING_DATE|PRIMARY_FEATURE|TITLE):",
    re.IGNORECASE,
)
PART_BOUNDARY_RE = re.compile(r"^---\s*(?:END|START)\s+OF\s+PART\b", re.IGNORECASE)


class PlainTextParser:
    def parse(self, transcript: str) -> list[Caption]:
        captions: list[Caption] = []
        for raw_line in transcript.replace("\r\n", "\n").split("\n"):
            line = raw_line.strip()
            if not line or HEADER_RE.match(line) or PART_BOUNDARY_RE.match(line):
                continue
            start_ms = None
            timestamp_match = LINE_RE.match(line)
            if timestamp_match:
                stamp = timestamp_match.group(1)
                if stamp.count(":") == 1:
                    stamp = f"00:{stamp}"
                start_ms = timestamp_to_ms(stamp)
                line = timestamp_match.group(2)
            speaker, text = split_speaker(line)
            if speaker == "Unknown" and captions:
                speaker = captions[-1].speaker_raw
            if text:
                captions.append(
                    Caption(
                        make_id("CAP", len(captions) + 1),
                        speaker,
                        start_ms,
                        None,
                        text,
                    )
                )
        return captions
