"""Stable IDs used across pipeline stages."""


def make_id(prefix: str, sequence: int) -> str:
    return f"{prefix}-{sequence:06d}"
