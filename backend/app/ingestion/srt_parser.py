"""SubRip parser."""

from .vtt_parser import VttParser


class SrtParser(VttParser):
    """SRT and VTT cue bodies share the same parser after timestamp normalization."""
