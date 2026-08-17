"""Generate one deterministic secretary-style note for every validation case.

Notes are generated from transcripts only. Expected output is read afterwards
for coverage auditing and never affects note selection or wording.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.annotation import annotate_clauses, extract_date_mentions
from backend.app.models import MeetingInput
from backend.app.pipeline import preprocess_meeting
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.utils.hashing import stable_hash
from backend.app.v2.context.keywords import extract_keywords, keyword_overlap
from backend.app.v2.context.overview import build_overview


FINAL_RECAP = re.compile(
    r"\b(?:final\s+(?:recap|list)|recap\s+(?:lan\s+)?cuoi|"
    r"tong\s+ket\s+(?:lan\s+)?cuoi|tong\s+ket\s+toan\s+bo|"
    r"trang\s+thai\s+cuoi|toan\s+bo\s+(?:task|action item))\b",
    re.I,
)
RECAP = re.compile(
    r"\b(?:recap|chot\s+lai|tong\s+ket|action\s+items?|"
    r"danh\s+sach\s+(?:task|cong viec)|cac\s+dau\s+viec)\b",
    re.I,
)
TASK_LABEL = re.compile(r"\b(?:task|ticket)\s*[-#:]?\s*[A-Z0-9-]+\b", re.I)
TASK_ROW = re.compile(r"\b(?:task|ticket)\b.{0,120}(?:[:：\"“”–-]|\bowner\b|\bgiao\b)", re.I)
ACTIVE_WORK = re.compile(
    r"\b(?:đang làm|đang cầm|chỉ còn(?: mỗi)? việc|assigned? (?:to|me)|"
    r"currently working on|in progress)\b",
    re.I,
)
UNCERTAINTY = re.compile(
    r"\?|\b(?:chua\s+chot|pending|can\s+hoi|de\s+hoi|"
    r"co\s+the|nen|could|should|maybe|muon\s+hoi|ban\s+khoan|"
    r"co\s+gang|du\s+kien|hy\s+vong)\b",
    re.I,
)
DISCOURSE_PREFIX = re.compile(
    r"^(?:(?:da|vang|uhm|um|ok|okay|roi|a|the|vay)[,.:\s]+)+",
    re.I,
)
NOTE_ADMIN = re.compile(
    r"\b(?:recap nhanh|tong ket giup|diem lai|cap nhat lai len he thong|"
    r"suy nghi va bao lai|phan recap|ket thuc cuoc hop|thanks|cam on|"
    r"don doc|nhac lai|xem lai|chuyen sang phan)\b",
    re.I,
)
NOTE_VAGUE = re.compile(
    r"\b(?:lam theo|cap nhat lai|hoan thanh som|chuan bi tai lieu kip|"
    r"ghi nhan|co gi .* cap nhat|se review sau|se long ghep|"
    r"dang lam.*assign)\b",
    re.I,
)
NOTE_LOGISTICS = re.compile(
    r"\b(?:co mat|vang|bao om|tham du|phong hop|audio|mic|camera|vao hop|join)\b",
    re.I,
)
TECHNICAL_TITLE = re.compile(r"^(?:W\d+-|[A-Z0-9]+(?:-[A-Z0-9]+){3,})", re.I)
DOMAIN_PHRASES = (
    "tai lieu", "bao cao", "du lieu", "moi truong", "trien khai", "kiem thu",
    "test case", "release", "staging", "production", "payment", "migration",
    "monitoring", "runbook", "rollback", "dashboard", "api", "config", "ci cd",
    "oauth", "token", "database", "frontend", "backend", "uat", "spec",
)
UNCERTAIN_TARGET = re.compile(
    r"\b(?:task|ticket|deadline|dl|owner|reviewer|scope|unresolved|api|spec|"
    r"config|release|staging|production|payment|migration|monitoring|runbook|"
    r"rollback|dashboard|oauth|token|database|frontend|backend|uat|"
    r"tài liệu|báo cáo|dữ liệu|môi trường|triển khai|kiểm thử|test case)\b",
    re.I,
)
TOPIC_STOP = {
    "moi", "nguoi", "can", "truoc", "sau", "thay", "ben", "chua", "cam",
    "den", "dung", "gio", "chung", "dau", "hop", "tong", "nhieu", "tap",
    "phan", "phan viec", "noi", "con", "van", "roi", "nho", "muon", "thay",
    "anh", "chi", "minh", "tuan", "lan", "hoa", "ha", "an", "task",
    "recap", "chot", "owner", "deadline", "nhanh", "giup", "thong nhat",
}


@dataclass(frozen=True)
class NoteAudit:
    case_id: str
    wave: int
    transcript_characters: int
    note_characters: int
    note_line_count: int
    compression_ratio: float
    expected_keyword_coverage: float
    transcript_keyword_grounding: float
    shorthand_present: bool
    uncertainty_present: bool
    exact_line_copy_count: int


def _wave(case_id: str) -> int:
    match = re.match(r"W(\d+)-", case_id, re.I)
    return int(match.group(1)) if match else 1


def _score(index, clause, annotation, final_recap_index: int | None) -> int:
    text = normalize_for_match(clause.text_raw)
    flags = annotation.flags
    score = 6 if final_recap_index is not None and index >= final_recap_index else 0
    score += 20 if FINAL_RECAP.search(text) else 10 if RECAP.search(text) else 0
    score += 18 if TASK_LABEL.search(clause.text_raw) or TASK_ROW.search(clause.text_raw) else 0
    score += 13 if flags & {"CORRECTION", "CANCELLATION", "REJECTION"} else 0
    score += 9 if flags & {"DIRECT_ASSIGNMENT", "FIRST_PERSON_COMMITMENT"} else 0
    score += 3 if "CONFIRMATION" in flags else 0
    score += 3 if "DATE_MENTION" in flags else 0
    score += 2 if "ACTION_VERB" in flags else 0
    score += 8 if ACTIVE_WORK.search(clause.text_raw) and "ACTION_VERB" in flags else 0
    score -= 8 if flags & {"BRAINSTORM", "HYPOTHETICAL", "PAST_COMPLETED", "FUTURE_DISCUSSION"} else 0
    score -= 5 if UNCERTAINTY.search(text) else 0
    return score


def _compact(value: str, variant: int) -> str:
    text = DISCOURSE_PREFIX.sub("", value.strip())
    text = re.sub(r"\bdeadline\b", "dl", text, flags=re.I)
    text = re.sub(r"\b(?:người\s+)?phụ\s+trách\b", "owner", text, flags=re.I)
    text = re.sub(r"\b(?:nhé|nha|ạ|please)\b\s*[.!]*$", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"\s+(?:vì không|nhưng vẫn|but still|vẫn)$", "", text, flags=re.I)
    if len(text) > 170:
        first = re.split(r"[.;]", text, maxsplit=1)[0]
        text = first if len(first) >= 55 else text[:167].rsplit(" ", 1)[0] + "…"
    if variant % 5 == 0:
        text = text.replace(" và ", " + ", 1)
    return text


def _select(meeting: MeetingInput, max_rows: int) -> tuple[list[str], list[str]]:
    stages = preprocess_meeting(meeting)
    clauses = stages["clauses"]
    mentions = extract_date_mentions(clauses)
    annotations = annotate_clauses(clauses, {item.clause_id for item in mentions.values()})
    normalized = [normalize_for_match(clause.text_raw) for clause in clauses]
    markers = [index for index, text in enumerate(normalized) if FINAL_RECAP.search(text)]
    final_index = markers[-1] if markers else None
    ranked = sorted(
        ((_score(index, clause, annotations[clause.clause_id], final_index), index, clause)
         for index, clause in enumerate(clauses)),
        key=lambda item: (-item[0], item[1]),
    )
    include_uncertain = int(stable_hash(meeting.meeting_id, 4), 16) % 2 == 0
    selection_limit = max(1, max_rows - int(include_uncertain))
    participant_keywords = {
        keyword for clause in clauses
        for keyword in extract_keywords(clause.speaker_name, limit=4)
    }
    selected: list[tuple[int, object, tuple[str, ...]]] = []
    for score, index, clause in ranked:
        annotation = annotations[clause.clause_id]
        text = normalize_for_match(clause.text_raw)
        keywords = extract_keywords(clause.text_raw, limit=10)
        if score < 5 or not keywords:
            continue
        if NOTE_ADMIN.search(text) or NOTE_VAGUE.search(text) or NOTE_LOGISTICS.search(text):
            continue
        if "?" in clause.text_raw or UNCERTAINTY.search(text):
            continue
        if annotation.flags & {"BRAINSTORM", "HYPOTHETICAL", "FUTURE_DISCUSSION"}:
            continue
        is_task_row = bool(TASK_LABEL.search(clause.text_raw) or TASK_ROW.search(clause.text_raw))
        is_active_work = bool(ACTIVE_WORK.search(clause.text_raw))
        if annotation.flags & {"PAST_COMPLETED"} and not (
            is_task_row or FINAL_RECAP.search(text)
        ):
            continue
        if not (
            is_task_row
            or (is_active_work and "ACTION_VERB" in annotation.flags)
            or annotation.flags & {
                "DIRECT_ASSIGNMENT", "FIRST_PERSON_COMMITMENT", "CORRECTION",
                "CANCELLATION", "REJECTION",
            }
            or (final_index is not None and index >= final_index and "ACTION_VERB" in annotation.flags)
        ):
            continue
        domain_keywords = [
            keyword for keyword in keywords
            if keyword not in participant_keywords and keyword not in TOPIC_STOP
        ]
        if not is_task_row and not annotation.flags & {"CORRECTION", "CANCELLATION", "REJECTION"}:
            if len(domain_keywords) < 2:
                continue
        if any(keyword_overlap(keywords, prior) >= 0.82 for _, _, prior in selected):
            continue
        selected.append((index, clause, keywords))
        if len(selected) >= selection_limit:
            break
    selected.sort(key=lambda item: item[0])
    rows = [
        _compact(clause.text_raw, int(stable_hash(f"{meeting.meeting_id}|{index}", 4), 16))
        for index, clause, _ in selected
    ]
    uncertain = next(
        (_compact(clause.text_raw, index) + " ? chưa chốt"
         for index, clause in enumerate(clauses)
         if ("?" in clause.text_raw or UNCERTAINTY.search(normalize_for_match(clause.text_raw)))
         and not NOTE_LOGISTICS.search(normalize_for_match(clause.text_raw))
         and (
             TASK_LABEL.search(clause.text_raw)
             or TASK_ROW.search(clause.text_raw)
             or annotations[clause.clause_id].flags & {"DATE_MENTION", "CORRECTION", "CANCELLATION"}
             or UNCERTAIN_TARGET.search(clause.text_raw)
         )
         and len(extract_keywords(clause.text_raw)) >= 2),
        "",
    )
    if (include_uncertain or len(rows) < 3) and uncertain and len(rows) < max_rows:
        rows.append(uncertain)
    # Topic hints are chosen from the title and selected evidence, not the first
    # arbitrary words in the transcript. They remain ranking hints only.
    title_keywords = extract_keywords(meeting.meeting_title, limit=12)
    selected_keywords = [keyword for _, _, values in selected for keyword in values]
    counts = Counter(selected_keywords)
    topic_source = normalize_for_match(
        meeting.meeting_title + " " + " ".join(clause.text_raw for _, clause, _ in selected)
    )
    phrase_candidates = [phrase for phrase in DOMAIN_PHRASES if phrase in topic_source]
    candidates = phrase_candidates + sorted(
        counts,
        key=lambda keyword: (-counts[keyword], selected_keywords.index(keyword)),
    ) + list(title_keywords)
    topic_keywords: list[str] = []
    for keyword in candidates:
        if keyword in TOPIC_STOP or keyword in topic_keywords:
            continue
        topic_keywords.append(keyword)
        if len(topic_keywords) == 7:
            break
    return rows, topic_keywords


def generate_note(meeting: MeetingInput) -> str:
    max_rows = {1: 5, 2: 6, 3: 8, 4: 11, 5: 13}.get(_wave(meeting.meeting_id), 8)
    rows, topics = _select(meeting, max_rows)
    headers = ("note nhanh", "ghi vội", "minutes nháp", "chốt sơ bộ")
    output = [headers[int(stable_hash(meeting.meeting_id, 4), 16) % len(headers)]]
    if meeting.meeting_title.strip() and not TECHNICAL_TITLE.match(meeting.meeting_title.strip()):
        output.append("chủ đề: " + meeting.meeting_title.strip()[:100])
    if topics:
        output.append("kw: " + " / ".join(topics[:7]))
    prefixes = ("-", "*", "-", "1)")
    output.extend(f"{prefixes[index % len(prefixes)]} {row}" for index, row in enumerate(rows))
    if len(output) == 1:
        output.append("- chưa ghi được action rõ")
    return "\n".join(output).strip() + "\n"


def _audit(case_id: str, transcript: str, note: str, expected: dict) -> NoteAudit:
    note_keywords = set(extract_keywords(note, limit=200))
    transcript_keywords = set(extract_keywords(transcript, limit=5000))
    expected_keywords = {
        keyword for task in expected.get("tasks", [])
        for keyword in extract_keywords(str(task.get("task_name", "")), limit=20)
    }
    expected_coverage = len(note_keywords & expected_keywords) / len(expected_keywords) if expected_keywords else 1.0
    grounding = len(note_keywords & transcript_keywords) / len(note_keywords) if note_keywords else 1.0
    transcript_lines = {normalize_for_match(line) for line in transcript.splitlines() if line.strip()}
    note_lines = [line for line in note.splitlines() if line.strip()]
    copied = sum(
        normalize_for_match(re.sub(r"^(?:[-*]|\d+\))\s*", "", line)) in transcript_lines
        for line in note_lines[1:]
    )
    return NoteAudit(
        case_id, _wave(case_id), len(transcript), len(note), len(note_lines),
        round(len(note) / max(1, len(transcript)), 4), round(expected_coverage, 4),
        round(grounding, 4),
        bool(re.search(r"\b(?:dl|owner|topic|nhap|chot)\b|\+", normalize_for_match(note))),
        "?" in note or "chưa chốt" in note, copied,
    )


def generate_dataset(root: Path, audit_path: Path) -> dict:
    audits: list[NoteAudit] = []
    for case_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata_path = case_dir / "metadata.json"
        transcript_path = case_dir / "transcript.txt"
        expected_path = case_dir / "expected_output.json"
        if not metadata_path.exists() or not transcript_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        transcript = transcript_path.read_text(encoding="utf-8-sig")
        meeting = MeetingInput(
            str(metadata.get("case_id") or case_dir.name),
            str(metadata.get("meeting_title") or case_dir.name),
            str(metadata.get("meeting_date")), transcript, transcript_path.name,
        )
        note = generate_note(meeting)
        (case_dir / "meeting_note.txt").write_text(note, encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8-sig")) if expected_path.exists() else {"tasks": []}
        audits.append(_audit(meeting.meeting_id, transcript, note, expected))
    result = {"case_count": len(audits), "by_wave": {}, "cases": [asdict(item) for item in audits]}
    for wave in sorted({item.wave for item in audits}):
        items = [item for item in audits if item.wave == wave]
        result["by_wave"][str(wave)] = {
            "case_count": len(items),
            "average_compression_ratio": round(sum(x.compression_ratio for x in items) / len(items), 4),
            "average_expected_keyword_coverage": round(sum(x.expected_keyword_coverage for x in items) / len(items), 4),
            "average_transcript_keyword_grounding": round(sum(x.transcript_keyword_grounding for x in items) / len(items), 4),
            "shorthand_rate": round(sum(x.shorthand_present for x in items) / len(items), 4),
            "uncertainty_rate": round(sum(x.uncertainty_present for x in items) / len(items), 4),
        }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/validation"))
    parser.add_argument("--audit", type=Path, default=Path("evaluation/meeting-note-audit.json"))
    args = parser.parse_args()
    result = generate_dataset(args.root, args.audit)
    print(f"Generated {result['case_count']} meeting notes. Audit: {args.audit}")


if __name__ == "__main__":
    main()
