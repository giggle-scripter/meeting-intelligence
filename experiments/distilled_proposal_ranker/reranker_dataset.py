"""Gold-isolated reranker labels and hard-negative pairs."""

from __future__ import annotations

from dataclasses import dataclass

from typing import Any

from .contracts import GroundedSpan, ProposalRecord, RerankerExample
from .hashing import canonical_json_hash, sha256_bytes
from .hard_negatives import select_hard_negatives
from .trace_reader import clause_map


@dataclass(frozen=True)
class RerankerLabel:
    proposal_id: str
    label: float
    sample_weight: float
    paired_negative_ids: tuple[str, ...]


def character_iou(left: GroundedSpan, right: GroundedSpan) -> float:
    if left.clause_id != right.clause_id:
        return 0.0
    intersection = max(0, min(left.end, right.end) - max(left.start, right.start))
    union = max(left.end, right.end) - min(left.start, right.start)
    return intersection / union if union else 0.0


def label_training_proposals(
    proposals: list[ProposalRecord], gold_spans: list[GroundedSpan], *, human_weight: float = 1.0
) -> list[RerankerLabel]:
    labels: list[RerankerLabel] = []
    positive_ids: list[str] = []
    raw_labels: dict[str, float] = {}
    for proposal in proposals:
        exact = any(proposal.action_span == gold for gold in gold_spans)
        best_iou = max((character_iou(proposal.action_span, gold) for gold in gold_spans), default=0.0)
        label = 1.0 if exact else best_iou if best_iou >= 0.8 else 0.0
        raw_labels[proposal.proposal_id] = label
        if label == 1.0:
            positive_ids.append(proposal.proposal_id)
    for proposal in proposals:
        pairs = ()
        if proposal.proposal_id in positive_ids:
            pairs = tuple(item.proposal_id for item in select_hard_negatives(proposals, proposal.proposal_id))
        labels.append(RerankerLabel(proposal.proposal_id, raw_labels[proposal.proposal_id], human_weight, pairs))
    return labels


def _render_clauses(refs: list[str], clauses: dict[str, dict[str, Any]]) -> str:
    values = []
    for clause_id in refs:
        clause = clauses.get(clause_id)
        if clause:
            values.append(f"[{clause_id}] {clause.get('speaker_name') or clause.get('speaker_id') or 'UNKNOWN'} | {clause['text_raw']}")
    return " || ".join(values) if values else "NONE"


def serialize_proposal(
    proposal: ProposalRecord,
    trace: dict[str, Any],
    tokenizer: Any,
    *,
    max_length: int = 512,
) -> str:
    clauses = clause_map(trace)
    primary = clauses[proposal.action_span.clause_id]
    text = primary["text_raw"]
    marked = text[: proposal.action_span.start] + "[ACT]" + proposal.action_span.text + "[/ACT]" + text[proposal.action_span.end :]
    action = f"[ACTION] {primary.get('speaker_name') or primary.get('speaker_id') or 'UNKNOWN'} | {marked}"
    authority = f"[AUTHORITY] {_render_clauses(proposal.authority_refs, clauses)}"
    acceptance = f"[ACCEPTANCE] {_render_clauses(proposal.acceptance_refs, clauses)}"
    owner = f"[OWNER] {_render_clauses(proposal.owner_refs, clauses)}"
    date_values = trace.get("date_mentions", {})
    if isinstance(date_values, list):
        date_values = {item["date_mention_id"]: item for item in date_values}
    deadline_values = []
    for mention_id in proposal.deadline_refs:
        mention = date_values.get(mention_id)
        if mention:
            deadline_values.append(f"{mention_id} | {mention.get('raw_text', '')}")
    deadline = "[DEADLINE] " + (" || ".join(deadline_values) if deadline_values else "NONE")
    negative = f"[NEGATIVE] {_render_clauses(proposal.negative_refs, clauses)}"
    relations = "[RELATIONS] " + (
        " || ".join(
            f"{item.relation_type}:{item.distance}:{item.chronology}:{item.support_clause_id}"
            for item in sorted(proposal.relations, key=lambda value: (value.relation_type, value.distance, value.support_clause_id))
        )
        or "NONE"
    )
    core = "\n".join((action, authority, acceptance, owner, deadline, negative, relations))
    core_tokens = tokenizer(core, add_special_tokens=True, truncation=False)["input_ids"]
    if len(core_tokens) > max_length:
        raise ValueError("STOP_OVERSIZED_CORE_EVIDENCE")
    context_candidates = sorted(
        clauses.values(),
        key=lambda item: (abs(int(item.get("order_index", 0)) - proposal.order_index), int(item.get("order_index", 0))),
    )
    accepted: list[dict[str, Any]] = []
    for clause in context_candidates:
        candidate = accepted + [clause]
        candidate.sort(key=lambda item: int(item.get("order_index", 0)))
        context = "[CONTEXT] " + " || ".join(
            f"[{item['clause_id']}] {item.get('speaker_name') or item.get('speaker_id') or 'UNKNOWN'} | {item['text_raw']}"
            for item in candidate
        )
        if len(tokenizer(core + "\n" + context, add_special_tokens=True, truncation=False)["input_ids"]) <= max_length:
            accepted = candidate
    context = "[CONTEXT] " + (
        " || ".join(
            f"[{item['clause_id']}] {item.get('speaker_name') or item.get('speaker_id') or 'UNKNOWN'} | {item['text_raw']}"
            for item in accepted
        )
        if accepted
        else "NONE"
    )
    return core + "\n" + context


def build_reranker_examples(
    proposals: list[ProposalRecord],
    trace: dict[str, Any],
    gold_spans: list[GroundedSpan],
    tokenizer: Any,
    *,
    outer_fold: int,
    max_length: int = 512,
) -> list[RerankerExample]:
    labels = {item.proposal_id: item for item in label_training_proposals(proposals, gold_spans)}
    examples = []
    for proposal in proposals:
        serialized = serialize_proposal(proposal, trace, tokenizer, max_length=max_length)
        input_value = {"case_id": proposal.case_id, "proposal_id": proposal.proposal_id, "serialized_input": serialized}
        label = labels[proposal.proposal_id]
        examples.append(
            RerankerExample(
                example_id=sha256_bytes(f"{proposal.case_id}\0{proposal.proposal_id}".encode()),
                input_hash=canonical_json_hash(input_value),
                case_id=proposal.case_id,
                outer_fold=outer_fold,
                proposal_id=proposal.proposal_id,
                serialized_input=serialized,
                label=label.label,
                sample_weight=label.sample_weight,
                paired_negative_ids=list(label.paired_negative_ids),
            )
        )
    return examples
