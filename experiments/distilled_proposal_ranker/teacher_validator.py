"""Strict schema, hash, allowlist, and source-grounding validation."""

from __future__ import annotations

from .contracts import TeacherPayload, TeacherResponse


def validate_teacher_response(
    response: TeacherResponse,
    payload: TeacherPayload,
    *,
    expected_prompt_hash: str,
    expected_model_hash: str,
) -> TeacherResponse:
    if response.payload_id != payload.payload_id or response.payload_hash != payload.input_hash:
        raise ValueError("payload hash mismatch")
    if response.prompt_hash != expected_prompt_hash or response.model_hash != expected_model_hash:
        raise ValueError("prompt/model hash mismatch")
    proposals = {item.proposal_id: item for item in payload.proposals}
    clauses = {item.clause_id: item for item in payload.clauses}
    allowed_dates = set(payload.allowed_date_mention_ids)
    allowed_tasks = set(payload.allowed_existing_task_refs)
    seen: set[str] = set()
    for record in response.records:
        if record.proposal_id in seen:
            raise ValueError("duplicate proposal decision")
        seen.add(record.proposal_id)
        proposal = proposals.get(record.proposal_id)
        if proposal is None or record.action_span_ref != record.proposal_id:
            raise ValueError("proposal/action span reference outside payload")
        span = proposal.action_span
        clause = clauses.get(span.clause_id)
        if clause is None or clause.text_raw[span.start : span.end] != span.text:
            raise ValueError("action substring is not exact raw text")
        if any(item not in clauses for item in record.authority_clause_ids):
            raise ValueError("authority clause outside payload")
        if record.owner_clause_id is not None and record.owner_clause_id not in clauses:
            raise ValueError("owner clause outside payload")
        if record.deadline_mention_id is not None and record.deadline_mention_id not in allowed_dates:
            raise ValueError("deadline mention outside payload")
        if record.existing_task_ref is not None and record.existing_task_ref not in allowed_tasks:
            raise ValueError("existing task reference outside payload")
        if record.decision == "CREATE" and not record.authority_clause_ids:
            raise ValueError("CREATE lacks transcript authority")
        if record.decision == "UPDATE" and record.existing_task_ref not in allowed_tasks:
            raise ValueError("UPDATE target outside allowlist")
    return response
