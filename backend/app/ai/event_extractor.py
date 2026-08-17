"""Rule-first event extraction with a constrained AI fallback."""

from __future__ import annotations

import re
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from ..annotation.cue_patterns import ACTION_VERBS
from ..annotation.date_parser import extract_date_mentions
from ..models import (
    CandidateWindow, Clause, ClauseAnnotation, DateMention, MeetingNoteInput,
    TaskEvent,
)
from ..preprocessing.unicode_normalizer import normalize_for_match, normalize_text
from ..utils.ids import make_id
from ..utils.event_semantics import is_discourse_transition_cancel
from .client import AiClient
from .rule_guards import evaluate_action

if TYPE_CHECKING:
    from ..v2.models import NoteCue


LOGGER = logging.getLogger(__name__)
WORD = r"[\wÀ-ỹ'-]+"
HONORIFIC_RE = re.compile(r"^(?:anh|chị|chi|em|bạn|ban|mr|ms|mrs)\s+", re.I)
DATE_TRAILER_RE = re.compile(
    r"\s+(?:dl|deadline|hạn(?:\s+chót)?|xong\s+trước|trước|vào|đến|chậm nhất|"
    r"không muộn hơn|chưa\s+(?:có\s+)?deadline|không\s+(?:có\s+)?deadline|"
    r"k\s*dl|sau khi|trong vòng|trong tuần|cuối tuần|sáng mai|"
    r"chiều nay|trong\s+(?:hôm nay|ngày mai|ngày kia|today|tomorrow|day after tomorrow)|"
    r"hôm nay|ngày mai|từ|before|on|by|after|from|within|this week|"
    r"next week|today|tomorrow)\b.*$",
    re.I,
)
TRAILING_DIALOGUE_RE = re.compile(
    r"[,;]\s*(?:em|anh|chị|bạn|ban|you)\s+"
    r"(?:làm thế nào|thấy sao|có nhận|được không|nhé|please).*$",
    re.I,
)
TRAILING_INSTRUCTION_RE = re.compile(
    r"[,;]\s*(?:em|anh|chị|bạn|ban|you)\s+"
    r"(?:cứ|tạm|nhớ|hãy|cần|please)\b.*$",
    re.I,
)
DISCOURSE_PREFIX_RE = re.compile(
    r"^(?:(?:ừm|ừ|uhm|vậy|thế|rồi|ok|okay|à)[,\.\s]+)+",
    re.I,
)
GENERIC_DEADLINE_SUBJECT_RE = re.compile(
    r"^(?:mọi người|mỗi người|cả nhóm|chúng ta|team|all|everyone)\b",
    re.I,
)
FIRST_PERSON_RE = re.compile(
    r"^(?:dạ[,\s]+|vâng[,\s]+|ok[,\s]+)?"
    r"(?:em|tôi|mình|anh|chị|i|we)\s+"
    r"(?:xác nhận\s+)?(?:sẽ|nhận(?:\s+thay\s+" + WORD + r")?|phụ trách|"
    r"will|shall|will own|can take)\s+(.+)$",
    re.I,
)
EXPLICIT_ASSIGN_RE = re.compile(
    r"\b(?:giao|chốt|phân công|nhờ)\s+(?:cho\s+)?(?P<name>" + WORD + r")"
    r"(?:\s+phụ trách|\s+làm|\s+xử lý)?\s+(?P<action>.+)$",
    re.I,
)
OWNER_RE = re.compile(
    r"^(?P<name>" + WORD + r"),?\s+"
    r"(?:sẽ\s+|phụ trách\s+|chịu trách nhiệm\s+|"
    r"will\s+|will own\s+|owns?\s+|is responsible for\s+)"
    r"(?P<action>.+)$",
    re.I,
)
ROLE_DELIVERABLE_ASSIGN_RE = re.compile(
    r"\b(?P<role>[\wÀ-ỹ'-]+)\s+manager\s+(?:là|is)\s+"
    r"(?P<name>" + WORD + r").*?\b(?:deadline|hạn)\s+(?:cho|for)\s+"
    r"(?P<deliverable>.+?)\s+(?:là|is)\s+\d{1,2}[/-]\d{1,2}",
    re.I,
)
VOCATIVE_RE = re.compile(r"^(?P<name>" + WORD + r"),\s+(?P<body>.+)$", re.I)
SECOND_PERSON_DIRECTIVE_RE = re.compile(
    r"^(?:dạ[,\s]+)?(?:em|anh|chị|bạn|ban|you)\s+"
    r"(?:hãy\s+|cần\s+|nhớ\s+|giúp\s+|cố gắng\s+|please\s+)?"
    r"(?P<action>.+)$",
    re.I,
)
OBJECT_FRONTED_COMMITMENT_RE = re.compile(
    r"^(?:còn\s+|riêng\s+)?(?P<object>[^,;:.!?]{2,50}?)\s+"
    r"(?:thì\s+)?(?:em|tôi|mình|anh|chị|i|we)\s+"
    r"(?:sẽ|will|shall)\s+(?P<action>.+)$",
    re.I,
)

INVALID_ASSIGNEES = {
    "em", "anh", "chi", "chị", "toi", "tôi", "minh", "mình", "ban", "bạn",
    "cho", "ben", "bên", "ca", "cả", "dien", "diện", "nguoi", "người",
    "it", "this", "that", "we", "i", "you", "team", "ai", "who",
    "someone", "mọi người", "chung ta", "chúng ta", "can", "hay", "nho",
    "nhớ", "khong", "không", "se", "sẽ", "deadline", "scope", "script",
}
ROLE_ASSIGNEES = {
    "devops", "qa", "qc", "po", "pm", "ba", "infra", "security", "backend",
    "frontend", "design", "designer", "vendor", "team qa", "team devops",
}
PRONOUN_ACTIONS = {
    "việc đó", "phần này", "phần đó", "cả hai", "task này", "task đó",
    "it", "this", "that", "the task", "đúng như vậy", "như vậy",
}
VAGUE_ACTIONS = {
    "cố", "cố gắng", "ghi nhận", "đồng ý", "xác nhận", "hỗ trợ", "giúp",
    "xem xét", "tìm hiểu", "tìm hiểu thêm", "báo sau", "trao đổi sau",
    "recap", "recap nhanh", "đôn đốc", "theo dõi", "làm", "gửi", "kiểm tra",
    "làm đúng như vậy", "làm như vậy", "i accept", "i agree", "will do",
    "hoàn thành đúng hạn", "liên hệ sau cuộc họp", "can help", "noted",
    "viết chi tiết", "viết theo hướng dẫn", "làm theo hướng đó", "làm theo",
    "làm trong sáng nay", "làm hết sức", "gửi invite", "gửi invitation",
    "gửi changes sớm", "cập nhật trạng thái hàng ngày", "update tiến độ",
}
FOLLOWUP_COMMUNICATION_RE = re.compile(
    r"^(?:gửi (?:email|mail|tin nhắn)|báo lại|cập nhật tiến độ|"
    r"send (?:an )?email|report back)\b",
    re.I,
)
NON_OBJECT_WORDS = {
    "anh", "chi", "em", "toi", "minh", "ban", "cho", "lai", "them", "luon",
    "dung", "nhu", "vay", "som", "ngay", "bay", "gio", "sau", "truoc", "trong",
    "hom", "nay", "mai", "thuong", "xuyen", "nhe", "a", "please", "it", "this",
    "that", "soon", "today", "tomorrow", "later", "again",
}
NEGATIVE_CREATION_FLAGS = {
    "BRAINSTORM", "HYPOTHETICAL", "PAST_COMPLETED", "FUTURE_DISCUSSION",
    "CANCELLATION", "REJECTION",
}
UNCERTAIN_OR_QUESTION_RE = re.compile(
    r"\?|\b(?:cố gắng|dự kiến|chắc|hy vọng|em nghĩ nên|có cần|có nên|muốn hỏi|"
    r"xem có|chưa chốt|try to|might|maybe|could|should|would)\b",
    re.I,
)
ADMIN_FOLLOWUP_RE = re.compile(
    r"^(?:gửi|send|cập nhật|update|ghi|write)\b.*\b(?:meeting notes?|minutes|"
    r"recap|group chat|jira|board|sau meeting|sau cuộc họp|after (?:the )?meeting)\b",
    re.I,
)
HUMAN_NOTE_UNCERTAIN_RE = re.compile(
    r"\?|\b(?:chưa\s+chốt|chua\s+chot|có\s+thể|có\s+lẽ|"
    r"em\s+nghĩ|tôi\s+nghĩ|hy\s+vọng|maybe|might|could|should|"
    r"unresolved|pending|chưa\s+có\s+owner|chưa\s+assign)\b",
    re.I,
)
HUMAN_NOTE_NON_TASK_RE = re.compile(
    r"\b(?:không\s+(?:tạo|giao|có)\s+(?:thêm\s+)?task|"
    r"không\s+cần\s+task|không\s+phải\s+task|no\s+(?:new\s+)?task|"
    r"thay\s+vì\s+tạo\s+task|không\s+tự\s+ý\s+tách\s+task|"
    r"(?:đã\s+)?(?:hủy|huỷ|cancel(?:led)?)|đã\s+(?:hoàn\s+thành|xong)|"
    r"follow[- ]?up\s+thôi|(?:em\s+thấy|tôi\s+thấy|trong\s+board)"
    r"[^.]{0,50}\b(?:có|đã\s+có)\s+task|tuần\s+trước\s+có\s+task)\b",
    re.I,
)
HUMAN_NOTE_QUOTED_TASK_RE = re.compile(
    r"\b(?:task|action\s*item)\s*[\"“](?P<action>[^\"”]+)[\"”]",
    re.I,
)
HUMAN_NOTE_LABELED_TASK_RE = re.compile(
    r"\btask\s+[A-Z0-9-]+\s*(?:-|–|:)\s*(?P<action>[^,;.]+)",
    re.I,
)
HUMAN_NOTE_ASSIGN_RE = re.compile(
    r"\b(?:giao|assign(?:ed)?)\s+(?:cho|to)\s+"
    r"(?P<owner>" + WORD + r")\s+(?P<action>.+)$",
    re.I,
)
HUMAN_NOTE_OWNER_FIELD_RE = re.compile(
    r"\b(?:owner(?:\s+chính)?(?:\s+là)?|do|assign(?:ed)?\s+(?:cho|to))\s+"
    r"(?P<owner>" + WORD + r")\b",
    re.I,
)
HUMAN_NOTE_REASSIGN_OWNER_RE = re.compile(
    r"\b(?:chuyển\s+(?:chủ|owner)\s+(?:sang|cho)|handoff\s+(?:to|cho))\s+"
    r"(?:anh|chị|chi|bạn|ban|mr\.?|ms\.?)?\s*(?P<owner>" + WORD + r")\b",
    re.I,
)
HUMAN_NOTE_ACTION_OWNER_RE = re.compile(
    r"(?:-|–|,)\s*(?:hiện\s+)?(?:do\s+)?(?P<owner>" + WORD + r")\s+"
    r"(?:đang\s+làm|làm|phụ\s+trách|owner|chịu\s+trách\s+nhiệm)\b",
    re.I,
)
HUMAN_NOTE_OWNER_PREFIX_RE = re.compile(
    r"^(?:mà\s+|vậy\s+)?(?P<owner>" + WORD + r")\s*[,,:]?\s+"
    r"(?P<body>(?:(?:em|anh|chị|bạn|you)\s+)?(?:sẽ\s+|will\s+)?(?:"
    r"phụ\s+trách\s+|chịu\s+trách\s+nhiệm\s+|owner\s+)?(?:"
    r"tạo|chuẩn\s+bị|gửi|hoàn\s+thiện|hoàn\s+thành|cập\s+nhật|"
    r"kiểm\s+tra|rà\s+soát|sửa|xác\s+nhận|upload|đặt\s+lịch|chạy|"
    r"soạn|viết|triển\s+khai|xử\s+lý|test|review|fix|deploy|setup|"
    r"create|prepare|send|complete|update|check|implement|build|làm)\b.+)$",
    re.I,
)
NOTE_PROGRESS_PREFIX_RE = re.compile(
    r"^(?:dạ[,\s]+|vâng[,\s]+|ok[,\s]+)?"
    r"(?:(?:em|tôi|mình|anh|chị|we)\s+"
    r"(?:xác nhận\s+)?(?:hiện\s+đang|vẫn\s+đang|đang|currently\s+|are\s+currently\s+|are\s+)"
    r"|i(?:'m|\s+am)\s+(?:currently\s+)?|currently\s+)",
    re.I,
)


