"""Read committed trace structures without mutating or normalizing source text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        items = value.get("records", [])
        return list(items) if isinstance(items, list) else []
    return list(value) if isinstance(value, list) else []


def read_trace(path: Path) -> dict[str, Any]:
    trace = json.loads(path.read_text(encoding="utf-8-sig"))
    clause_ids = [item.get("clause_id") for item in trace.get("clauses", [])]
    if not trace.get("meeting_id") or len(clause_ids) != len(set(clause_ids)):
        raise ValueError("invalid trace identity")
    return trace


def load_trace_set(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*-v1-*.json")):
        trace = read_trace(path)
        meeting_id = trace["meeting_id"]
        if meeting_id in result:
            raise ValueError(f"duplicate trace meeting_id: {meeting_id}")
        result[meeting_id] = trace
    return result


def clause_map(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["clause_id"]: item for item in trace.get("clauses", [])}
