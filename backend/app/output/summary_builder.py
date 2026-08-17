"""Deterministic summary template over final task states."""

import re

from ..models import FinalTask


_TECHNICAL_ID_RE = re.compile(
    r"^(?:W\d+-|upload-|(?:smoke|test|demo|sample|transcript|meeting)"
    r"(?:[-_ ]?[A-Z0-9]+)*|[A-Z0-9]+(?:-[A-Z0-9]+){3,})",
    re.I,
)


def _fallback_topic(tasks: list[FinalTask]) -> str:
    actions = [task.task_name.strip() for task in tasks if task.task_name.strip()]
    if not actions:
        return ""
    actions = [action[:1].lower() + action[1:] for action in actions]
    if len(actions) == 1:
        return actions[0]
    if len(actions) == 2:
        return f"{actions[0]} và {actions[1]}"
    return f"{actions[0]}, {actions[1]} và các đầu việc liên quan"


def _topic_sentence(meeting_title: str, tasks: list[FinalTask]) -> str:
    topic = meeting_title.strip().strip(" .")
    if not topic or _TECHNICAL_ID_RE.match(topic):
        topic = _fallback_topic(tasks)
    if not topic:
        return ""
    if topic.lower().startswith("rà soát "):
        return f"Cuộc họp rà soát {topic[8:]}."
    english_title_case = re.fullmatch(
        r"[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)+", topic
    )
    if not re.match(r"^[A-Z]{2,}\b", topic) and not english_title_case:
        topic = topic[:1].lower() + topic[1:]
    return f"Cuộc họp tập trung vào {topic}."


def build_summary(
    meeting_title: str,
    tasks: list[FinalTask],
    no_active_reason: str | None = None,
    meeting_note_present: bool | None = None,
) -> str:
    note_status = (
        " Không có Meeting Note; kết quả được trích xuất từ transcript."
        if meeting_note_present is False else ""
    )
    topic_sentence = _topic_sentence(meeting_title, tasks)
    if not tasks:
        decision_sentence = (
            "Các task cũ được đề cập đã bị hủy; không có task active mới."
            if no_active_reason == "cancelled"
            else "Chưa chốt công việc mới."
        )
        return (f"{topic_sentence} {decision_sentence}".strip() + note_status).strip()

    parts: list[str] = []
    for task in tasks:
        action = task.task_name[:1].lower() + task.task_name[1:]
        if task.assignee:
            part = f"{task.assignee} sẽ {action}"
        else:
            part = f"Cần phân công người thực hiện {action}"
        if (
            task.due_date_text
            and task.due_date_text.casefold() not in task.task_name.casefold()
        ):
            part += f" ({task.due_date_text})"
        parts.append(part)

    count = len(parts)
    decisions = f"{count} công việc được chốt: " + "; ".join(parts) + "."
    return (f"{topic_sentence} {decisions}".strip() + note_status).strip()