def _verb_pattern() -> re.Pattern[str]:
    values = sorted(ACTION_VERBS, key=len, reverse=True)
    return re.compile(r"^(?:" + "|".join(re.escape(item) for item in values) + r")\b", re.I)


ACTION_START_RE = _verb_pattern()
RECAP_MARKER_RE = re.compile(
    r"\b(?:tổng kết|chốt lại|recap|vậy thống nhất|thống nhất lại|"
    r"thống nhất như sau|action items?|"
    r"các đầu việc|danh sách công việc|final list|"
    r"(?:tôi|anh|em|mình|i)\s+điểm\s+qua)\b",
    re.I,
)
FINAL_RECAP_MARKER_RE = re.compile(
    r"\b(?:recap\s+(?:lần\s+)?cuối|recap\s+lại\s+tổng\s+thể|"
    r"recap\s+lại\s+các\s+task\s+đã\s+giao|"
    r"tổng\s+kết\s+(?:lần\s+)?cuối|tổng\s+kết\s+toàn\s+bộ|"
    r"tổng\s+cộng\s+các\s+task\s+hiện\s+tại|final\s+list|"
    r"final\s+recap|trạng\s+thái\s+cuối|"
    r"điểm\s+(?:nhanh\s+)?trạng\s+thái\s+từng\s+task|"
    r"(?:tôi|anh|em|mình|i)\s+điểm\s+qua)\b",
    re.I,
)
TASK_DEFINITION_RE = re.compile(
    r"\btask\s+(?P<label>[A-Z0-9]+)\s*(?:[:\-–])\s*"
    r"[“\"](?P<action>[^”\"]+)[”\"](?P<tail>.*?)(?=\btask\s+[A-Z0-9]+\s*(?:[:\-–])|$)",
    re.I,
)
TASK_RENAME_RE = re.compile(
    r"\btask\s+(?P<label>[A-Z0-9]+)\b.*?"
    r"(?:chính\s+thức\s+là|đổi\s+tên(?:\s+thành)?|renamed?\s+to)\s*"
    r"[“\"](?P<action>[^”\"]+)[”\"]",
    re.I,
)
COMPACT_TASK_RE = re.compile(
    r"(?<!\w)(?P<label>[A-Z])\s*\((?P<body>[^)]*)\)",
    re.I,
)
INLINE_FINAL_TASK_RE = re.compile(
    r"\btask\s+[\"“](?P<action>[^\"”]+)[\"”]\s*(?:[:\-–—])\s*"
    r"(?P<body>.*?)(?=\btask\s+[\"“]|$)",
    re.I,
)
RECAP_BULLET_RE = re.compile(r"(?:^|\s)-\s+(?P<body>.*?)(?=(?:\s-\s+)|$)", re.S)
ACTION_ANYWHERE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(item) for item in sorted(ACTION_VERBS, key=len, reverse=True)) + r")\b",
    re.I,
)


def _clean_action(value: str) -> str:
    value = TRAILING_DIALOGUE_RE.sub("", value)
    value = TRAILING_INSTRUCTION_RE.sub("", value)
    value = re.sub(
        r"\s*,\s*(?:cố gắng\s+)?(?:trước\s+)?"
        r"(?:thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ nhật)"
        r"(?:\s+(?:hoặc|hay)\s+(?:thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ nhật))?\b.*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s*,\s*(?:chưa|không)\s+có\s+deadline(?:\s+cứng)?\b.*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s*\((?:dl|deadline|hạn|không\s+(?:có\s+)?deadline|k\s*dl|"
        r"chưa\s+(?:có\s+)?deadline|thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|"
        r"chủ\s+nhật|cuối\s+ngày|sáng|chiều|tối)\b[^)]*\).*$",
        "",
        value,
        flags=re.I,
    )
    value = DATE_TRAILER_RE.sub("", value).strip(" .,:;?!-–—")
    value = re.sub(
        r"^(?:vậy\s+)?còn\s+(?:phần|việc|task)\s+",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"^(?:việc|task|phần)\s+(?=(?:" +
        "|".join(re.escape(item) for item in sorted(ACTION_VERBS, key=len, reverse=True)) +
        r")\b)",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"^(?:cố gắng|tranh thủ|cam kết(?:\s+sẽ)?|xác nhận(?:\s+sẽ)?)\s+",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"^(?P<verb>(?:gửi|send)\s+(?:bổ sung|thêm))\s+"
        r"có điều\s+(?:(?:mấy|vài)\s+)?cái\s+",
        r"\g<verb> ",
        value,
        flags=re.I,
    )
    compound = re.search(
        r"\b(?:và|rồi|sau đó|and then)\s+"
        r"(?P<deliverable>(?:viết|soạn|gửi|hoàn thành|hoàn thiện|"
        r"tạo|build|deploy|write|draft|send|complete)\b.+)$",
        value,
        flags=re.I,
    )
    if compound and re.match(
        r"^(?:tập hợp|thu thập|kiểm tra|rà soát|phân tích|"
        r"compile|collect|check|review)\b",
        value,
        flags=re.I,
    ):
        value = compound.group("deliverable")
    value = re.sub(
        r"\s+(?:và|and|đồng thời|nhé|ạ|please)$",
        "",
        value,
        flags=re.I,
    ).strip()
    value = re.sub(
        r"\s*\((?:unresolved|resolved|active|cancelled?|canceled?)\)?\s*$",
        "",
        value,
        flags=re.I,
    ).strip()
    return value[:1].upper() + value[1:] if value else ""


def _is_concrete_action(value: str) -> bool:
    normalized = normalize_for_match(value).strip()
    base_guard = evaluate_action(value)
    if not base_guard.concrete:
        return False
    if (
        normalized.startswith("cap nhat lai task")
        or re.match(r"^(?:cap nhat|update) (?:lai )?(?:task|ticket)\b", normalized)
        or re.match(r"^(?:cap nhat|update) (?:lai )?(?:task|ticket)\s*[a-z]*[- ]?\d+\b", normalized)
        or normalized.startswith("update the task")
        or normalized.startswith("tao task")
        or normalized == "update lich"
        or ADMIN_FOLLOWUP_RE.search(value)
    ):
        return False
    if not normalized or normalized in {normalize_for_match(item) for item in PRONOUN_ACTIONS | VAGUE_ACTIONS}:
        return False
    if not ACTION_START_RE.match(value):
        return False
    words = re.findall(r"\w+", normalized)
    if len(words) < 2:
        return False
    verb_word_count = max(
        (
            len(normalize_for_match(verb).split())
            for verb in ACTION_VERBS
            if normalized == normalize_for_match(verb)
            or normalized.startswith(normalize_for_match(verb) + " ")
        ),
        default=1,
    )
    object_words = words[verb_word_count:]
    object_text = " ".join(object_words)
    if object_text in {
        "final", "draft", "ban nhap", "chi tiet", "theo huong dan",
        "theo huong do", "trong sang nay", "het suc", "changes som",
    }:
        return False
    if re.fullmatch(r"(?:phan|viec|task) (?:nay|do)|it|this|that", object_text):
        return False
    if re.fullmatch(
        r"(?:giup|ho tro) (?:em|anh|chi|toi|minh|ban)|"
        r"(?:may|vai) (?:loi|viec|task)(?: [\w-]+){0,2} do",
        object_text,
    ):
        return False
    return any(word not in NON_OBJECT_WORDS for word in object_words)


def _without_discourse_prefix(text: str) -> str:
    return DISCOURSE_PREFIX_RE.sub("", text).strip()


def _participant_map(clauses_by_id: dict[str, Clause]) -> dict[str, str]:
    result: dict[str, str] = {}
    for clause in clauses_by_id.values():
        result.setdefault(normalize_for_match(clause.speaker_name), clause.speaker_name)
    return result


def _participant_occurrences(
    text: str,
    participants: dict[str, str],
) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    names = set(participants.values())
    for name in sorted(names, key=len, reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I):
            found.append((match.start(), match.end(), participants.get(normalize_for_match(name), name)))
    selected: list[tuple[int, int, str]] = []
    for item in sorted(found, key=lambda value: (value[0], -(value[1] - value[0]))):
        if selected and item[0] < selected[-1][1]:
            continue
        selected.append(item)
    return selected


def _actions_from_segment(segment: str) -> list[tuple[str, int]]:
    actions: list[tuple[str, int]] = []
    matches = list(ACTION_ANYWHERE_RE.finditer(segment))
    if not matches:
        return actions
    starts = [matches[0]]
    for match in matches[1:]:
        between = segment[starts[-1].end():match.start()]
        if re.search(r"\b(?:và|and|đồng thời|ngoài ra)\s*$", between, re.I):
            starts.append(match)
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(segment)
        raw_action = segment[match.start():end]
        raw_action = re.split(
            r"[,;]\s*(?:cố gắng|chưa|không có|báo|cập nhật tiến độ|kèm|"
            r"thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ nhật|\d{1,2}[/-]\d{1,2})\b",
            raw_action,
            maxsplit=1,
            flags=re.I,
        )[0]
        action = _clean_action(raw_action)
        if _is_concrete_action(action):
            actions.append((action, match.start()))
    return actions


def _mention_for_range(
    clause_id: str,
    mentions: dict[str, DateMention],
    start: int,
    end: int,
) -> str:
    matches = [
        item
        for item in mentions.values()
        if item.clause_id == clause_id
        and item.purpose == "DEADLINE"
        and start <= item.span_start < end
    ]
    return matches[0].date_mention_id if matches else ""


def _owner_from_definition_tail(
    tail: str,
    participants: dict[str, str],
) -> str:
    transfer = re.search(
        r"\b(?:chuyển|bàn\s+giao)\s+từ\s+" + WORD + r"\s+sang\s+(?P<name>" + WORD + r")",
        tail,
        re.I,
    )
    if transfer:
        return participants.get(
            normalize_for_match(transfer.group("name")),
            transfer.group("name"),
        )
    owner = re.search(
        r"\b(?:do|owner(?:\s+is)?|phụ\s+trách\s+bởi)\s+"
        r"(?P<name>" + WORD + r")(?:\s+phụ\s+trách)?",
        tail,
        re.I,
    )
    if owner:
        return participants.get(
            normalize_for_match(owner.group("name")),
            owner.group("name"),
        )
    owner = re.search(
        r"\b(?P<name>" + WORD + r")\s+(?:phụ\s+trách|chịu\s+trách\s+nhiệm)\b",
        tail,
        re.I,
    )
    if owner:
        return participants.get(
            normalize_for_match(owner.group("name")),
            owner.group("name"),
        )
    return ""


def _task_definition_registry(
    clauses: list[Clause],
    participants: dict[str, str],
) -> dict[str, dict[str, object]]:
    """Track the latest explicit definition for Task A/B/... labels."""

    registry: dict[str, dict[str, object]] = {}
    for clause in clauses:
        for match in TASK_DEFINITION_RE.finditer(clause.text_raw):
            label = match.group("label").upper()
            previous = registry.get(label, {})
            owner = _owner_from_definition_tail(match.group("tail"), participants)
            registry[label] = {
                "action": _clean_action(match.group("action")),
                "owner": owner or previous.get("owner", ""),
                "clause_id": clause.clause_id,
                "order_index": clause.order_index,
            }
        for match in TASK_RENAME_RE.finditer(clause.text_raw):
            label = match.group("label").upper()
            previous = registry.get(label, {})
            registry[label] = {
                "action": _clean_action(match.group("action")),
                "owner": previous.get("owner", ""),
                "clause_id": clause.clause_id,
                "order_index": clause.order_index,
            }
    return registry


