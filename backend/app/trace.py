"""Opt-in local diagnostic traces; never include provider credentials."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Unsupported trace value: {type(value).__name__}")


def write_pipeline_trace(directory: str, meeting_id: str, pipeline_version: str, payload: dict[str, Any]) -> Path:
    """Write one deterministic JSON artifact for local failure diagnosis."""

    safe_id = "".join(character if character.isalnum() or character in "-_" else "_" for character in meeting_id)
    content_hash = sha256(meeting_id.encode("utf-8")).hexdigest()[:10]
    target = Path(directory) / f"{safe_id or 'meeting'}-{pipeline_version}-{content_hash}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
    return target
