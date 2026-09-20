"""Deterministic authority routing for span-grounded action candidates.

The router is intentionally independent from the legacy clause classifier.  It
can run in shadow or suppress legacy create events that fail its authority
policy in assist mode; it never creates an event by itself.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import re

from .action_candidates import ActionCandidate, CandidateState
from backend.app.models import Clause


class CommitmentRoute(str, Enum):
    LOCAL_CREATE = "LOCAL_CREATE"
    AI_CREATE_CHECK = "AI_CREATE_CHECK"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    DROP = "DROP"


class AuthorityKind(str, Enum):
    DIRECT_ASSIGNMENT = "DIRECT_ASSIGNMENT"
    SELF_COMMITMENT = "SELF_COMMITMENT"
    EXPLICIT_ACCEPTANCE = "EXPLICIT_ACCEPTANCE"
    CONFIRMED_ACTION = "CONFIRMED_ACTION"
    FINAL_RECAP_CONFIRMATION = "FINAL_RECAP_CONFIRMATION"
    QUESTION_UNCONFIRMED = "QUESTION_UNCONFIRMED"
    SUGGESTION_ONLY = "SUGGESTION_ONLY"
    CONDITIONAL = "CONDITIONAL"
    HYPOTHETICAL = "HYPOTHETICAL"
    PROGRESS_ONLY = "PROGRESS_ONLY"
    PAST_COMPLETED = "PAST_COMPLETED"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    REJECTION = "REJECTION"
    CANCELLATION = "CANCELLATION"
    ADMIN_FOLLOWUP = "ADMIN_FOLLOWUP"


@dataclass(frozen=True)
class CommitmentDecision:
    candidate_id: str
    route: CommitmentRoute
    authority_kind: AuthorityKind
    reasons: tuple[str, ...]


_CONDITIONAL_RE = re.compile(
    r"\b(?:nếu|khi\s+nào|sau\s+khi|trong\s+lúc\s+chờ|khi\s+có|"
    r"có\s+.+?\s+(?:thì\s+)?(?:em|tôi|mình|anh|chị)\s+sẽ)\b",
    re.IGNORECASE,
)
_FOLLOWUP_COMMITMENT_RE = re.compile(
    r"\b(?:"
    r"liên\s+hệ|theo\s+dõi|check(?:\s+mail)?|confirm|"
    r"mở\s+meeting\s+note|"
    r"gửi\s+(?:file|link|biên\s+bản|checklist|meeting\s+note|góp\s+ý|request|sau\s+buổi\s+họp)|"
    r"báo\s+(?:sau|lại)|soạn\s+sẵn\s+test\s+case|test\s+offline|"
    r"cập\s+nhật\s+mẫu\s+chung\s+rồi\s+gửi\s+lại|"
    r"cập\s+nhật\s+(?:kết\s+quả|danh\s+sách\s+task|priority|kết\s+quả\s+profiling)|"
    r"update\s+(?:priority|schema)|"
    r"cấp\s+(?:quyền|vpn)"
    r")\b",
    re.IGNORECASE,
)


def _authority_kind(candidate: ActionCandidate, clauses_by_id: dict[str, Clause]) -> AuthorityKind:
    flags = set(candidate.negative_signals)
    source_text = " ".join(
        clauses_by_id[clause_id].text_raw
        for clause_id in candidate.primary_clause_ids
        if clause_id in clauses_by_id
    )
    if candidate.state == CandidateState.ACCEPTED:
        return AuthorityKind.EXPLICIT_ACCEPTANCE
    if (
        "ROOT_QUESTION" in flags
        or (
            candidate.state == CandidateState.REFERENCE_ONLY
            and "QUESTION" in " ".join(candidate.commitment_signals)
        )
    ):
        return AuthorityKind.QUESTION_UNCONFIRMED
    if "REJECTION" in flags:
        return AuthorityKind.REJECTION
    if "CANCELLATION" in flags:
        return AuthorityKind.CANCELLATION
    if "PAST_COMPLETED" in flags:
        return AuthorityKind.PAST_COMPLETED
    if "HYPOTHETICAL" in flags:
        return AuthorityKind.HYPOTHETICAL
    if "SUGGESTION_ONLY" in flags:
        return AuthorityKind.SUGGESTION_ONLY
    if "PROGRESS_UPDATE" in flags:
        return AuthorityKind.PROGRESS_ONLY
    if "ADMIN_FOLLOWUP" in flags:
        return AuthorityKind.ADMIN_FOLLOWUP
    if _CONDITIONAL_RE.search(source_text):
        return AuthorityKind.CONDITIONAL
    if _FOLLOWUP_COMMITMENT_RE.search(source_text):
        return AuthorityKind.ADMIN_FOLLOWUP
    if candidate.candidate_kind in {"MUTATION", "REFERENCE", "UNKNOWN"} or candidate.state == CandidateState.REFERENCE_ONLY:
        return AuthorityKind.REFERENCE_ONLY
    if "DIRECT_ASSIGNMENT" in candidate.commitment_signals:
        return AuthorityKind.DIRECT_ASSIGNMENT
    if "FIRST_PERSON_COMMITMENT" in candidate.commitment_signals:
        return AuthorityKind.SELF_COMMITMENT
    if candidate.state == CandidateState.CONFIRMED:
        return AuthorityKind.CONFIRMED_ACTION
    return AuthorityKind.REFERENCE_ONLY


def route_commitments(
    candidates: list[ActionCandidate],
    clauses_by_id: dict[str, Clause],
    *,
    active_authorities: frozenset[AuthorityKind],
) -> list[CommitmentDecision]:
    """Classify authority before any score or legacy event can promote a task."""

    decisions = []
    hard_negatives = {
        AuthorityKind.QUESTION_UNCONFIRMED,
        AuthorityKind.HYPOTHETICAL,
        AuthorityKind.PAST_COMPLETED,
        AuthorityKind.REJECTION,
        AuthorityKind.CANCELLATION,
    }
    context_only = {
        AuthorityKind.SUGGESTION_ONLY,
        AuthorityKind.CONDITIONAL,
        AuthorityKind.PROGRESS_ONLY,
        AuthorityKind.REFERENCE_ONLY,
        AuthorityKind.ADMIN_FOLLOWUP,
        AuthorityKind.FINAL_RECAP_CONFIRMATION,
    }
    for candidate in candidates:
        authority = _authority_kind(candidate, clauses_by_id)
        reasons = [authority.value]
        if authority in hard_negatives:
            route = CommitmentRoute.DROP
            reasons.append("HARD_NEGATIVE")
        elif authority in context_only:
            route = CommitmentRoute.CONTEXT_ONLY
        elif not candidate.action_spans:
            route = CommitmentRoute.DROP
            reasons.append("NO_CONCRETE_ACTION_SPAN")
        elif authority in active_authorities:
            route = CommitmentRoute.LOCAL_CREATE
        else:
            route = CommitmentRoute.AI_CREATE_CHECK
            reasons.append("OUTSIDE_ACTIVE_AUTHORITY")
        decisions.append(CommitmentDecision(
            candidate_id=candidate.candidate_id,
            route=route,
            authority_kind=authority,
            reasons=tuple(reasons),
        ))
    return decisions


def summarize_commitment_decisions(
    decisions: list[CommitmentDecision],
) -> dict[str, dict[str, int]]:
    return {
        "route_counts": dict(
            sorted(Counter(item.route.value for item in decisions).items())
        ),
        "authority_counts": dict(
            sorted(Counter(item.authority_kind.value for item in decisions).items())
        ),
    }