def _add_segment_date_mention(
    segment: str,
    source_clause: Clause,
    mentions: dict[str, DateMention],
) -> str:
    """Parse a recap segment independently so its date cannot leak to a neighbor."""

    virtual = Clause(
        clause_id="RECAP-VIRTUAL",
        sentence_id=source_clause.sentence_id,
        speaker_id=source_clause.speaker_id,
        speaker_name=source_clause.speaker_name,
        start_ms=source_clause.start_ms,
        end_ms=source_clause.end_ms,
        text_raw=segment,
        text_normalized=normalize_text(segment),
        source_caption_ids=source_clause.source_caption_ids,
        order_index=source_clause.order_index,
    )
    parsed = [
        mention
        for mention in extract_date_mentions([virtual]).values()
        if mention.purpose == "DEADLINE"
    ]
    if not parsed:
        shorthand = re.search(
            r"\b(?:(?:hạn(?:\s+chót)?|deadline)\s+)?"
            r"(?:thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ\s+nhật|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
            r"(?:\s+(?:tuần\s+sau|tuần\s+tới|next\s+week))?\b",
            segment,
            re.I,
        )
        if shorthand:
            raw_text = shorthand.group(0)
            weekday_text = re.sub(
                r"^(?:hạn(?:\s+chót)?|deadline)\s+",
                "",
                raw_text,
                flags=re.I,
            )
            virtual.text_raw = f"deadline {weekday_text}"
            virtual.text_normalized = normalize_text(virtual.text_raw)
            parsed = [
                mention
                for mention in extract_date_mentions([virtual]).values()
                if mention.purpose == "DEADLINE"
            ]
            if parsed:
                parsed[-1].raw_text = raw_text
    if not parsed:
        return ""
    # A task bullet may mention history before the final deadline. The final
    # temporal expression in that bullet is authoritative.
    selected = parsed[-1]
    mention_id = make_id("DATE-RECAP", len(mentions) + 1)
    mentions[mention_id] = replace(
        selected,
        date_mention_id=mention_id,
        clause_id=source_clause.clause_id,
    )
    return mention_id


def _normalize_recap_action(value: str) -> str:
    action = _clean_action(value)
    if re.match(r"^monitoring\b", action, re.I):
        return _clean_action(f"Cấu hình {action}")
    if _is_concrete_action(action):
        return action
    normalized = normalize_for_match(action)
    if re.match(r"^(?:release notes?|release note)\b", normalized):
        return _clean_action(f"Viết {action}")
    if re.match(r"^(?:hướng dẫn|tai lieu|tài liệu)\b", action, re.I):
        deliverable = action[:1].lower() + action[1:]
        return _clean_action(f"Viết {deliverable}")
    if re.match(r"^(?:login|đăng nhập|dang nhap)\b", action, re.I):
        suffix = re.sub(r"^(?:login|đăng nhập|dang nhap)\b", "", action, flags=re.I)
        return _clean_action(f"Fix lỗi login {suffix}")
    if re.match(r"^(?:data|dữ liệu|du lieu)\s+test\b", action, re.I):
        suffix = re.sub(
            r"^(?:data|dữ liệu|du lieu)\s+test\b", "", action, flags=re.I
        )
        return _clean_action(f"Chuẩn bị data test {suffix}")
    if re.match(r"^(?:staging|môi trường|moi truong)\b", action, re.I):
        return _clean_action(f"Chuẩn bị {action}")
    if re.match(r"^release\s+plan\b", action, re.I):
        return _clean_action(f"Lập {action}")
    if re.match(r"^dashboard\b", action, re.I):
        return _clean_action(f"Cập nhật {action}")
    if re.match(r"^(?:sp365\s+display|display\s+sp365)\b", normalized):
        return "Hiển thị SP365"
    if re.match(r"^update\s+documentation\b", normalized):
        suffix = re.sub(r"^update\s+documentation\b", "", action, flags=re.I)
        return _clean_action(f"Cập nhật tài liệu {suffix}")
    if len(normalized.split()) >= 2:
        # Explicit recap rows may use deliverable names rather than verb
        # phrases. Preserve those names instead of discarding the task.
        return action
    return ""


def _split_structured_recap_actions(value: str) -> list[str]:
    pieces = re.split(
        r"(?<=\))\s*(?:,|và|and)\s*|"
        r",\s*(?=(?:backup|update|cập nhật|tài liệu|documentation|"
        r"fix|review|test|regression|monitoring|viết|soạn|chuẩn bị|"
        r"báo\s+tiến\s+độ)\b)",
        value,
        flags=re.I,
    )
    return [piece.strip(" ,") for piece in pieces if piece.strip(" ,")]


def _normalize_structured_owners(
    value: str,
    participants: dict[str, str],
) -> str:
    owners = []
    for raw in re.split(
        r"\s*(?:;|,|&|\bvà\b|\band\b)\s*",
        value,
        flags=re.I,
    ):
        raw = raw.strip()
        key = normalize_for_match(raw)
        bare = re.sub(
            r"^(?:anh|chị|chi|em|ms\.?|mr\.?|mrs\.?)\s+",
            "",
            raw,
            flags=re.I,
        ).strip()
        bare_key = normalize_for_match(bare)
        if not key:
            continue
        if key in participants:
            owners.append(participants[key])
        elif bare_key in participants:
            owners.append(participants[bare_key])
        elif key in ROLE_ASSIGNEES or (
            raw[:1].isupper()
            and key
            not in {
                "em", "anh", "chi", "toi", "ban", "task", "recap",
                "tong", "moi", "nguoi", "ticket", "cac",
            }
        ):
            owners.append(raw)
    return "; ".join(dict.fromkeys(owners))


def _recap_chunks(text: str) -> list[str]:
    value = re.sub(
        r"^.*?\b(?:chốt\s+lại|recap(?:\s+lại)?|tổng\s+kết(?:\s+lại)?|"
        r"thống\s+nhất(?:\s+lại)?|các\s+task\s+chính|các\s+đầu\s+việc|"
        r"toàn\s+bộ\s+công\s+việc|action\s+items?|"
        r"(?:tôi|anh|em|mình|i)\s+điểm\s+qua)\s*:\s*",
        "",
        text,
        count=1,
        flags=re.I,
    )
    value = re.sub(r"(?:^|\s)-\s+(?=\S)", " ||| ", value)
    value = re.sub(
        r"\b(?:(?:đầu\s+tiên|thứ\s+(?:nhất|hai|ba|tư|năm|sáu|bảy|tám))"
        r"\s*[,.:]\s*|(?:một|hai|ba|bốn|năm|sáu)\s+là\s+)",
        " ||| ",
        value,
        flags=re.I,
    )
    return [
        item.strip(" .,:;-")
        for item in re.split(r"\|\|\||;", value)
        if item.strip(" .,:;-")
    ]


def _structured_recap_item(
    item: str,
    participants: dict[str, str],
) -> tuple[str, str, str] | None:
    if re.search(
        r"^(?:(?:các|những)\s+(?:task|phần|việc)\s+khác\b|"
        r"all\s+other\s+tasks?\b)",
        item.strip(),
        re.I,
    ):
        return None
    person = r"(?:(?:anh|chị|chi|ms\.?|mr\.?|mrs\.?)\s+)?" + WORD
    owner_pattern = person + r"(?:\s+(?:và|and|&|;)\s+" + person + r")*"
    patterns = [
        re.compile(
            r"^(?:task\s+)?(?P<action>.+?)\s*:\s*"
            rf"(?P<owner>{owner_pattern})(?P<tail>\s*,?.*)$",
            re.I,
        ),
        re.compile(
            rf"^(?P<owner>{owner_pattern})\s*(?:-|–|:)\s*"
            r"(?P<action>.+?)(?P<tail>\s*)$",
            re.I,
        ),
        re.compile(
            r"^(?P<action>.+?)\s*(?:-|–)\s*"
            rf"(?P<owner>{owner_pattern})\s*(?:-|–)\s*(?P<tail>.*)$",
            re.I,
        ),
        re.compile(
            r"^(?P<action>.+?)\s*(?:-|–)\s*"
            rf"(?P<owner>{owner_pattern})(?P<tail>\s*,.*)$",
            re.I,
        ),
        re.compile(
            rf"^(?P<owner>{owner_pattern})\s+(?:có\s+\w+\s+task\s*:\s*)?"
            r"(?P<action>.+?)(?P<tail>\s*)$",
            re.I,
        ),
    ]
    for pattern in patterns:
        match = pattern.match(item)
        if not match:
            continue
        action = re.sub(r"^task\s+", "", match.group("action"), flags=re.I)
        action = re.sub(
            r"^(?:một|hai|ba|bốn|năm|sáu)\s+là\s+",
            "",
            action,
            flags=re.I,
        )
        normalized = _normalize_recap_action(action)
        owner = match.group("owner").strip()
        action_key = normalize_for_match(normalized)
        normalized_owner = _normalize_structured_owners(owner, participants)
        if (
            normalized
            and action_key
            and not re.search(
                r"\b(?:recap|tong ket|task chinh|dau viec)\b",
                action_key,
            )
            and normalized_owner
        ):
            return normalized_owner, action, match.group("tail")
    return None


def _extract_generic_structured_recap_events(
    clauses: list[Clause],
    mentions: dict[str, DateMention],
    participants: dict[str, str],
    start_sequence: int,
) -> list[TaskEvent]:
    candidates: list[list[TaskEvent]] = []
    minimum_order = int(len(clauses) * 0.45)
    for index, marker in enumerate(clauses):
        if (
            marker.order_index < minimum_order
            or not RECAP_MARKER_RE.search(marker.text_raw)
        ):
            continue
        block = [marker]
        for clause in clauses[index + 1:index + 7]:
            if clause.speaker_id != marker.speaker_id:
                break
            block.append(clause)
        text = " ".join(clause.text_raw for clause in block)
        block_events: list[TaskEvent] = []
        for item in _recap_chunks(text):
            item = re.sub(
                r"^(?:tôi|em|i)\b",
                marker.speaker_name,
                item,
                count=1,
                flags=re.I,
            )
            parsed = _structured_recap_item(item, participants)
            if not parsed:
                continue
            owner, raw_action, tail = parsed
            action_pieces = _split_structured_recap_actions(raw_action)
            for piece in action_pieces:
                action = _normalize_recap_action(piece)
                if (
                    not action
                    or re.match(
                        r"^(?:báo|cập nhật)\s+tiến\s+độ\b",
                        action,
                        re.I,
                    )
                ):
                    continue
                date_source = f"{piece} {tail}"
                block_events.append(
                    TaskEvent(
                        make_id(
                            "EVENT",
                            start_sequence + len(block_events) + 1,
                        ),
                        "OWNER_ASSIGN",
                        [marker.clause_id],
                        action,
                        owner,
                        _add_segment_date_mention(date_source, marker, mentions),
                        confidence=0.97,
                        extraction_source="RULE_FINAL_RECAP",
                        order_index=block[-1].order_index,
                    )
                )
        if len(block_events) >= 2:
            candidates.append(block_events)
    if not candidates:
        return []
    return max(
        candidates,
        key=lambda items: (len(items), items[-1].order_index),
    )


SHORT_RECAP_ACK_RE = re.compile(
    r"^(?:vâng|dạ|ok|okay|đồng ý|rõ|chính xác|anh nhắc đúng|"
    r"em không thấy thiếu|không có duplicate)[.!]?$",
    re.I,
)
NUMBERED_RECAP_ROW_RE = re.compile(
    r"^(?:và\s+)?task\s+(?P<label>[A-Z0-9]+)\s*(?:-|–|:)\s*"
    r"(?P<body>.+)$",
    re.I,
)
UNNUMBERED_RECAP_ROW_RE = re.compile(
    r"^(?:và\s+)?task\s+(?P<action>.+?)(?:\s+mới)?\s*,\s*"
    r"(?:owner|do)\s+(?P<owner>" + WORD + r")(?P<tail>.*)$",
    re.I,
)


