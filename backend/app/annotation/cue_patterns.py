"""Reusable Vietnamese and English semantic cue patterns."""

import re

COMMITMENT_PATTERNS = [
    r"\b(?:em|tôi|mình|anh|chị)\s+sẽ\b",
    r"\b(?:em|tôi|mình|anh|chị)\s+nhận\b",
    r"\bđể\s+(?:em|tôi|mình|anh|chị)\b",
    r"\b(?:em|tôi|mình|anh|chị)\s+phụ trách\b",
    r"\b(?:i|we)\s+(?:will|shall|can take|will own)\b",
    r"^(?:còn\s+|riêng\s+)?[^,;:.!?]{2,50}\s+(?:thì\s+)?"
    r"(?:em|tôi|mình|anh|chị)\s+sẽ\b",
]

ASSIGNMENT_PATTERNS = [
    r"\b[\wÀ-ỹ'-]+\s+manager\s+là\s+[\wÀ-ỹ'-]+\b",
    r"\b[\w'-]+\s+manager\s+is\s+[\w'-]+\b",
    r"\bgiao\s+(?:cho\s+)?[\wÀ-ỹ'-]+\b",
    r"\bchốt\s+[\wÀ-ỹ'-]+\s+phụ trách\b",
    r"\b[\wÀ-ỹ'-]+\s+chịu trách nhiệm\b",
    r"\b[\wÀ-ỹ'-]+\s+phụ trách\b",
    r"\bassign(?:ed)?\s+(?:this\s+)?to\s+[\w'-]+\b",
    r"\b[\w'-]+\s+(?:will own|is responsible for)\b",
    r"\b[\wÀ-ỹ'-]+\s+(?:hãy|cần|giúp|chốt giúp)\s+(?:tạo|chuẩn bị|gửi|hoàn thiện|hoàn thành|cập nhật|kiểm tra|rà soát|sửa|xác nhận|upload|đặt lịch|chạy|làm|soạn|viết|triển khai|xử lý|cài đặt|setup|test|fix)\b",
    r"\b[\wÀ-ỹ'-]+\s+(?:tạo|chuẩn bị|gửi|hoàn thiện|hoàn thành|cập nhật|kiểm tra|rà soát|sửa|xác nhận|upload|đặt lịch|chạy|làm|soạn|viết|triển khai|xử lý|cài đặt|setup|test|fix)\b.+\bnhé\b",
    r"\b[\w'-]+,?\s+please\s+(?:create|prepare|send|complete|update|check|review|fix|confirm|upload|schedule|run|draft|implement)\b",
    r"\b(?:em|anh|chị|bạn)\s+[\wÀ-ỹ'-]+\s+"
    r"(?<!\bđã\s)(?<!\bsẽ\s)(?<!\bđang\s)(?<!\bvừa\s)(?<!\bmới\s)"
    r"(?:tạo|chuẩn bị|gửi|hoàn thành|cập nhật|viết|soạn|test|fix|build)\b",
]

