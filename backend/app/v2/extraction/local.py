"""High-precision local extractor; ambiguous mutations stay unresolved."""

from __future__ import annotations

import re

from ...models import Clause, ClauseAnnotation
from ...preprocessing.unicode_normalizer import normalize_for_match
from ...utils.hashing import stable_hash
from ..models import (
    ClauseRelevance, DateMentionV2, ExtractionDecision, PrimaryResolution,
    ReasonCode, RelevanceClass, TaskEventV2, TaskOperation,
)
from ..segmentation import SegmentV2


_LABELLED_CREATE = re.compile(
    r"\btask\s+(?P<label>[A-Za-z0-9-]+)\s*[:：-]\s*(?P<action>[^,.]+?)(?:,?\s*(?:owner|người phụ trách)\s+(?P<owner>[\wÀ-ỹ'-]+))?(?:$|[,.])",
    re.I,
)
_NAMED_COMMITMENT = re.compile(r"^(?P<owner>[\wÀ-ỹ'-]+)\s+(?:sẽ|will)\s+(?P<action>[^.?!,;]+)", re.I)
_FIRST_PERSON_COMMITMENT = re.compile(r"^(?:dạ[, ]+|vâng[, ]+)?(?:em|tôi|mình|anh|chị|i)\s+(?:sẽ|will|nhận|phụ trách)\s+(?P<action>[^.?!,;]+)", re.I)
_ASSIGN_CREATE = re.compile(r"^(?:giao|phân công|assign)\s+(?:cho\s+)?(?P<owner>[\wÀ-ỹ'-]+)\s+(?:làm|xử lý|phụ trách|viết|soạn|kiểm tra|review|cập nhật)\s*(?P<action>[^.?!,;]+)", re.I)
_VOCATIVE_ASSIGN = re.compile(r"^(?P<owner>[\wÀ-ỹ'-]+),.*?\b(?:giao|assign)\s+(?:cho\s+)?(?:em|anh|chị|bạn)\s+(?P<action>[^.?!,;]+)", re.I)
_PLEASE_ASSIGN = re.compile(r"^(?P<owner>[A-Za-zÀ-ỹ'-]+),?\s+(?:please\s+|hãy\s+|giúp\s+)?(?P<action>(?:create|prepare|send|complete|update|check|review|fix|confirm|upload|schedule|run|draft|implement|tạo|chuẩn bị|gửi|hoàn thiện|cập nhật|kiểm tra|rà soát|sửa|xác nhận|chạy|soạn|viết|triển khai|xử lý)\b[^.?!,;]+)", re.I)
_ENGLISH_ASSIGN = re.compile(r"^assign\s+(?P<action>[^.?!,;]+?)\s+to\s+(?P<owner>[A-Za-z'-]+)", re.I)
_CANCEL = re.compile(r"(?:hủy|không làm|cancel)\s+(?:task\s+)?(?P<target>[\wÀ-ỹ' -]+)", re.I)
_REASSIGN = re.compile(r"(?:chuyển|bàn giao|reassign)\s+(?:task\s+)?(?P<target>[\wÀ-ỹ' -]+?)\s+(?:cho|to)\s+(?P<owner>[\wÀ-ỹ'-]+)", re.I)
_RENAME = re.compile(r"(?:task\s+)?(?P<target>[A-Za-z0-9-]+)\s+(?:đổi tên thành|rename(?:d)? to)\s+(?P<action>[^.?!,;]+)", re.I)
_DEADLINE = re.compile(r"(?:deadline|hạn)\s+(?:của\s+)?(?:task\s+)?(?P<target>[A-Za-z0-9-]+)\s+(?:đổi sang|là|to)\b", re.I)
_VAGUE_MUTATION = re.compile(r"\b(?:hủy|chuyển|đổi deadline|đổi tên|cancel|reassign)\b", re.I)
_QUESTION = re.compile(r"\?|\b(?:có thể|nên|liệu|hay là|could|should|would)\b", re.I)
_ROOT_QUESTION = re.compile(r"^(?:ai|gì|sao|khi nào|bao giờ|có cần|có nên|liệu|why|what|who|when|where|how|should|could|would|do we|can we)\b", re.I)
_PROGRESS = re.compile(r"\b(?:đang|đã|xong|hoàn thành|progress|completed?)\b", re.I)
_ACTION_VERB = re.compile(
    r"\b(?:tao|chuan bi|gui|hoan thien|cap nhat|kiem tra|ra soat|sua|xac nhan|"
    r"upload|dat lich|chay|soan|viet|trien khai|xu ly|cai dat|setup|test|review|"
    r"fix|deploy|cau hinh|kiem thu|phan tich|tong hop|thu thap|lap|build|bo sung|"
    r"tich hop|thuc hien|toi uu|migrate|thiet ke|create|prepare|send|complete|"
    r"update|check|confirm|schedule|run|draft|implement|process|handle|write|add|"
    r"install|configure|analyze|collect|integrate|execute|optimize|design)\b",
    re.I,
)
_VAGUE_ACTION = re.compile(
    r"^(?:lam|thuc hien|xu ly|hoan thanh|cap nhat|check|update|do|handle|complete)"
    r"(?:\s+(?:theo|lai|do|nay|viec nay|phan nay|it|this|that))?\s*$",
    re.I,
)
_INVALID_OWNER = {
    "ai", "who", "someone", "anyone", "cung", "mọi", "moi", "chung", "team",
    "em", "anh", "chi", "chị", "ban", "bạn", "toi", "tôi", "minh", "mình", "i", "we",
}