def _latest_final_recap_block(clauses: list[Clause]) -> list[Clause]:
    """Collect the latest recap, including rows after short acknowledgements."""

    markers = [
        index
        for index, clause in enumerate(clauses)
        if FINAL_RECAP_MARKER_RE.search(clause.text_raw)
    ]
    if not markers:
        return []
    start = markers[-1]
    marker = clauses[start]
    block = [marker]
    saw_recap_row = False
    # Long transcripts often put one acknowledgement between "recap" and the
    # actual rows. Keep scanning the recap owner's clauses, but stop as soon as
    # another participant starts a substantive discussion.
    for clause in clauses[start + 1:start + 35]:
        if clause.speaker_id == marker.speaker_id:
            block.append(clause)
            if (
                NUMBERED_RECAP_ROW_RE.match(clause.text_raw.strip())
                or re.match(r"^(?:" + WORD + r")\s*:", clause.text_raw.strip())
                or re.match(r"^(?:anh|tôi|em|i)\s+sẽ\b", clause.text_raw.strip(), re.I)
            ):
                saw_recap_row = True
            continue
        if SHORT_RECAP_ACK_RE.match(clause.text_raw.strip()):
            continue
        if saw_recap_row:
            break
        # Permit one short bridge before the list starts.
        if clause.order_index - marker.order_index <= 3:
            continue
        break
    return block


def _recap_owner(value: str, participants: dict[str, str]) -> str:
    return _normalize_structured_owners(value.strip(" ,.:;"), participants)


def _expand_bare_recap_action(
    action: str,
    clauses: list[Clause],
    before_order: int,
) -> str:
    """Recover an object for a recap verb such as "giám sát từ thứ Năm"."""

    cleaned = _clean_action(action)
    if _is_concrete_action(cleaned):
        return cleaned
    normalized = normalize_for_match(cleaned)
    if not normalized:
        return ""
    verb = max(
        (
            normalize_for_match(item)
            for item in ACTION_VERBS
            if normalized == normalize_for_match(item)
            or normalized.startswith(normalize_for_match(item) + " ")
        ),
        key=len,
        default=normalized.split()[0],
    )
    temporal = r"(?:tu|den|truoc|sau|trong|khong|chua|deadline|han)\b"
    pattern = re.compile(
        rf"\b(?P<phrase>{re.escape(verb)}\s+(?!{temporal})"
        rf"[^,.;!?]{{2,60}})",
        re.I,
    )
    candidates: list[tuple[int, int, str]] = []
    for clause in reversed(
        [item for item in clauses if item.order_index < before_order]
    ):
        match = pattern.search(normalize_for_match(clause.text_raw))
        if not match:
            continue
        object_text = match.group("phrase")[len(verb):].strip()
        if re.search(
            r"\b(?:deadline|han|khong|chua|tu|den|truoc|sau)\b",
            object_text,
        ):
            continue
        candidate = _clean_action(f"{cleaned} {object_text}")
        if _is_concrete_action(candidate):
            candidates.append(
                (
                    len(normalize_for_match(candidate).split()),
                    -clause.order_index,
                    candidate,
                )
            )
    return min(candidates)[2] if candidates else ""


def _preserve_handoff_qualifier(action: str, raw_action: str) -> str:
    """Keep a shift boundary when a recap uses only a bare action verb."""

    cleaned_raw = _clean_action(raw_action)
    normalized_raw = normalize_for_match(cleaned_raw)
    bare_verbs = {normalize_for_match(item) for item in ACTION_VERBS}
    if normalized_raw not in bare_verbs:
        return action
    qualifier = re.search(
        r"\b(?P<value>"
        r"(?:từ|from)\s+(?:thứ\s+[\wÀ-ỹ]+|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"|(?:đến(?:\s+hết)?|until|through)\s+"
        r"(?:thứ\s+[\wÀ-ỹ]+|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r")\b",
        raw_action,
        re.I,
    )
    if not qualifier:
        return action
    return f"{action} {qualifier.group('value').strip()}"


def _extract_numbered_snapshot_events(
    block: list[Clause],
    clauses: list[Clause],
    mentions: dict[str, DateMention],
    participants: dict[str, str],
    registry: dict[str, dict[str, object]],
    start_sequence: int,
) -> list[TaskEvent]:
    """Parse final rows such as `Task 2 - Lan, report - deadline 10/03`."""

    events: list[TaskEvent] = []
    for clause in block[1:]:
        text = clause.text_raw.strip()
        match = NUMBERED_RECAP_ROW_RE.match(text)
        if match:
            label = match.group("label").upper()
            body = match.group("body").strip()
            if re.search(r"\b(?:đã\s+)?(?:hủy|cancel(?:led)?)\b", body, re.I):
                continue
            definition = registry.get(label, {})
            owner_match = re.match(r"(?P<owner>" + WORD + r")\s*,", body)
            owner = (
                _recap_owner(owner_match.group("owner"), participants)
                if owner_match
                else ""
            )
            if not owner:
                explicit_owner = re.search(
                    r"\b(?:owner|do)\s+(?P<owner>" + WORD + r")\b",
                    body,
                    re.I,
                )
                if explicit_owner:
                    owner = _recap_owner(
                        explicit_owner.group("owner"),
                        participants,
                    )
            owner = owner or str(definition.get("owner", ""))
            action = str(definition.get("action", ""))
            if not action:
                action_source = re.split(
                    r"\s+(?:-|–)\s+(?:đang|chờ|đã|deadline|hạn)\b|"
                    r",\s*(?:đang|chờ|đã|deadline|hạn)\b",
                    body,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                if owner_match:
                    action_source = action_source[owner_match.end():]
                action = _normalize_recap_action(action_source)
            if (
                re.search(r"\b(?:đang\s+)?ẩn\s+danh\b", body, re.I)
                and re.search(r"\b(?:dữ\s+liệu|data)\s+test\b", action, re.I)
            ):
                action = "Ẩn danh dữ liệu test"
            if not owner or not action:
                continue
            events.append(
                TaskEvent(
                    make_id("EVENT", start_sequence + len(events) + 1),
                    "OWNER_ASSIGN",
                    [clause.clause_id],
                    action,
                    owner,
                    _add_segment_date_mention(body, clause, mentions),
                    related_task_id=f"RECAP-{label}",
                    confidence=1.0,
                    extraction_source="RULE_FINAL_RECAP",
                    order_index=clause.order_index,
                )
            )
            continue

        unnumbered = UNNUMBERED_RECAP_ROW_RE.match(text)
        if not unnumbered:
            continue
        owner = _recap_owner(unnumbered.group("owner"), participants)
        action = _normalize_recap_action(unnumbered.group("action"))
        if not owner or not action:
            continue
        date_source = f"{unnumbered.group('action')} {unnumbered.group('tail')}"
        deadline_mention_id = _add_segment_date_mention(
            date_source,
            clause,
            mentions,
        ) or _add_prior_dependency_mention(
            action,
            clauses,
            clause.order_index,
            mentions,
        )
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                "OWNER_ASSIGN",
                [clause.clause_id],
                action,
                owner,
                deadline_mention_id,
                related_task_id=f"RECAP-{normalize_for_match(action)}",
                confidence=1.0,
                extraction_source="RULE_FINAL_RECAP",
                order_index=clause.order_index,
            )
        )
    return events


def _add_prior_dependency_mention(
    action: str,
    clauses: list[Clause],
    before_order: int,
    mentions: dict[str, DateMention],
) -> str:
    """Preserve a non-calendar dependency stated before the final recap."""

    # A single dependency cannot safely describe a recap row that combines
    # multiple actions, for example "gửi log, chuẩn bị test case". In that
    # situation the dependency may apply to only one half of the row.
    if re.search(r"[,;]", action):
        return ""
    action_tokens = {
        token
        for token in normalize_for_match(action).split()
        if len(token) >= 4
        and token
        not in {
            "cau", "hinh", "cap", "nhat", "thuc", "hien", "hoan", "thanh",
            "configure", "update", "complete",
        }
    }
    if not action_tokens:
        return ""
    dependency_re = re.compile(
        r"\b(?P<dependency>(?:trước|before)\s+(?!(?:ngày|thứ|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)"
        r"[^,.;]+|(?:sau\s+khi|after)\s+[^,.;]+)",
        re.I,
    )
    for clause in reversed(
        [item for item in clauses if item.order_index < before_order]
    ):
        normalized = normalize_for_match(clause.text_raw)
        if not action_tokens.intersection(normalized.split()):
            continue
        match = dependency_re.search(clause.text_raw)
        if not match:
            continue
        raw_text = match.group("dependency").strip()
        mention_id = make_id("DATE-RECAP", len(mentions) + 1)
        mentions[mention_id] = DateMention(
            mention_id,
            clause.clause_id,
            raw_text,
            "AFTER_EVENT",
            "EVENT_DEPENDENT",
            span_start=match.start("dependency"),
            span_end=match.end("dependency"),
        )
        return mention_id
    return ""


def _extract_owner_section_snapshot_events(
    block: list[Clause],
    clauses: list[Clause],
    mentions: dict[str, DateMention],
    participants: dict[str, str],
    start_sequence: int,
) -> list[TaskEvent]:
    """Parse a recap where one owner heading applies to following rows."""

    events: list[TaskEvent] = []
    current_owner = ""
    marker = block[0]
    for clause in block[1:]:
        text = clause.text_raw.strip(" ;")
        if not text or text.endswith("?"):
            continue
        owner_match = re.match(
            r"^(?P<owner>" + WORD + r")\s*:\s*(?P<action>.+)$",
            text,
            re.I,
        )
        if owner_match:
            current_owner = _recap_owner(
                owner_match.group("owner"),
                participants,
            )
            raw_action = owner_match.group("action")
        else:
            first_person = re.match(
                r"^(?:anh|tôi|em|i)\s+sẽ\s+(?P<action>.+)$",
                text,
                re.I,
            )
            if first_person:
                current_owner = marker.speaker_name
                raw_action = first_person.group("action")
            elif current_owner:
                raw_action = text
            else:
                continue
        if re.search(
            r"\b(?:chưa\s+chốt|không\s+(?:phải|có)\s+task|"
            r"distractor|bỏ\s+qua|đã\s+hủy)\b",
            raw_action,
            re.I,
        ):
            continue
        action = _expand_bare_recap_action(
            raw_action,
            clauses,
            clause.order_index,
        )
        action = _preserve_handoff_qualifier(action, raw_action)
        if not action or not current_owner:
            continue
        deadline_mention_id = (
            ""
            if re.search(r"\b(?:từ|from)\b", raw_action, re.I)
            else _add_segment_date_mention(raw_action, clause, mentions)
        )
        if not deadline_mention_id and not re.search(
            r"\b(?:từ|from)\b",
            raw_action,
            re.I,
        ):
            deadline_mention_id = _add_prior_dependency_mention(
                action,
                clauses,
                clause.order_index,
                mentions,
            )
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                "OWNER_ASSIGN",
                [clause.clause_id],
                action,
                current_owner,
                deadline_mention_id,
                confidence=1.0,
                extraction_source="RULE_FINAL_RECAP",
                order_index=clause.order_index,
            )
        )
    return events


