"""Rule annotation and temporal mention models."""

from dataclasses import dataclass, field


@dataclass
class ClauseAnnotation:
    clause_id: str
    flags: set[str] = field(default_factory=set)
    rule_confidence: float = 0.0
    score: int = 0


@dataclass
class DateMention:
    date_mention_id: str
    clause_id: str
    raw_text: str
    relation: str
    date_type: str
    target_weekday: str = ""
    week_scope: str = ""
    explicit_day: int | None = None
    explicit_month: int | None = None
    explicit_year: int | None = None
    duration_days: int | None = None
    relative_day_offset: int | None = None
    span_start: int = 0
    span_end: int = 0
    purpose: str = "DEADLINE"
