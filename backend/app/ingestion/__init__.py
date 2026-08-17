"""Transcript parser selection."""

from typing import Protocol

from ..models import Caption
from .plain_text_parser import PlainTextParser
from .srt_parser import SrtParser
from .vtt_parser import VttParser
from .meeting_package import (
    MeetingPackageMetadata,
    build_meeting_package,
    parse_meeting_package,
    split_meeting_package,
)


class TranscriptParser(Protocol):
    def parse(self, transcript: str) -> list[Caption]: ...


def get_parser(transcript: str, file_name: str = "") -> TranscriptParser:
    stripped = transcript.lstrip("\ufeff \t\r\n")
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if stripped.startswith("WEBVTT") or suffix == "vtt":
        return VttParser()
    if suffix == "srt" or "-->" in transcript:
        return SrtParser()
    return PlainTextParser()


def parse_transcript(transcript: str, file_name: str = "") -> list[Caption]:
    return get_parser(transcript, file_name).parse(transcript)


__all__ = [
    "MeetingPackageMetadata",
    "build_meeting_package",
    "get_parser",
    "parse_transcript",
    "parse_meeting_package",
    "split_meeting_package",
]