def _extract_final_recap_events(
    clauses: list[Clause],
    mentions: dict[str, DateMention],
    participants: dict[str, str],
    start_sequence: int,
) -> list[TaskEvent]:
    block = _latest_final_recap_block(clauses)
    if not block:
        return []
    text = " ".join(clause.text_raw for clause in block)
    registry = _task_definition_registry(clauses, participants)
    events: list[TaskEvent] = []

    numbered_events = _extract_numbered_snapshot_events(
        block,
        clauses,
        mentions,
        participants,
        registry,
        start_sequence,
    )
    if len(numbered_events) >= 2:
        return numbered_events

    inline_matches = list(INLINE_FINAL_TASK_RE.finditer(text))
    for match in inline_matches:
        action = _normalize_recap_action(match.group("action"))
        body = match.group("body").strip(" .;,")
        if not action:
            continue
        if re.search(r"\b(?:đã\s+hủy|hủy|cancelled|rejected)\b", body, re.I):
            events.append(
                TaskEvent(
                    make_id("EVENT", start_sequence + len(events) + 1),
                    "TASK_CANCEL",
                    [block[0].clause_id],
                    related_task_hint=action,
                    confidence=0.99,
                    extraction_source="RULE_FINAL_RECAP",
                    order_index=block[-1].order_index,
                )
            )
            continue
        owner_match = re.search(
            r"\bowner\s+(?P<owner>.+?)(?=\s*,\s*(?:deadline|dl|reviewer)\b|$)",
            body,
            re.I,
        )
        owner = (
            _normalize_structured_owners(owner_match.group("owner"), participants)
            if owner_match
            else ""
        )
        if not owner:
            continue
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                "OWNER_ASSIGN",
                [block[0].clause_id],
                action,
                owner,
                _add_segment_date_mention(body, block[0], mentions),
                confidence=0.99,
                extraction_source="RULE_FINAL_RECAP",
                order_index=block[-1].order_index,
            )
        )
    if len(events) >= 2:
        return events

    compact_matches = list(COMPACT_TASK_RE.finditer(text))
    for match in compact_matches:
        definition = registry.get(match.group("label").upper())
        if not definition:
            continue
        body = match.group("body")
        owner_token = body.split(",", 1)[0].strip()
        owner = _normalize_structured_owners(owner_token, participants)
        action = str(definition["action"])
        if (
            len(normalize_for_match(action).split()) < 2
            or not owner
        ):
            continue
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                "OWNER_ASSIGN",
                [block[0].clause_id],
                action,
                owner,
                _add_segment_date_mention(body, block[0], mentions),
                confidence=0.99,
                extraction_source="RULE_FINAL_RECAP",
                order_index=block[-1].order_index,
            )
        )

    if events:
        return events

    for match in RECAP_BULLET_RE.finditer(text):
        segment = match.group("body").strip(" .")
        owner_match = re.match(r"(?P<owner>" + WORD + r")\s*:\s*(?P<action>.+)$", segment, re.I)
        if not owner_match:
            continue
        owner_token = owner_match.group("owner")
        owner = participants.get(normalize_for_match(owner_token), "")
        if not owner:
            continue
        action_text = re.split(
            r"\s*,\s*(?:dl|deadline|hạn|không deadline|k dl|chưa có deadline)\b",
            owner_match.group("action"),
            maxsplit=1,
            flags=re.I,
        )[0]
        action = _normalize_recap_action(action_text)
        if not action:
            continue
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                "OWNER_ASSIGN",
                [block[0].clause_id],
                action,
                owner,
                _add_segment_date_mention(segment, block[0], mentions),
                confidence=0.99,
                extraction_source="RULE_FINAL_RECAP",
                order_index=block[-1].order_index,
            )
        )
    if len(events) >= 2:
        return events

    return _extract_owner_section_snapshot_events(
        block,
        clauses,
        mentions,
        participants,
        start_sequence,
    )


def extract_recap_events(
    clauses: list[Clause],
    mentions: dict[str, DateMention],
    start_sequence: int = 0,
) -> list[TaskEvent]:
    """Extract final owner-action pairs from explicit recap clauses."""

    clauses_by_id = {clause.clause_id: clause for clause in clauses}
    participants = _participant_map(clauses_by_id)
    final_events = _extract_final_recap_events(
        clauses,
        mentions,
        participants,
        start_sequence,
    )
    if len(final_events) >= 2:
        return final_events
    structured_events = _extract_generic_structured_recap_events(
        clauses,
        mentions,
        participants,
        start_sequence,
    )
    if len(structured_events) >= 2:
        return structured_events
    events: list[TaskEvent] = []
    recap_speaker_id = ""
    recap_remaining = 0
    for clause in clauses:
        has_marker = bool(RECAP_MARKER_RE.search(clause.text_raw))
        if has_marker:
            recap_speaker_id = clause.speaker_id
            recap_remaining = 3
        elif recap_remaining and clause.speaker_id == recap_speaker_id:
            recap_remaining -= 1
        else:
            recap_speaker_id = ""
            recap_remaining = 0
            continue
        occurrences = _participant_occurrences(clause.text_raw, participants)
        action_before_owner: set[tuple[int, str]] = set()
        for owner_start, owner_end, owner in occurrences:
            suffix = clause.text_raw[owner_end:]
            if not re.match(
                r"\s*(?:đang làm|phụ trách|đảm nhận|chịu trách nhiệm|"
                r"đang xử lý|owns?|is responsible)",
                suffix,
                re.I,
            ):
                continue
            prefix = clause.text_raw[:owner_start].rstrip(" ,;:-")
            boundary = max(prefix.rfind(";"), prefix.rfind(":"))
            candidate = prefix[boundary + 1:].strip()
            match = ACTION_ANYWHERE_RE.search(candidate)
            if not match:
                continue
            action = _clean_action(candidate[match.start():])
            if not _is_concrete_action(action):
                continue
            absolute_start = boundary + 1 + match.start()
            events.append(
                TaskEvent(
                    make_id("EVENT", start_sequence + len(events) + 1),
                    "OWNER_ASSIGN",
                    [clause.clause_id],
                    action,
                    owner,
                    _mention_for_range(
                        clause.clause_id,
                        mentions,
                        absolute_start,
                        len(clause.text_raw),
                    ),
                    confidence=0.95,
                    extraction_source="RULE_RECAP",
                    order_index=clause.order_index,
                )
            )
            action_before_owner.add((owner_start, owner))
        index = 0
        while index < len(occurrences):
            start, name_end, assignee = occurrences[index]
            combined = [assignee]
            while index + 1 < len(occurrences):
                next_start, next_end, next_name = occurrences[index + 1]
                connector = clause.text_raw[name_end:next_start]
                if not re.fullmatch(r"\s*(?:và|and|&)\s*", connector, re.I):
                    break
                combined.append(next_name)
                index += 1
                name_end = next_end
            segment_end = occurrences[index + 1][0] if index + 1 < len(occurrences) else len(clause.text_raw)
            segment = clause.text_raw[name_end:segment_end]
            if (start, assignee) in action_before_owner:
                index += 1
                continue
            if not re.search(r"\b(?:không còn|hủy|bỏ|dừng|cancel)\b", segment, re.I):
                for action, relative_start in _actions_from_segment(segment):
                    absolute_start = name_end + relative_start
                    mention_id = _mention_for_range(
                        clause.clause_id,
                        mentions,
                        absolute_start,
                        segment_end,
                    )
                    events.append(
                        TaskEvent(
                            make_id("EVENT", start_sequence + len(events) + 1),
                            "OWNER_ASSIGN",
                            [clause.clause_id],
                            action,
                            "; ".join(combined),
                            mention_id,
                            confidence=0.95,
                            extraction_source="RULE_RECAP",
                            order_index=clause.order_index,
                        )
                    )
                for companion in re.finditer(
                    r"\bkèm\s+(?P<object>tài liệu(?:\s+hướng dẫn)?[^,;.]*)",
                    segment,
                    re.I,
                ):
                    action = _clean_action(f"Soạn {companion.group('object')}")
                    if _is_concrete_action(action):
                        events.append(
                            TaskEvent(
                                make_id("EVENT", start_sequence + len(events) + 1),
                                "OWNER_ASSIGN",
                                [clause.clause_id],
                                action,
                                "; ".join(combined),
                                _mention_for_range(
                                    clause.clause_id,
                                    mentions,
                                    name_end + companion.start(),
                                    segment_end,
                                ),
                                confidence=0.95,
                                extraction_source="RULE_RECAP",
                                order_index=clause.order_index,
                            )
                        )
            index += 1
    return events


