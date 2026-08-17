"""Unicode-safe normalization for rules; raw text remains untouched."""

import re
import unicodedata


WEEKDAY_REPLACEMENTS = {
    r"\bthứ\s*2\b": "thứ hai", r"\bthứ\s*3\b": "thứ ba",
    r"\bthứ\s*4\b": "thứ tư", r"\bthứ\s*5\b": "thứ năm",
    r"\bthứ\s*6\b": "thứ sáu", r"\bthứ\s*7\b": "thứ bảy",
    r"\bcn\b": "chủ nhật",
}


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for pattern, replacement in WEEKDAY_REPLACEMENTS.items():
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def normalize_for_match(text: str) -> str:
    value = normalize_text(text).replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^\w\s:/.-]", "", value)