def _event_id(segment_id: str, clause_id: str, operation: TaskOperation) -> str:
    return f"EVENT-{stable_hash(f'{segment_id}|{clause_id}|{operation.value}', 20)}"


def _speaker_name(value: str) -> str:
    return re.sub(r"^(?:anh|chị|em|mr|ms)\s+", "", value.strip(), flags=re.I)


def _valid_owner(value: str) -> bool:
    owner = normalize_for_match(_speaker_name(value))
    return bool(owner) and owner not in _INVALID_OWNER and len(owner) >= 2


def _clean_action(value: str) -> str:
    value = re.sub(r"\b(?:trước|vào|đến|hạn|deadline)\b.*$", "", value, flags=re.I)
    return value.strip(" ,;:-")


def _is_concrete_action(value: str) -> bool:
    action = _clean_action(value)
    normalized = normalize_for_match(action)
    if not normalized or _VAGUE_ACTION.fullmatch(normalized):
        return False
    verb = _ACTION_VERB.search(normalized)
    if not verb:
        return False
    tail = normalized[verb.end():]
    content = [token for token in re.findall(r"\w+", tail) if token not in {
        "lai", "theo", "do", "nay", "phan", "viec", "it", "this", "that", "the", "a", "an",
    }]
    return bool(content)


def _deadline_for_clause(clause_id: str, mentions: dict[str, DateMentionV2]) -> str:
    matches = [
        mention.mention_id
        for mention in mentions.values()
        if mention.clause_id == clause_id and mention.purpose == "DEADLINE"
    ]
    return matches[0] if len(matches) == 1 else ""


def _event(segment: SegmentV2, clause: Clause, operation: TaskOperation, **changes: str) -> TaskEventV2:
    return TaskEventV2(
        event_id=_event_id(segment.segment_id, clause.clause_id, operation),
        operation=operation,
        anchor_clause_id=clause.clause_id,
        source_clause_ids=(clause.clause_id,),
        global_order=clause.order_index,
        extraction_source="LOCAL_RULE",
        confidence=0.95,
        **changes,
    )