def _topic_from_progress_question(text: str) -> str:
    active_problem = re.search(
        r"\b(?P<object>(?:parser|module|api|config|dashboard|dataset|"
        r"pipeline|script|spec|ui|tài\s+liệu|báo\s+cáo)"
        r"[^,.!?;]{0,60}?)\s+(?:hiện\s+tại\s+)?(?:vẫn\s+)?chưa\s+"
        r"(?P<verb>xử\s+lý|hoàn\s+thiện|cập\s+nhật|sửa|fix|process|"
        r"complete|update)\b",
        text,
        re.I,
    )
    if active_problem:
        action = _clean_action(
            f"{active_problem.group('verb')} {active_problem.group('object')}"
        )
        if _is_concrete_action(action):
            return action
    patterns = [
        re.compile(
            r"\b(?:phần|việc)\s+(?P<topic>.+?)\s+"
            r"(?:của\s+(?:em|anh|chị)\s+)?"
            r"(?:thế\s+nào|sao\s+rồi|làm\s+đến\s+đâu|tiến\s+độ)",
            re.I,
        ),
        re.compile(
            r"\b(?P<topic>(?:tài\s+liệu|module|báo\s+cáo|kế\s+hoạch)"
            r"[^?.!,;]{2,80}?)\s+"
            r"(?:của\s+(?:em|anh|chị)\s+)?"
            r"(?:thế\s+nào|sao\s+rồi|làm\s+đến\s+đâu)",
            re.I,
        ),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        topic = match.group("topic").strip(" .,:;")
        topic = re.sub(r"^(?:phần|việc)\s+", "", topic, flags=re.I)
        topic = re.sub(r"\s+(?:em|anh|chị|bạn)$", "", topic, flags=re.I)
        if normalize_for_match(topic).startswith("module "):
            topic = f"tài liệu {topic}"
        return topic
    task_introduction = re.search(
        r"\btask\b[^:]{0,90}:\s*(?P<topic>[^.?!]{3,120})",
        text,
        re.I,
    )
    if task_introduction:
        topic = _clean_action(task_introduction.group("topic"))
        if _is_concrete_action(topic):
            return topic
    requested_action = re.search(
        r"\b(?:muốn|cần)\s+chúng\s+ta\s+"
        r"(?P<topic>(?:" + "|".join(
            re.escape(item) for item in sorted(ACTION_VERBS, key=len, reverse=True)
        ) + r")\b[^.?!]{2,120})",
        text,
        re.I,
    )
    if requested_action:
        topic = _clean_action(requested_action.group("topic"))
        if _is_concrete_action(topic):
            return topic
    transition = re.search(
        r"\b(?:chuyển\s+sang|tiếp\s+theo\s+là|còn)\s+"
        r"(?P<topic>(?:phần\s+)?(?:module|tài\s+liệu|báo\s+cáo)"
        r"[^?.!,;]{1,60})",
        text,
        re.I,
    )
    if transition:
        topic = transition.group("topic").strip(" .,:;")
        topic = re.sub(r"^phần\s+", "", topic, flags=re.I)
        if normalize_for_match(topic).startswith("module "):
            topic = f"tài liệu {topic}"
        return topic
    return ""


def extract_contextual_commitment_events(
    clauses: list[Clause],
    mentions: dict[str, DateMention],
    start_sequence: int = 0,
) -> list[TaskEvent]:
    """Resolve vague commitments against the latest per-person work topic."""

    participants = _participant_map({clause.clause_id: clause for clause in clauses})
    topics: dict[str, str] = {}
    latest_topic = ""
    latest_topic_order = -100
    last_addressed_owner = ""
    last_addressed_order = -100
    preferred_deadlines: dict[str, str] = {}
    events: list[TaskEvent] = []
    for clause_index, clause in enumerate(clauses):
        text = clause.text_raw.strip()
        discovered = _topic_from_progress_question(text)
        if discovered:
            latest_topic = discovered
            latest_topic_order = clause.order_index

        addressed = VOCATIVE_RE.match(text)
        if addressed:
            owner = _canonical_assignee(addressed.group("name"), participants)
            if owner:
                last_addressed_owner = owner
                last_addressed_order = clause.order_index
                direct_topic = _topic_from_progress_question(
                    addressed.group("body")
                )
                if direct_topic:
                    topics[normalize_for_match(owner)] = direct_topic
                    latest_topic = direct_topic
                elif latest_topic and re.search(
                    r"\b(?:làm\s+đến\s+đâu|sao\s+rồi|tiến\s+độ)\b",
                    addressed.group("body"),
                    re.I,
                ):
                    topics[normalize_for_match(owner)] = latest_topic

        speaker_key = normalize_for_match(clause.speaker_name)
        mention_id = _mention_for_clause(clause.clause_id, mentions)
        named_deadline = re.search(
            r"\btask\s+(?P<label>[\wÀ-ỹ'-]+(?:\s+[\wÀ-ỹ'-]+){0,8}?)\s+"
            r"(?:vẫn\s+(?:giữ\s+)?)?(?:có\s+)?deadline\b",
            text,
            re.I,
        )
        if (
            named_deadline
            and mention_id
            and "?" not in text
            and not re.search(
                r"\b(?:chưa\s+chốt|không\s+phải|có\s+thể|maybe|might)\b",
                text,
                re.I,
            )
        ):
            events.append(
                TaskEvent(
                    make_id("EVENT", start_sequence + len(events) + 1),
                    "DEADLINE_SET",
                    [clause.clause_id],
                    related_task_hint=named_deadline.group("label").strip(),
                    deadline_mention_id=mention_id,
                    confidence=0.94,
                    extraction_source="RULE_CONTEXT",
                    order_index=clause.order_index,
                )
            )
        existing_task = re.search(
            r"^(?:dạ[,.\s]+|vâng[,.\s]+)?"
            r"(?:(?:em|tôi|mình)\s+)?(?:chỉ\s+)?còn\s+"
            r"(?:mỗi\s+)?việc\s+"
            r"(?P<action>.+?)(?:,\s*(?:dự\s+kiến|chắc|deadline|hạn)\b|$)",
            text,
            re.I,
        )
        if existing_task:
            existing_action = _clean_action(existing_task.group("action"))
            if _is_concrete_action(existing_action):
                if not mention_id and re.search(
                    r"\b(?:dự\s+kiến|chắc|deadline|hạn)\s+mai\b",
                    text,
                    re.I,
                ):
                    mention_id = make_id("DATE-CONTEXT", len(mentions) + 1)
                    mentions[mention_id] = DateMention(
                        mention_id,
                        clause.clause_id,
                        "mai",
                        "ON_DATE",
                        "RELATIVE_DAY",
                        relative_day_offset=1,
                    )
                events.append(
                    TaskEvent(
                        make_id("EVENT", start_sequence + len(events) + 1),
                        "TASK_COMMITMENT",
                        [clause.clause_id],
                        existing_action,
                        clause.speaker_name,
                        mention_id,
                        confidence=0.92,
                        extraction_source="RULE_PENDING_CONFIRMATION",
                        order_index=clause.order_index,
                    )
                )
        accepts_pending = re.search(
            r"^(?:dạ[,.\s]+|vâng[,.\s]+|ok[,.\s]+)?"
            r"(?:(?:để\s+)?em\s+làm|task\s+này\s+(?:em|tôi|mình)\s+nhận|"
            r"(?:em|tôi|mình)\s+"
            r"(?:có\s+thể\s+)?nhận(?:\s+task\s+này)?"
            r"(?!\s+(?:cả|các|những)\b)|"
            r"i(?:\s+can)?\s+take\s+(?:this|the\s+task))\b",
            text,
            re.I,
        )
        if (
            accepts_pending
            and latest_topic
            and 0 <= clause.order_index - latest_topic_order <= 30
            and _is_concrete_action(latest_topic)
        ):
            pending_action = latest_topic
            api_partner = re.match(
                r"^tích\s+hợp\s+API\s+(?P<object>.+?)\s+cho\s+bên\s+đối\s+tác$",
                pending_action,
                re.I,
            )
            if api_partner:
                pending_action = _clean_action(
                    f"Làm API {api_partner.group('object')}"
                )
            selected_mention_id = mention_id
            source_clause_ids = [clause.clause_id]
            if not selected_mention_id:
                for future in clauses[clause_index + 1:clause_index + 5]:
                    future_mention_id = _mention_for_clause(
                        future.clause_id, mentions
                    )
                    if (
                        future_mention_id
                        and not re.search(
                            r"\b(?:chắc|dự\s+kiến|có\s+thể|maybe|might)\b",
                            future.text_raw,
                            re.I,
                        )
                    ):
                        selected_mention_id = future_mention_id
                        source_clause_ids.append(future.clause_id)
                        break
            events.append(
                TaskEvent(
                    make_id("EVENT", start_sequence + len(events) + 1),
                    "TASK_COMMITMENT",
                    source_clause_ids,
                    pending_action,
                    clause.speaker_name,
                    selected_mention_id,
                    confidence=0.92,
                    extraction_source="RULE_PENDING_CONFIRMATION",
                    order_index=clause.order_index,
                )
            )
            latest_topic = ""
            latest_topic_order = -100
        addressed_key = normalize_for_match(last_addressed_owner)
        directive = re.search(
            r"\b(?:em|anh|chị|bạn|you)\b.*?"
            r"(?P<verb>gửi|hoàn\s+thành|hoàn\s+thiện|viết|soạn|"
            r"chuẩn\s+bị|cập\s+nhật|send|complete|finish|write|prepare)\b",
            text,
            re.I,
        )
        if (
            mention_id
            and directive
            and addressed_key
            and addressed_key != speaker_key
            and clause.order_index - last_addressed_order <= 6
            and addressed_key in topics
        ):
            action = _clean_action(
                f"{directive.group('verb')} {topics[addressed_key]}"
            )
            if _is_concrete_action(action):
                preferred_deadlines[addressed_key] = mention_id
                events.append(
                    TaskEvent(
                        make_id(
                            "EVENT",
                            start_sequence + len(events) + 1,
                        ),
                        "OWNER_ASSIGN",
                        [clause.clause_id],
                        action,
                        last_addressed_owner,
                        mention_id,
                        confidence=0.92,
                        extraction_source="RULE_CONTEXT",
                        order_index=clause.order_index,
                    )
                )

        topic = topics.get(speaker_key, "")
        if not topic:
            continue
        commitment = re.search(
            r"\b(?:em|tôi|mình|anh|chị|i)\b.*?"
            r"(?:sẽ|dự\s+kiến|cố\s+gắng|will|plan\s+to)\s+"
            r"(?P<verb>gửi|hoàn\s+thành|hoàn\s+thiện|viết|soạn|"
            r"chuẩn\s+bị|cập\s+nhật|send|complete|finish|write|prepare)\b",
            text,
            re.I,
        )
        if not commitment:
            continue
        verb = commitment.group("verb")
        action = _clean_action(f"{verb} {topic}")
        if not _is_concrete_action(action):
            continue
        selected_mention_id = mention_id
        preferred_id = preferred_deadlines.get(speaker_key, "")
        if preferred_id and (
            not selected_mention_id
            or (
                mentions[preferred_id].explicit_day
                == mentions[selected_mention_id].explicit_day
                and mentions[preferred_id].explicit_month
                == mentions[selected_mention_id].explicit_month
                and mentions[preferred_id].explicit_year
                and not mentions[selected_mention_id].explicit_year
            )
        ):
            selected_mention_id = preferred_id
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                "TASK_COMMITMENT",
                [clause.clause_id],
                action,
                clause.speaker_name,
                selected_mention_id,
                confidence=0.9,
                extraction_source="RULE_PENDING_CONFIRMATION",
                order_index=clause.order_index,
            )
        )
    return events


def _canonical_assignee(value: str, participants: dict[str, str]) -> str:
    raw = HONORIFIC_RE.sub("", value.strip(" ,.:;")).strip()
    key = normalize_for_match(raw)
    if key in participants:
        return participants[key]
    if not key or key in {normalize_for_match(item) for item in INVALID_ASSIGNEES}:
        return ""
    if key in ROLE_ASSIGNEES:
        return raw
    return ""


def _validated_ai_assignee(
    value: str,
    participants: dict[str, str],
    context_text: str,
) -> str:
    canonical = _canonical_assignee(value, participants)
    if canonical:
        return canonical
    raw = HONORIFIC_RE.sub("", value.strip(" ,.:;")).strip()
    key = normalize_for_match(raw)
    if (
        raw[:1].isupper()
        and re.search(rf"(?<!\w){re.escape(raw)}(?!\w)", context_text)
    ):
        return raw
    if not key or key in {normalize_for_match(item) for item in INVALID_ASSIGNEES}:
        return ""
    if re.search(rf"(?<!\w){re.escape(raw)}(?!\w)", context_text, re.I):
        return raw
    return ""


