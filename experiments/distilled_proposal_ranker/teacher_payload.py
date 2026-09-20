"""Bounded teacher topic bundles built only from source trace features."""

from __future__ import annotations

from typing import Any

from .candidate_pool import build_candidate_pool
from .contracts import TeacherClause, TeacherPayload, TeacherProposal
from .hashing import canonical_json_hash, sha256_bytes


def _existing_task_refs(trace: dict[str, Any]) -> list[str]:
    refs: set[str] = set()
    values = trace.get("task_states", [])
    if isinstance(values, dict):
        values = values.values()
    for item in values or []:
        if isinstance(item, dict):
            for key in ("task_id", "task_key", "event_id"):
                if item.get(key):
                    refs.add(str(item[key]))
    return sorted(refs)


def build_teacher_payloads(case_id: str, trace: dict[str, Any], outer_fold: int) -> list[TeacherPayload]:
    clauses = trace.get("clauses", [])
    proposals = build_candidate_pool(case_id, trace, [], max_proposals=60, evaluation=True)
    chunks: list[list[dict[str, Any]]] = []
    cursor = 0
    while cursor < len(clauses):
        chunk: list[dict[str, Any]] = []
        characters = 0
        index = cursor
        while index < len(clauses) and len(chunk) < 30:
            clause = clauses[index]
            added = len(clause["text_raw"])
            if chunk and characters + added > 12000:
                break
            chunk.append(clause)
            characters += added
            index += 1
        if not chunk:
            raise ValueError("single teacher clause exceeds payload cap")
        chunks.append(chunk)
        if index >= len(clauses):
            break
        cursor = max(cursor + 1, index - 3)
    payloads: list[TeacherPayload] = []
    assigned: set[str] = set()
    date_values = trace.get("date_mentions", {})
    allowed_dates = sorted(date_values if isinstance(date_values, dict) else [item["date_mention_id"] for item in date_values])
    for shard_index, chunk in enumerate(chunks):
        clause_ids = {item["clause_id"] for item in chunk}
        shard_proposals = [item for item in proposals if item.action_span.clause_id in clause_ids and item.proposal_id not in assigned]
        assigned.update(item.proposal_id for item in shard_proposals)
        teacher_clauses = [
            TeacherClause(
                clause_id=item["clause_id"],
                speaker=item.get("speaker_name") or item.get("speaker_id") or "UNKNOWN",
                order_index=int(item.get("order_index", 0)),
                text_raw=item["text_raw"],
            )
            for item in chunk
        ]
        teacher_proposals = [
            TeacherProposal(
                proposal_id=item.proposal_id,
                action_span=item.action_span,
                kind=item.kind,
                authority_refs=[ref for ref in item.authority_refs if ref in clause_ids],
                acceptance_refs=[ref for ref in item.acceptance_refs if ref in clause_ids],
                owner_refs=[ref for ref in item.owner_refs if ref in clause_ids],
                deadline_refs=item.deadline_refs,
                negative_refs=[ref for ref in item.negative_refs if ref in clause_ids],
                relation_types=sorted({relation.relation_type for relation in item.relations if relation.support_clause_id in clause_ids}),
            )
            for item in shard_proposals
        ]
        content = {
            "case_id": case_id,
            "outer_fold": outer_fold,
            "shard_index": shard_index,
            "clauses": [item.model_dump(mode="json") for item in teacher_clauses],
            "proposals": [item.model_dump(mode="json") for item in teacher_proposals],
            "allowed_date_mention_ids": allowed_dates,
            "allowed_existing_task_refs": _existing_task_refs(trace),
        }
        input_hash = canonical_json_hash(content)
        payloads.append(
            TeacherPayload(
                payload_id=sha256_bytes(f"{case_id}\0{shard_index}\0{input_hash}".encode()),
                input_hash=input_hash,
                **content,
            )
        )
    return payloads