def extract_segment_locally(
    segment: SegmentV2,
    clauses_by_id: dict[str, Clause],
    mentions: dict[str, DateMentionV2],
    annotations: dict[str, ClauseAnnotation] | None = None,
    relevance: dict[str, ClauseRelevance] | None = None,
) -> tuple[PrimaryResolution, ...]:
    """Always issue one resolution for every primary clause in a segment."""

    resolutions: list[PrimaryResolution] = []
    for clause_id in segment.primary_clause_ids:
        clause = clauses_by_id[clause_id]
        text = clause.text_raw.strip()
        normalized = normalize_for_match(text)
        flags = annotations[clause_id].flags if annotations and clause_id in annotations else set()
        clause_relevance = relevance.get(clause_id) if relevance else None
        deadline_id = _deadline_for_clause(clause_id, mentions)
        labelled = _LABELLED_CREATE.search(text)
        commitment = _NAMED_COMMITMENT.search(text)
        first_person = _FIRST_PERSON_COMMITMENT.search(text)
        assigned = _ASSIGN_CREATE.search(text)
        vocative_assign = _VOCATIVE_ASSIGN.search(text)
        please_assign = _PLEASE_ASSIGN.search(text)
        english_assign = _ENGLISH_ASSIGN.search(text)
        cancel = _CANCEL.search(text)
        reassign = _REASSIGN.search(text)
        rename = _RENAME.search(text)
        deadline = _DEADLINE.search(text)
        # Mutations are evaluated before CREATE. A cancellation or handoff must
        # never leave the old task and accidentally create a second one.
        if cancel:
            target = cancel.group("target").strip()
            event = _event(segment, clause, TaskOperation.CANCEL, explicit_task_label=target if re.fullmatch(r"[A-Za-z0-9-]+", target) else "", target_action_hint="" if re.fullmatch(r"[A-Za-z0-9-]+", target) else target)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_CANCELLATION))
        elif reassign and _valid_owner(reassign.group("owner")):
            target = reassign.group("target").strip()
            event = _event(segment, clause, TaskOperation.REASSIGN, explicit_task_label=target if re.fullmatch(r"[A-Za-z0-9-]+", target) else "", target_action_hint="" if re.fullmatch(r"[A-Za-z0-9-]+", target) else target, assignee_patch=_speaker_name(reassign.group("owner")))
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_HANDOFF))
        elif rename and _is_concrete_action(rename.group("action")):
            event = _event(segment, clause, TaskOperation.RENAME, explicit_task_label=rename.group("target"), action_patch=rename.group("action").strip())
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_HANDOFF))
        elif deadline and deadline_id:
            event = _event(segment, clause, TaskOperation.REPLACE_DEADLINE, explicit_task_label=deadline.group("target"), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_DEADLINE_CHANGE))
        elif _VAGUE_MUTATION.search(text):
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.UNRESOLVED_REFERENCE, (), ReasonCode.VAGUE_REFERENCE))
        elif clause_relevance and clause_relevance.relevance_class is RelevanceClass.NOISE:
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.NO_EVENT, (), ReasonCode.INSUFFICIENT_CONTEXT))
        elif flags & {"BRAINSTORM", "HYPOTHETICAL", "PAST_COMPLETED", "FUTURE_DISCUSSION", "REJECTION"}:
            reason = ReasonCode.PAST_WORK if "PAST_COMPLETED" in flags else ReasonCode.SUGGESTION_ONLY
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.NO_EVENT, (), reason))
        elif _ROOT_QUESTION.search(normalized):
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.NO_EVENT, (), ReasonCode.QUESTION_ONLY))
        elif labelled and _is_concrete_action(labelled.group("action")):
            event = _event(segment, clause, TaskOperation.CREATE, explicit_task_label=labelled.group("label"), action_patch=_clean_action(labelled.group("action")), assignee_patch=(labelled.group("owner") or "").strip(), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_ASSIGNMENT))
        elif first_person and _is_concrete_action(first_person.group("action")) and _valid_owner(clause.speaker_name):
            event = _event(segment, clause, TaskOperation.CREATE, action_patch=_clean_action(first_person.group("action")), assignee_patch=_speaker_name(clause.speaker_name), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_COMMITMENT))
        elif commitment and _is_concrete_action(commitment.group("action")) and _valid_owner(commitment.group("owner")):
            event = _event(segment, clause, TaskOperation.CREATE, action_patch=_clean_action(commitment.group("action")), assignee_patch=_speaker_name(commitment.group("owner")), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_COMMITMENT))
        elif assigned and _valid_owner(assigned.group("owner")):
            verb = re.search(r"\b(?:làm|xử lý|phụ trách|viết|soạn|kiểm tra|review|cập nhật)\b", text, re.I)
            action = text[verb.start():] if verb else assigned.group("action")
            if _is_concrete_action(action):
                event = _event(segment, clause, TaskOperation.CREATE, action_patch=_clean_action(action), assignee_patch=_speaker_name(assigned.group("owner")), deadline_mention_id=deadline_id)
                resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_ASSIGNMENT))
            else:
                resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.UNRESOLVED_REFERENCE, (), ReasonCode.VAGUE_REFERENCE))
        elif vocative_assign and _is_concrete_action(vocative_assign.group("action")) and _valid_owner(vocative_assign.group("owner")):
            event = _event(segment, clause, TaskOperation.CREATE, action_patch=_clean_action(vocative_assign.group("action")), assignee_patch=vocative_assign.group("owner").strip(), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_ASSIGNMENT))
        elif english_assign and _is_concrete_action(english_assign.group("action")) and _valid_owner(english_assign.group("owner")):
            event = _event(segment, clause, TaskOperation.CREATE, action_patch=_clean_action(english_assign.group("action")), assignee_patch=english_assign.group("owner"), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_ASSIGNMENT))
        elif please_assign and _is_concrete_action(please_assign.group("action")) and _valid_owner(please_assign.group("owner")):
            event = _event(segment, clause, TaskOperation.CREATE, action_patch=_clean_action(please_assign.group("action")), assignee_patch=_speaker_name(please_assign.group("owner")), deadline_mention_id=deadline_id)
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_ASSIGNMENT))
        elif labelled or first_person or commitment or assigned or vocative_assign or please_assign or english_assign:
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.UNRESOLVED_REFERENCE, (), ReasonCode.VAGUE_REFERENCE))
        else:
            reason = ReasonCode.QUESTION_ONLY if _QUESTION.search(text) else ReasonCode.PROGRESS_UPDATE if _PROGRESS.search(text) else ReasonCode.INSUFFICIENT_CONTEXT
            resolutions.append(PrimaryResolution(clause_id, ExtractionDecision.NO_EVENT, (), reason))
    return tuple(resolutions)