def _named_participant_assignment(
    text: str,
    participants: dict[str, str],
) -> tuple[str, str]:
    for key, display_name in sorted(participants.items(), key=lambda item: len(item[1]), reverse=True):
        patterns = [
            rf"^\s*{re.escape(display_name)}\b(?P<body>.+)$",
            rf"^\s*(?:em|anh|chị|bạn)\s+{re.escape(display_name)}\b(?P<body>.+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, text, re.I)
            if not match:
                continue
            body = re.sub(
                r"^[,\s]*(?:em|anh|chị|bạn)?\s*"
                r"(?:cần xác nhận việc|cần xác nhận|cần|hãy|nhớ|giúp|"
                r"sẽ là người|sẽ|phụ trách|chịu trách nhiệm|nhận|lo)?\s*",
                "",
                match.group("body"),
                flags=re.I,
            )
            action_match = ACTION_ANYWHERE_RE.search(body)
            if not action_match:
                continue
            action = _clean_action(body[action_match.start():])
            if _is_concrete_action(action):
                return display_name, action
    return "", ""


def _previous_other_speaker(
    clause: Clause,
    window: CandidateWindow,
    clauses_by_id: dict[str, Clause],
) -> str:
    context = sorted(
        (clauses_by_id[cid] for cid in window.context_clause_ids if cid in clauses_by_id),
        key=lambda item: item.order_index,
    )
    previous = [
        item for item in context
        if item.order_index < clause.order_index and item.speaker_id != clause.speaker_id
    ]
    return previous[-1].speaker_name if previous else ""


def _mention_for_clause(clause_id: str, mentions: dict[str, DateMention]) -> str:
    matches = [
        item
        for item in mentions.values()
        if item.clause_id == clause_id and item.purpose == "DEADLINE"
    ]
    return matches[-1].date_mention_id if matches else ""


def _assignment_from_clause(
    clause: Clause,
    window: CandidateWindow,
    clauses_by_id: dict[str, Clause],
    participants: dict[str, str],
) -> tuple[str, str]:
    text = clause.text_raw.strip()
    role_assignment = ROLE_DELIVERABLE_ASSIGN_RE.search(text)
    if role_assignment:
        assignee = _canonical_assignee(role_assignment.group("name"), participants)
        action = _clean_action(
            f"Hoàn thiện {role_assignment.group('deliverable').strip(' ,.;:')}"
        )
        if assignee and _is_concrete_action(action):
            return assignee, action
    match = EXPLICIT_ASSIGN_RE.search(text) or OWNER_RE.search(text)
    if match:
        assignee = _canonical_assignee(match.group("name"), participants)
        action = _clean_action(match.group("action"))
        if assignee and _is_concrete_action(action):
            return assignee, action

    assignee, action = _named_participant_assignment(text, participants)
    if assignee and action:
        return assignee, action

    vocative = VOCATIVE_RE.match(text)
    if vocative:
        assignee = _canonical_assignee(vocative.group("name"), participants)
        body = vocative.group("body")
        body = re.sub(
            r"^(?:anh|chị|tôi|mình)\s+(?:giao|nhờ)\s+(?:em|anh|chị|bạn)\s+",
            "",
            body,
            flags=re.I,
        )
        body = re.sub(
            r"^(?:em|anh|chị|bạn)\s+(?:hãy|cần|nhớ|giúp|phụ trách|lo)\s+",
            "",
            body,
            flags=re.I,
        )
        action = _clean_action(body)
        if assignee and _is_concrete_action(action):
            return assignee, action

    directive = SECOND_PERSON_DIRECTIVE_RE.match(text)
    if directive:
        action = _clean_action(directive.group("action"))
        assignee = _previous_other_speaker(clause, window, clauses_by_id)
        if assignee and _is_concrete_action(action):
            return assignee, action
    return "", ""


def _trusted_human_note_owner(
    value: str,
    participants: dict[str, str],
) -> str:
    raw_input = value.strip(" ,.:;-")
    raw_casefold = raw_input.casefold()
    normalized_input = normalize_for_match(raw_input)
    if raw_casefold in {
        "em", "chị", "chi", "bạn", "ban", "tôi", "toi", "mình", "chúng ta",
        "chung ta", "we", "i", "đúng", "tuyệt", "sau", "vậy", "rồi", "tốt",
        "task", "đầu tiên",
    } or (normalized_input == "anh" and normalized_input not in participants):
        return ""
    owner = _canonical_assignee(value, participants)
    if owner:
        return owner
    raw = HONORIFIC_RE.sub("", value.strip(" ,.:;-")).strip()
    key = normalize_for_match(raw)
    if (
        raw
        and key not in {normalize_for_match(item) for item in INVALID_ASSIGNEES}
        and (raw[:1].isupper() or key in ROLE_ASSIGNEES)
    ):
        return raw
    return ""


def _positive_human_note_fields(
    text: str,
    participants: dict[str, str],
) -> tuple[str, str]:
    """Extract one trusted positive assertion from a human-written note row."""

    if HUMAN_NOTE_UNCERTAIN_RE.search(text) or HUMAN_NOTE_NON_TASK_RE.search(text):
        return "", ""
    explicit_task = False
    action = ""
    owner = ""

    quoted = HUMAN_NOTE_QUOTED_TASK_RE.search(text)
    labeled = HUMAN_NOTE_LABELED_TASK_RE.search(text)
    assigned = HUMAN_NOTE_ASSIGN_RE.search(text)
    if quoted:
        explicit_task = True
        action = quoted.group("action")
    elif labeled:
        explicit_task = True
        action = labeled.group("action")
    elif assigned:
        owner = _trusted_human_note_owner(assigned.group("owner"), participants)
        action = assigned.group("action")
    else:
        prefixed = HUMAN_NOTE_OWNER_PREFIX_RE.match(text.strip())
        if prefixed:
            owner = _trusted_human_note_owner(prefixed.group("owner"), participants)
            action = prefixed.group("body")

    owner_field = HUMAN_NOTE_OWNER_FIELD_RE.search(text)
    owner_after_action = HUMAN_NOTE_ACTION_OWNER_RE.search(text)
    reassigned_owner = HUMAN_NOTE_REASSIGN_OWNER_RE.search(text)
    if reassigned_owner:
        owner = _trusted_human_note_owner(
            reassigned_owner.group("owner"), participants
        )
    elif owner_field:
        owner = _trusted_human_note_owner(owner_field.group("owner"), participants)
    elif owner_after_action:
        owner = _trusted_human_note_owner(
            owner_after_action.group("owner"), participants
        )

    action = re.sub(
        r"^(?:(?:em|anh|chị|bạn|you)\s+)?(?:sẽ|will)\s+",
        "",
        action.strip(),
        flags=re.I,
    )
    action = re.split(
        r"\s*(?:,|;|–|-)\s*(?:owner|do|assign(?:ed)?\s+(?:cho|to)|"
        r"deadline|dl|ddl|hạn)\b",
        action,
        maxsplit=1,
        flags=re.I,
    )[0]
    action = _clean_action(action)
    if not _is_concrete_action(action):
        return "", ""
    if not owner and not explicit_task:
        # A trusted note may add a task with no assignee only when the writer
        # explicitly called it a task/action item. Bare action fragments still
        # need an identifiable human owner.
        return "", ""
    return action, owner


def extract_events_from_human_note(
    note: MeetingNoteInput | None,
    meeting_context,
    clauses_by_id: dict[str, Clause],
    mentions: dict[str, DateMention],
    start_sequence: int = 0,
) -> tuple[list[TaskEvent], dict[str, Clause]]:
    """Create trusted positive events while retaining explicit note evidence."""

    if (
        note is None
        or note.source == "AUTO_OVERVIEW"
        or meeting_context is None
        or not note.content.strip()
    ):
        return [], {}
    participants = _participant_map(clauses_by_id)
    grounded_by_line = {
        hint.note_line_id: hint.clause_ids
        for hint in meeting_context.grounded_hints
        if hint.status in {"GROUNDED", "AMBIGUOUS"}
    }
    events: list[TaskEvent] = []
    note_clauses: dict[str, Clause] = {}
    author = note.author.strip() or (
        "Thư ký" if note.source == "SECRETARY" else "Meeting Note"
    )
    speaker = f"Meeting Note ({author})"
    for index, line in enumerate(meeting_context.note_lines):
        if line.kind.value not in {"ACTION_HINT", "STATE_HINT"}:
            continue
        if index + 1 < len(meeting_context.note_lines):
            next_text = meeting_context.note_lines[index + 1].raw_text
            if re.search(
                r"\b(?:việc\s+đó\s+)?không\s+tạo\s+task\s+mới\b|"
                r"\bfollow[- ]?up\s+thôi\b",
                next_text,
                re.I,
            ):
                continue
        action, owner = _positive_human_note_fields(line.raw_text, participants)
        if not action:
            continue
        clause_id = f"NOTE-CLAUSE-{index + 1:03d}"
        note_clause = Clause(
            clause_id=clause_id,
            sentence_id=f"NOTE-SENTENCE-{index + 1:03d}",
            speaker_id="MEETING-NOTE",
            speaker_name=speaker,
            start_ms=None,
            end_ms=None,
            text_raw=line.raw_text,
            text_normalized=normalize_text(line.raw_text),
            source_caption_ids=[],
            order_index=-1000 + index,
        )
        note_clauses[clause_id] = note_clause
        parsed_mentions = extract_date_mentions([note_clause])
        deadline_id = ""
        for mention_index, parsed in enumerate(parsed_mentions.values(), start=1):
            mention_id = (
                f"DATE-NOTE-{index + 1:03d}-{mention_index:02d}"
            )
            mentions[mention_id] = replace(
                parsed,
                date_mention_id=mention_id,
                clause_id=clause_id,
            )
            if parsed.purpose == "DEADLINE":
                deadline_id = mention_id
        source_ids = [clause_id, *grounded_by_line.get(line.line_id, ())]
        events.append(
            TaskEvent(
                event_id=make_id("EVENT", start_sequence + len(events) + 1),
                event_type="OWNER_ASSIGN" if owner else "TASK_CREATE",
                source_clause_ids=list(dict.fromkeys(source_ids)),
                action_text=action,
                assignee=owner,
                deadline_mention_id=deadline_id,
                confidence=0.98,
                extraction_source="HUMAN_NOTE",
                order_index=note_clause.order_index,
            )
        )
    return events, note_clauses


def extract_events_by_rule(
    window: CandidateWindow,
    clauses_by_id: dict[str, Clause],
    annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention],
    start_sequence: int = 0,
    note_cues_by_clause: dict[str, tuple[NoteCue, ...]] | None = None,
) -> list[TaskEvent]:
    events: list[TaskEvent] = []
    note_cues_by_clause = note_cues_by_clause or {}
    participants = _participant_map(clauses_by_id)
    for clause_id in window.primary_clause_ids:
        clause = clauses_by_id[clause_id]
        annotation = annotations[clause_id]
        event_type, action, assignee, hint = "", "", "", ""
        extraction_source = "RULE"
        event_confidence = annotation.rule_confidence
        blocks_deadline_only = False
        text = clause.text_raw.strip()

        existing_task = re.search(
            r"^(?:dạ[,.\s]+|vâng[,.\s]+)?"
            r"(?:(?:em|tôi|mình)\s+)?(?:chỉ\s+)?còn\s+"
            r"(?:mỗi\s+)?việc\s+"
            r"(?P<action>.+?)(?:,\s*(?:dự\s+kiến|chắc|deadline|hạn)\b|$)",
            text,
            re.I,
        )

        if existing_task and _is_concrete_action(_clean_action(existing_task.group("action"))):
            event_type = "TASK_COMMITMENT"
            action = _clean_action(existing_task.group("action"))
            assignee = clause.speaker_name
        elif "CANCELLATION" in annotation.flags:
            event_type = "TASK_CANCEL"
            hint = re.sub(
                r".*?(?:hủy task|bỏ task|dừng (?:phần|task|việc)|không làm nữa|"
                r"không cần làm nữa|"
                r"loại khỏi scope|cancel(?:led)?|remove(?:d)? from scope)\s*",
                "",
                clause.text_raw,
                flags=re.I,
            ).strip(" .")
        elif "REJECTION" in annotation.flags:
            event_type = "TASK_REJECT"
            assignee = clause.speaker_name
            hint = clause.text_raw
        elif "CORRECTION" in annotation.flags and _mention_for_clause(clause_id, mentions):
            event_type = "DEADLINE_REPLACE"
            hint = clause.text_raw
        elif (
            (named_deadline := re.search(
                r"\btask\s+(?P<label>[\wÀ-ỹ'-]+(?:\s+[\wÀ-ỹ'-]+){0,8}?)\s+"
                r"(?:vẫn\s+(?:giữ\s+)?)?(?:có\s+)?deadline\b",
                text,
                re.I,
            ))
            and _mention_for_clause(clause_id, mentions)
            and "?" not in text
        ):
            event_type = "DEADLINE_SET"
            hint = named_deadline.group("label").strip()
        elif not (annotation.flags & NEGATIVE_CREATION_FLAGS):
            commitment_text = _without_discourse_prefix(text)
            # A trailing question to an already explicit assignment is context,
            # while a question/suggestion that constitutes the clause itself is
            # not a CREATE. Uncertain date statements may still update a known
            # task through the deadline-only branch below.
            creation_scope = TRAILING_DIALOGUE_RE.sub("", text)
            creation_blocked = bool(UNCERTAIN_OR_QUESTION_RE.search(creation_scope))
            first_person = None if creation_blocked else FIRST_PERSON_RE.match(commitment_text)
            if first_person:
                action = _clean_action(first_person.group(1))
                is_followup = (
                    FOLLOWUP_COMMUNICATION_RE.match(action)
                    and re.search(r"\b(?:sau khi|khi xong|after|once done)\b", text, re.I)
                )
                blocks_deadline_only = bool(is_followup)
                if _is_concrete_action(action) and not is_followup:
                    event_type = "TASK_COMMITMENT"
                    assignee = clause.speaker_name
            if not event_type and not creation_blocked:
                fronted = OBJECT_FRONTED_COMMITMENT_RE.match(commitment_text)
                if fronted:
                    action_part = _clean_action(fronted.group("action"))
                    object_part = fronted.group("object").strip(" ,")
                    object_key = normalize_for_match(object_part)
                    action_key = normalize_for_match(action_part)
                    action = _clean_action(f"{action_part} {object_part}")
                    if (
                        object_key not in {"da", "vang", "ok", "okay"}
                        and ACTION_START_RE.match(action_part)
                        and action_key
                        not in {normalize_for_match(item) for item in VAGUE_ACTIONS}
                        and _is_concrete_action(action)
                    ):
                        event_type = "TASK_COMMITMENT"
                        assignee = clause.speaker_name
            if not event_type and not creation_blocked:
                assignee, action = _assignment_from_clause(
                    clause, window, clauses_by_id, participants
                )
                if action:
                    event_type = "OWNER_ASSIGN"
            if not event_type:
                local_action_cues = [
                    cue for cue in note_cues_by_clause.get(clause_id, ())
                    if cue.local_usable and cue.kind.value == "ACTION_HINT"
                ]
                note_blocked = bool(
                    annotation.flags
                    & {
                        "ROOT_QUESTION", "SUGGESTION_ONLY", "BRAINSTORM",
                        "HYPOTHETICAL", "PAST_COMPLETED", "FUTURE_DISCUSSION",
                        "ADMIN_FOLLOWUP", "CANCELLATION", "REJECTION",
                    }
                )
                if (
                    local_action_cues
                    and not note_blocked
                    and "PROGRESS_UPDATE" in annotation.flags
                    and "ACTION_VERB" in annotation.flags
                ):
                    # The note only makes this transcript progress sentence
                    # worth reconsidering. All emitted fields remain parsed
                    # from the transcript clause itself.
                    transcript_action = text
                    for mention in mentions.values():
                        if mention.clause_id == clause_id and mention.raw_text:
                            transcript_action = re.sub(
                                re.escape(mention.raw_text),
                                "",
                                transcript_action,
                                count=1,
                                flags=re.I,
                            )
                    transcript_action = re.sub(
                        r"[,;\s]+(?:xong|hoàn thành|done|finished)\s*[.!]*$",
                        "",
                        transcript_action,
                        flags=re.I,
                    )
                    action = _clean_action(
                        NOTE_PROGRESS_PREFIX_RE.sub("", transcript_action).strip()
                    )
                    assignee = _canonical_assignee(clause.speaker_name, participants)
                    if assignee and _is_concrete_action(action):
                        event_type = "TASK_COMMITMENT"
                        extraction_source = "RULE_NOTE_CUE"
                        event_confidence = min(
                            0.88,
                            max(annotation.rule_confidence, local_action_cues[0].grounding_score),
                        )
            if (
                not event_type
                and not blocks_deadline_only
                and not re.search(r"\bchắc\b", text, re.I)
                and "?" not in text
                and not GENERIC_DEADLINE_SUBJECT_RE.match(commitment_text)
                and _mention_for_clause(clause_id, mentions)
                and annotation.flags & {"FIRST_PERSON_COMMITMENT", "CONFIRMATION"}
            ):
                event_type = "DEADLINE_SET"
                assignee = clause.speaker_name
                hint = clause.text_raw

        if event_type:
            events.append(
                TaskEvent(
                    make_id("EVENT", start_sequence + len(events) + 1),
                    event_type,
                    [clause_id],
                    action,
                    assignee,
                    _mention_for_clause(clause_id, mentions),
                    related_task_hint=hint,
                    confidence=event_confidence,
                    extraction_source=extraction_source,
                    order_index=clause.order_index,
                )
            )
    return events


