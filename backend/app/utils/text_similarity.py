"""Language-independent text similarity helpers."""

from difflib import SequenceMatcher
import re


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def normalized_tokens(value: str) -> set[str]:
    return set(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def token_overlap(left: str, right: str) -> float:
    left_tokens = normalized_tokens(left)
    right_tokens = normalized_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