CONFIRMATION_PATTERNS = [r"\b(?:em|tôi|mình)\s+xác nhận\b", r"\bchốt\b", r"\bconfirmed?\b", r"\bi accept\b"]
CORRECTION_PATTERNS = [
    r"\bđổi thành\b", r"\bchuyển sang\b", r"\bdời sang\b", r"\blùi sang\b",
    r"\bchốt lại\b", r"\bthay thế deadline\b", r"\bdeadline mới\b",
    r"\bdeadline cũ\b.+\bkhông còn hiệu lực\b",
    r"\bkhông phải\b.+\bmà là\b", r"\bchange(?:d)?\s+to\b",
    r"\bmove(?:d)?\s+to\b", r"\bcorrection\b",
]
CANCELLATION_PATTERNS = [r"\bkhông triển khai\b", r"\bbỏ task\b", r"\bdừng (?:phần|task|việc)\b", r"\bkhông (?:cần )?làm nữa\b", r"\bloại khỏi scope\b", r"\bhủy task\b", r"\bcancel(?:led)?\b", r"\bremove(?:d)? from scope\b", r"\bno longer (?:do|needed)\b"]
REJECTION_PATTERNS = [r"\bkhông nhận(?: được)?\b", r"\bxin trả lại task\b", r"\bkhông thể cam kết\b", r"\bi can(?:not|'t) take\b", r"\bdecline\b"]
BRAINSTORM_PATTERNS = [
    r"\bcó thể\b", r"\bcó lẽ\b", r"\bnên cân nhắc\b", r"\bem nghĩ nên\b",
    r"\bcó cần\b", r"\bcó nên\b", r"\bmuốn hỏi\b", r"\bý tưởng\b",
    r"\bbrainstorm\b", r"\bmaybe\b", r"\bcould\b", r"\bshould\b",
    r"\bwould it\b", r"\bconsider\b",
]
HYPOTHETICAL_PATTERNS = [r"\bnếu\b", r"\bgiả sử\b", r"\bif\b", r"\bsuppose\b"]
PAST_COMPLETED_PATTERNS = [
    r"\bđã\s+(?:[\wÀ-ỹ'-]+\s+){0,6}xong\b",
    r"\bđã (?:hoàn thành|gửi|làm|viết|tạo|đóng|deploy|cập nhật)\b",
    r"\bcompleted?\b",
    r"\balready (?:sent|done|finished)\b",
]
FUTURE_DISCUSSION_PATTERNS = [r"\bđể (?:bàn|phase) sau\b", r"\bchưa (?:giao|tạo) task\b", r"\btrao đổi sau\b", r"\bbacklog\b", r"\bdiscuss later\b", r"\bnext phase\b"]
SUGGESTION_ONLY_PATTERNS = [
    r"\b(?:có thể|có lẽ|nên cân nhắc|em nghĩ nên|có cần|có nên)\b",
    r"\b(?:maybe|could|should|would it|consider)\b",
]
PROGRESS_UPDATE_PATTERNS = [
    r"\b(?:đang|vừa|mới)\s+(?:làm|viết|gửi|cập nhật|kiểm tra|xử lý|triển khai)\b",
    r"\b(?:in progress|working on|currently updating|status update)\b",
]
ADMIN_FOLLOWUP_PATTERNS = [
    r"\b(?:gửi|send|cập nhật|update|ghi|write)\b.*\b(?:meeting notes?|minutes|recap|group chat|jira|board)\b",
    r"\b(?:sau meeting|sau cuộc họp|after (?:the )?meeting)\b",
]

# A trailing dialogue question does not make an explicit assignment a root
# question: "Giao Tuấn viết báo cáo, em làm thế nào?" remains an assignment.
TRAILING_DIALOGUE_RE = re.compile(
    r"[,;]\s*(?:em|anh|chị|bạn|you)\s+"
    r"(?:làm thế nào|thấy sao|có nhận|được không|nhé|please).*$",
    re.I,
)
ROOT_QUESTION_RE = re.compile(
    r"^(?:có\s+(?:cần|nên)|có\s+.+\s+không|còn\s+.+\s+không|"
    r"(?:cập nhật|update)\s+gì\s+không\b|"
    r"(?:should|could|can)\s+we\b|do\s+we\s+need\b|"
    r"is\s+.+\s+needed\b)",
    re.I,
)
EXPLICIT_TASK_LABEL_RE = re.compile(r"\btask\s*[- ]?[a-z0-9]+\b", re.I)


def is_root_question(text: str) -> bool:
    """Detect interrogatives that are the whole clause, not dialogue tails."""

    cleaned = TRAILING_DIALOGUE_RE.sub("", text).strip()
    return bool(ROOT_QUESTION_RE.search(cleaned))
ACTION_VERBS = [
    "tạo", "chuẩn bị", "gửi", "hoàn thiện", "hoàn thành", "cập nhật", "kiểm tra", "rà soát",
    "sửa", "xác nhận", "upload", "đặt lịch", "chạy", "làm", "soạn", "triển khai", "xử lý",
    "viết", "thêm", "cài đặt", "setup", "test", "review", "fix", "deploy", "cấu hình",
    "kiểm thử", "phân tích", "tổng hợp", "tập hợp", "thu thập", "dọn dẹp", "lập", "build",
    "chuẩn hóa", "cấp", "mở", "tag", "theo dõi", "giám sát", "nghiên cứu", "lo", "vẽ",
    "liên hệ", "bổ sung", "chèn", "rút gọn",
    "tích hợp", "merge", "thực hiện", "tối ưu", "phân loại", "export",
    "thông báo", "xây dựng", "lên danh sách", "migrate", "thiết kế", "duy trì",
    "xác thực", "monitoring", "regression test", "hiển thị", "cải thiện",
    "ẩn danh", "điều chỉnh", "copy", "lấy",
    "create", "prepare", "send", "complete", "update", "check", "review", "fix",
    "confirm", "upload", "schedule", "run", "draft", "implement", "process", "handle",
    "write", "add", "install", "test", "deploy", "configure", "analyze", "compile",
    "collect", "clean", "build", "grant", "open", "track", "investigate",
    "integrate", "merge", "execute", "optimize", "classify", "export",
    "notify", "design", "maintain", "monitor", "display", "improve", "copy",
]