def extract_events_by_ai(
    window: CandidateWindow,
    clauses_by_id: dict[str, Clause],
    annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention],
    client: AiClient,
    start_sequence: int = 0,
    note_cues_by_clause: dict[str, tuple[NoteCue, ...]] | None = None,
    task_memory: list[dict] | None = None,
    contract_diagnostics: dict[str, int] | None = None,
) -> list[TaskEvent]:
    if not client.enabled:
        return []
    context_clauses = [clauses_by_id[cid] for cid in window.context_clause_ids]
    note_cues_by_clause = note_cues_by_clause or {}
    task_memory = task_memory or []
    contract_diagnostics = contract_diagnostics if contract_diagnostics is not None else {}

    def record_contract_rejection(
        *,
        category: str,
        reason: str,
    ) -> None:
        contract_diagnostics["rejection_count"] = (
            int(contract_diagnostics.get("rejection_count", 0)) + 1
        )
        category_key = f"{category}_rejection_count"
        contract_diagnostics[category_key] = (
            int(contract_diagnostics.get(category_key, 0)) + 1
        )
        reason_key = f"{reason}_rejection_count"
        contract_diagnostics[reason_key] = (
            int(contract_diagnostics.get(reason_key, 0)) + 1
        )
    focus_ids = set(window.primary_clause_ids)
    safety_flags = {
        "DIRECT_ASSIGNMENT", "FIRST_PERSON_COMMITMENT", "CONFIRMATION",
        "CORRECTION", "CANCELLATION", "REJECTION", "DATE_MENTION",
    }
    negative_flags = {
        "BRAINSTORM", "HYPOTHETICAL", "PAST_COMPLETED", "FUTURE_DISCUSSION",
    }
    digest_tokens: list[str] = []
    for clause in context_clauses:
        for token in re.findall(r"\w+", normalize_for_match(clause.text_raw)):
            if len(token) >= 4 and token not in NON_OBJECT_WORDS and token not in digest_tokens:
                digest_tokens.append(token)
            if len(digest_tokens) == 12:
                break
        if len(digest_tokens) == 12:
            break
    supplemental_note_cues = [
        {
            "clause_id": clause_id,
            "note_line_id": cue.note_line_id,
            "kind": cue.kind.value,
            "status": cue.status,
            "grounding_score": round(cue.grounding_score, 4),
            "text_hint": cue.text_hint,
            "keywords": list(cue.keywords),
            "owner_hints": list(cue.owner_hints),
            "task_labels": list(cue.task_labels),
            "deadline_hints": list(cue.deadline_hints),
            "operation_hints": list(cue.operation_hints),
        }
        for clause_id in window.context_clause_ids
        for cue in note_cues_by_clause.get(clause_id, ())
        if cue.ai_usable
    ]
    payload = {
        "mode": "MUTATION_RESOLUTION",
        "window_id": window.window_id,
        "primary_clause_ids": window.primary_clause_ids,
        "candidate_tasks": task_memory,
        "context_digest": {
            "chronology": "oldest_to_newest",
            "focus": [
                {
                    "clause_id": clause.clause_id,
                    "speaker": clause.speaker_name,
                    "text": clause.text_raw,
                    "flags": sorted(annotations[clause.clause_id].flags),
                }
                for clause in context_clauses if clause.clause_id in focus_ids
            ],
            "participants": list(dict.fromkeys(clause.speaker_name for clause in context_clauses)),
            "topic_keywords": digest_tokens,
            "safety_evidence_clause_ids": [
                clause.clause_id for clause in context_clauses
                if annotations[clause.clause_id].flags & safety_flags
            ],
            "negative_evidence_clause_ids": [
                clause.clause_id for clause in context_clauses
                if annotations[clause.clause_id].flags & negative_flags
            ],
        },
        "supplemental_context": {
            "meeting_note_cues": supplemental_note_cues,
            "task_memory": task_memory,
            "authority": "CONTEXT_ONLY_TRANSCRIPT_REQUIRED",
            "task_memory_authority": "TARGET_RESOLUTION_ONLY",
        },
        "clauses": [
            {
                "clause_id": cid,
                "speaker": clauses_by_id[cid].speaker_name,
                "text": clauses_by_id[cid].text_raw,
                "flags": sorted(annotations[cid].flags),
            }
            for cid in window.context_clause_ids
        ],
        "date_mentions": [
            {
                "date_mention_id": item.date_mention_id,
                "clause_id": item.clause_id,
                "raw_text": item.raw_text,
                "relation": item.relation,
                "date_type": item.date_type,
            }
            for item in mentions.values()
            if item.clause_id in window.context_clause_ids
            and item.purpose == "DEADLINE"
        ],
    }
    response = client.extract_events(payload)
    debug = getattr(client, "debug", False)
    if debug:
        LOGGER.warning(
            "AI fallback window %s returned %s raw event(s)",
            window.window_id,
            len(response.events),
        )
    confidence = {"LOW": 0.45, "MEDIUM": 0.70, "HIGH": 0.90}
    events: list[TaskEvent] = []
    allowed_clause_ids = set(window.context_clause_ids)
    primary_clause_ids = set(window.primary_clause_ids)
    allowed_mention_ids = {
        item.date_mention_id
        for item in mentions.values()
        if item.clause_id in allowed_clause_ids
        and item.purpose == "DEADLINE"
    }
    context_text = "\n".join(
        clauses_by_id[clause_id].text_raw for clause_id in window.context_clause_ids
    )
    participants = _participant_map(clauses_by_id)
    supplied_candidate_task_ids = {
        str(item.get("task_id", "")) for item in task_memory if item.get("task_id")
    }
    for unresolved_item in response.unresolved:
        if any(
            task_id not in supplied_candidate_task_ids
            for task_id in unresolved_item.candidate_task_ids
        ):
            record_contract_rejection(
                category="structural",
                reason="unknown_task_id",
            )
    for item in response.events:
        item.action_text = _clean_action(item.action_text)
        source_clause_ids = list(dict.fromkeys(item.source_clause_ids))
        if (
            not set(source_clause_ids).issubset(allowed_clause_ids)
            or not set(source_clause_ids).intersection(primary_clause_ids)
        ):
            record_contract_rejection(
                category="structural",
                reason="invalid_source_clause",
            )
            if debug:
                LOGGER.warning(
                    "AI fallback rejected event in %s: invalid source clauses %s",
                    window.window_id,
                    source_clause_ids,
                )
            continue
        primary_source_ids = set(source_clause_ids).intersection(primary_clause_ids)
        anchor_clause_id = item.anchor_clause_id.strip()
        if (
            anchor_clause_id not in primary_source_ids
            or anchor_clause_id not in allowed_clause_ids
        ):
            record_contract_rejection(
                category="structural",
                reason="invalid_anchor_clause",
            )
            if debug:
                LOGGER.warning(
                    "AI fallback rejected event in %s: invalid anchor clause %s",
                    window.window_id,
                    anchor_clause_id,
                )
            continue
        if not item.related_task_id or item.related_task_id not in supplied_candidate_task_ids:
            record_contract_rejection(
                category="structural",
                reason="unknown_task_id",
            )
            if debug:
                LOGGER.warning(
                    "AI fallback rejected event in %s: unknown related_task_id %r",
                    window.window_id,
                    item.related_task_id,
                )
            continue
        if item.deadline_mention_id not in allowed_mention_ids:
            item.deadline_mention_id = ""
        if (
            item.event_type == "TASK_CANCEL"
            and is_discourse_transition_cancel(
                f"{item.action_text} {clauses_by_id[anchor_clause_id].text_raw}"
            )
        ):
            record_contract_rejection(
                category="semantic",
                reason="non_concrete_action",
            )
            if debug:
                LOGGER.warning(
                    "AI fallback rejected discourse transition as cancellation in %s: %r",
                    window.window_id,
                    item.action_text,
                )
            continue
        if item.event_type in {"OWNER_ASSIGN", "OWNER_REASSIGN"}:
            if not _is_concrete_action(item.action_text):
                record_contract_rejection(
                    category="semantic",
                    reason="non_concrete_action",
                )
                if debug:
                    LOGGER.warning(
                        "AI fallback rejected event in %s: non-concrete action %r",
                        window.window_id,
                        item.action_text,
                    )
                continue
            canonical_assignee = _validated_ai_assignee(
                item.assignee,
                participants,
                context_text,
            )
            if not canonical_assignee:
                record_contract_rejection(
                    category="semantic",
                    reason="invalid_assignee",
                )
                if debug:
                    LOGGER.warning(
                        "AI fallback rejected event in %s: invalid assignee %r",
                        window.window_id,
                        item.assignee,
                    )
                continue
            item.assignee = canonical_assignee
        order = clauses_by_id[anchor_clause_id].order_index
        events.append(
            TaskEvent(
                make_id("EVENT", start_sequence + len(events) + 1),
                item.event_type,
                source_clause_ids,
                item.action_text,
                item.assignee,
                item.deadline_mention_id,
                related_task_id=item.related_task_id,
                related_task_hint=item.related_task_hint,
                confidence=confidence[item.confidence],
                extraction_source="AI",
                order_index=order,
                anchor_clause_id=anchor_clause_id,
            )
        )
    return events
