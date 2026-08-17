"""Stable content hashes."""

from hashlib import sha256


def stable_hash(value: str, length: int = 16) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:length]
