"""Pure offline challenger adapter using production validation/reduction/serialization."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from backend.app.candidate.proposal import TaskCreateProposal
from backend.app.models import Clause, ClauseAnnotation, DateMention, PipelineDiagnostics, TaskEvent
from backend.app.output.serializer import build_pipeline_result
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.reduction.reducer import reduce_task_events
from backend.app.verification.proposal_validator import validate_task_create_proposal

from .contracts import ProposalRecord
from .safety import SafetyReport
from .trace_reader import records


ACCEPTANCE_SUPPORT_FLAGS = {"CONFIRMATION", "DIRECT_ASSIGNMENT", "FIRST_PERSON_COMMITMENT"}
BLOCKED_SUPPORT_FLAGS = {"REJECTION", "CANCELLATION", "HYPOTHETICAL", "PAST_COMPLETED"}
NEVER_OVERRIDE_FLAGS = {"HYPOTHETICAL", "PAST_COMPLETED", "PROGRESS_ONLY", "REJECTED_PROPOSAL", "FUTURE_DISCUSSION"}
EXPERIMENTAL_NEGATIVE_FLAGS = NEVER_OVERRIDE_FLAGS | {"RECAP_ITEM", "MUTATION_ONLY", "CANCELLATION"}


def _annotation_flags(trace: dict[str, Any], clause_id: str) -> set[str]:
    return set(trace.get("annotations", {}).get(clause_id, {}).get("flags", []))


def structured_acceptance_overrides(proposal: ProposalRecord, trace: dict[str, Any]) -> dict[str, frozenset[str]]:
    primary_id = proposal.action_span.clause_id
    primary_flags = _annotation_flags(trace, primary_id)
    if primary_flags & NEVER_OVERRIDE_FLAGS:
        return {}
    clauses = {item["clause_id"]: item for item in trace.get("clauses", [])}
    primary = clauses.get(primary_id)
    if primary is None or primary["text_raw"][proposal.action_span.start : proposal.action_span.end] != proposal.action_span.text:
        return {}
    seeds = records(trace.get("proposal_evidence_seeds_v3"))
    seed_orders = {
        item.get("clause_id"): (int(item.get("order_index", 0)), set(item.get("roles", [])))
        for item in seeds
    }
    allowed: set[str] = set()
    for relation in proposal.relations:
        if relation.relation_type not in {"ACCEPTS", "AUTHORIZES"} or relation.chronology != "AFTER" or relation.distance > 3:
            continue
        support_clause = clauses.get(relation.support_clause_id)
        if support_clause is None:
            continue
        support_flags = _annotation_flags(trace, relation.support_clause_id)
        if not support_flags & ACCEPTANCE_SUPPORT_FLAGS or support_flags & BLOCKED_SUPPORT_FLAGS:
            continue
        start_order = int(primary.get("order_index", 0))
        support_order = int(support_clause.get("order_index", 0))
        crosses_seed = any(
            start_order < order < support_order and roles & {"ACTION", "REFERENCE"}
            for clause_id, (order, roles) in seed_orders.items()
            if clause_id not in {primary_id, relation.support_clause_id}
        )
        if crosses_seed:
            continue
        if "ROOT_QUESTION" in primary_flags or primary["text_raw"].rstrip().endswith("?"):
            allowed.add("QUESTION")
        if "SUGGESTION_ONLY" in primary_flags:
            allowed.add("SUGGESTION")
    return {primary_id: frozenset(allowed)} if allowed else {}


def experimental_create_guard(proposal: ProposalRecord, trace: dict[str, Any]) -> str | None:
    # A selected UNRESOLVED proposal is the reranker's CREATE prediction. Typed
    # lifecycle proposals remain barred from creating a new identity.
    if proposal.kind not in {"CREATE", "UNRESOLVED"}:
        return "NON_CREATE_CANNOT_MINT_IDENTITY"
    if "SIBLING_AMBIGUITY" in proposal.ambiguity_flags:
        return "SIBLING_AMBIGUITY"
    if not (proposal.authority_refs or proposal.acceptance_refs):
        return "MISSING_TRANSCRIPT_AUTHORITY"
    flags = _annotation_flags(trace, proposal.action_span.clause_id)
    if flags & EXPERIMENTAL_NEGATIVE_FLAGS:
        return "NEGATIVE_LIFECYCLE_GUARD"
    if ("ROOT_QUESTION" in flags or "SUGGESTION_ONLY" in flags) and not structured_acceptance_overrides(proposal, trace):
        return "QUESTION_OR_SUGGESTION_WITHOUT_ACCEPTANCE"
    return None


def _domain_objects(trace: dict[str, Any]) -> tuple[dict[str, Clause], dict[str, ClauseAnnotation], dict[str, DateMention]]:
    clauses = {item["clause_id"]: Clause(**item) for item in trace.get("clauses", [])}
    annotations = {
        clause_id: ClauseAnnotation(
            clause_id=clause_id,
            flags=set(value.get("flags", [])),
            rule_confidence=float(value.get("rule_confidence", 0.0)),
            score=int(value.get("score", 0)),
        )
        for clause_id, value in trace.get("annotations", {}).items()
    }
    raw_mentions = trace.get("date_mentions", {})
    if isinstance(raw_mentions, list):
        raw_mentions = {item["date_mention_id"]: item for item in raw_mentions}
    mentions = {mention_id: DateMention(**value) for mention_id, value in raw_mentions.items()}
    return clauses, annotations, mentions


def _baseline_events(trace: dict[str, Any]) -> list[TaskEvent]:
    return [TaskEvent(**item) for item in trace.get("events_after_deduplication", [])]


def replay_case(
    repo_root: Path,
    case_id: str,
    baseline_trace: dict[str, Any],
    selected: list[ProposalRecord],
    safety: SafetyReport,
) -> tuple[dict[str, Any], dict[str, Any]]:
    clauses, annotations, mentions = _domain_objects(baseline_trace)
    events = _baseline_events(baseline_trace)
    provenance = [{"event_id": item.event_id, "provenance": "BASELINE"} for item in events]
    failures = []
    for offset, proposal in enumerate(selected, start=1):
        primary_id = proposal.action_span.clause_id
        if primary_id not in clauses:
            safety.violation("unknown_clause_id_count", proposal.proposal_id)
            failures.append({"proposal_id": proposal.proposal_id, "reason": "UNKNOWN_CLAUSE"})
            continue
        clause = clauses[primary_id]
        if (
            proposal.action_span.start < 0
            or proposal.action_span.end <= proposal.action_span.start
            or proposal.action_span.end > len(clause.text_raw)
            or clause.text_raw[proposal.action_span.start : proposal.action_span.end] != proposal.action_span.text
        ):
            safety.violation("invalid_action_span_count", proposal.proposal_id)
            failures.append({"proposal_id": proposal.proposal_id, "reason": "INVALID_ACTION_SPAN"})
            continue
        guard = experimental_create_guard(proposal, baseline_trace)
        if guard:
            flags = _annotation_flags(baseline_trace, primary_id)
            if guard == "NON_CREATE_CANNOT_MINT_IDENTITY" and proposal.kind == "UPDATE":
                safety.violation("update_minted_identity_count", proposal.proposal_id)
            elif guard == "SIBLING_AMBIGUITY":
                safety.violation("sibling_unsafe_merge_count", proposal.proposal_id)
            elif guard == "QUESTION_OR_SUGGESTION_WITHOUT_ACCEPTANCE":
                counter = "suggestion_only_create_count" if "SUGGESTION_ONLY" in flags else "question_without_acceptance_create_count"
                safety.violation(counter, proposal.proposal_id)
            elif guard == "NEGATIVE_LIFECYCLE_GUARD":
                if "PAST_COMPLETED" in flags:
                    safety.violation("past_completed_create_count", proposal.proposal_id)
                elif "RECAP_ITEM" in flags:
                    safety.violation("recap_duplicate_count", proposal.proposal_id)
                elif "MUTATION_ONLY" in flags:
                    safety.violation("mutation_only_create_count", proposal.proposal_id)
            failures.append({"proposal_id": proposal.proposal_id, "reason": guard})
            continue
        support_ids = set(proposal.authority_refs + proposal.acceptance_refs + proposal.owner_refs + proposal.negative_refs)
        support_ids.update(relation.support_clause_id for relation in proposal.relations)
        if not support_ids <= set(clauses):
            safety.violation("unknown_clause_id_count", proposal.proposal_id)
            failures.append({"proposal_id": proposal.proposal_id, "reason": "UNKNOWN_SUPPORT_CLAUSE"})
            continue
        unknown_deadlines = [item for item in proposal.deadline_refs if item not in mentions]
        if unknown_deadlines:
            safety.violation("unknown_date_mention_id_count", proposal.proposal_id)
            failures.append({"proposal_id": proposal.proposal_id, "reason": "UNKNOWN_DATE_MENTION"})
            continue
        exact_duplicate = any(
            primary_id in event.source_clause_ids
            and normalize_for_match(event.action_text) == normalize_for_match(proposal.action_span.text)
            for event in events
        )
        if exact_duplicate:
            continue
        source_ids = [primary_id]
        for clause_id in proposal.authority_refs + proposal.acceptance_refs + proposal.owner_refs:
            if clause_id not in source_ids:
                source_ids.append(clause_id)
        deadline_id = proposal.deadline_refs[0] if proposal.deadline_refs else None
        if deadline_id and deadline_id in mentions and mentions[deadline_id].clause_id not in source_ids:
            source_ids.append(mentions[deadline_id].clause_id)
        candidate = TaskCreateProposal(
            source_clause_ids=source_ids,
            action_span=proposal.action_span.text,
            owner_span=None,
            deadline_mention_id=deadline_id,
            commitment_type="CONFIRMED_ACTION" if proposal.acceptance_refs else "ASSIGNMENT",
            confidence=1.0,
        )
        validation = validate_task_create_proposal(
            candidate,
            clauses_by_id=clauses,
            annotations=annotations,
            mentions=mentions,
            start_sequence=100000 + offset,
            allowed_source_clause_ids=set(clauses),
            required_primary_clause_ids={primary_id},
            structured_acceptance_overrides=structured_acceptance_overrides(proposal, baseline_trace),
        )
        if not validation.accepted or validation.event is None:
            failures.append({"proposal_id": proposal.proposal_id, "reason": list(validation.reasons)})
            continue
        events.append(validation.event)
        provenance.append({"event_id": validation.event.event_id, "provenance": "EXPERIMENTAL_OOF_CREATE", "proposal_id": proposal.proposal_id})
    states = reduce_task_events(events)
    metadata = json.loads((repo_root / "data/validation" / case_id / "metadata.json").read_text(encoding="utf-8-sig"))
    baseline_tasks = list(baseline_trace.get("final_tasks", []))
    baseline_by_identity = {
        normalize_for_match(item.get("task_name", "")): item
        for item in baseline_tasks
    }
    experimental_states = [
        state
        for state in states
        if "AI_CREATE_PROPOSAL" in state.extraction_sources
        and normalize_for_match(state.task_name) not in baseline_by_identity
    ]
    result = build_pipeline_result(
        metadata.get("meeting_title", case_id),
        metadata["meeting_date"],
        experimental_states,
        clauses,
        mentions,
        PipelineDiagnostics(),
        [],
        meeting_note_present=bool(baseline_trace.get("meeting_context", {}).get("note_present")),
    )
    generated = [asdict(item) for item in result.tasks]
    experimental_tasks = [
        item
        for item in generated
        if normalize_for_match(item.get("task_name", "")) not in baseline_by_identity
    ]
    # Q2 traces contain temporal sidecars that are not part of the public event
    # contract. Preserve their exact serialized baseline objects; only new
    # reducer-produced identities are appended by the offline challenger.
    public = {
        "meeting_title": result.meeting_title,
        "summary": result.summary,
        "tasks": baseline_tasks + experimental_tasks,
    }
    sidecar = {"case_id": case_id, "provenance": provenance, "rejections": failures}
    return public, sidecar
