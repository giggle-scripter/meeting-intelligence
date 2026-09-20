"""Bounded chronological raw-clause contexts with reversible target offsets."""

from __future__ import annotations

from typing import Any

from .contracts import ContextClause


def build_context(
    clauses: list[dict[str, Any]],
    target_clause_id: str,
    *,
    before: int = 3,
    after: int = 5,
    max_clauses: int = 30,
    max_characters: int = 12000,
) -> tuple[str, list[ContextClause], int, int]:
    index_by_id = {item["clause_id"]: index for index, item in enumerate(clauses)}
    if target_clause_id not in index_by_id:
        raise ValueError("unknown target clause")
    target_index = index_by_id[target_clause_id]
    selected = list(range(max(0, target_index - before), min(len(clauses), target_index + after + 1)))
    if len(selected) > max_clauses:
        selected = sorted(selected, key=lambda index: (abs(index - target_index), index))[:max_clauses]
        selected.sort()
    while True:
        rendered = []
        records: list[ContextClause] = []
        cursor = 0
        for index in selected:
            clause = clauses[index]
            prefix = f"[{clause['clause_id']}] {clause.get('speaker_name') or clause.get('speaker_id') or 'UNKNOWN'} | "
            line = prefix + clause["text_raw"]
            rendered.append(line)
            records.append(
                ContextClause(
                    clause_id=clause["clause_id"],
                    speaker=clause.get("speaker_name") or clause.get("speaker_id") or "UNKNOWN",
                    text_raw=clause["text_raw"],
                    order_index=int(clause.get("order_index", index)),
                    context_start=cursor,
                    text_start=cursor + len(prefix),
                    text_end=cursor + len(line),
                )
            )
            cursor += len(line) + 1
        context = "\n".join(rendered)
        if len(context) <= max_characters or len(selected) == 1:
            break
        removable = [index for index in selected if index != target_index]
        if not removable:
            break
        remove_index = max(removable, key=lambda index: (abs(index - target_index), index > target_index, index))
        selected.remove(remove_index)
    if len(context) > max_characters:
        raise ValueError("target clause exceeds context character cap")
    target = next(item for item in records if item.clause_id == target_clause_id)
    return context, records, target.text_start, target.text_end


def raw_to_context_offset(context_clause: ContextClause, raw_offset: int) -> int:
    if raw_offset < 0 or raw_offset > len(context_clause.text_raw):
        raise ValueError("raw offset outside clause")
    return context_clause.text_start + raw_offset


def context_to_raw_offset(context_clause: ContextClause, context_offset: int) -> int:
    if context_offset < context_clause.text_start or context_offset > context_clause.text_end:
        raise ValueError("context offset outside clause")
    return context_offset - context_clause.text_start
