"""Build a reviewed-corpus-derived clause dataset for the action classifier.

The builder is deliberately conservative: uncertain ground-truth-to-clause
matches are emitted as manual-review records with no label and are excluded
from fold statistics. It never edits ``data/validation``.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ai import DisabledAiClient
from backend.app.annotation import annotate_clauses, extract_date_mentions
from backend.app.evaluation import compare_case
from backend.app.ml.action_features import build_action_features
from backend.app.ml.contracts import ActionLabel
from backend.app.models import Clause, ClauseAnnotation, MeetingInput
from backend.app.pipeline import preprocess_meeting, process_meeting
from backend.app.utils.hashing import stable_hash


SCHEMA_VERSION = "action-dataset-v1"
BUILDER_VERSION = "action-dataset-builder-v1"
FEATURES_VERSION = "action-features-v1"
DEFAULT_FOLD_COUNT = 5
DEFAULT_MAPPING_THRESHOLD = 0.72
DEFAULT_MAPPING_MARGIN = 0.12
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = {
    "a",
    "an",
    "anh",
    "cac",
    "chi",
    "cho",
    "em",
    "for",
    "phan",
    "task",
    "the",
    "to",
    "va",
    "viec",
}
_CREATION_FLAGS = {
    "FIRST_PERSON_COMMITMENT",
    "DIRECT_ASSIGNMENT",
    "CONFIRMATION",
}
_NEGATIVE_FLAGS = {
    "ADMIN_FOLLOWUP",
    "BRAINSTORM",
    "FUTURE_DISCUSSION",
    "HYPOTHETICAL",
    "PAST_COMPLETED",
    "PROGRESS_UPDATE",
    "ROOT_QUESTION",
    "SUGGESTION_ONLY",
}
_MUTATION_FLAGS = {
    "CANCELLATION",
    "CORRECTION",
    "REJECTION",
}
_HARD_NEGATIVE_MAPPING_FLAGS = {
    "ADMIN_FOLLOWUP",
    "FUTURE_DISCUSSION",
    "HYPOTHETICAL",
    "PAST_COMPLETED",
    "PROGRESS_UPDATE",
    "ROOT_QUESTION",
}


@dataclass(frozen=True)
class MatchCandidate:
    clause_id: str
    score: float
    lexical_score: float
    owner_supported: bool
    deadline_supported: bool
    creation_cue_supported: bool
    negative_guarded: bool


@dataclass(frozen=True)
class TaskMapping:
    task_index: int
    task_name: str
    method: str
    accepted: bool
    manual_review_required: bool
    selected_clause_id: str | None
    score: float
    margin: float
    candidate_clause_ids: tuple[str, ...]
    candidate_scores: tuple[float, ...]
    label: ActionLabel | None
    reason: str


@dataclass
class CaseData:
    case_id: str
    meeting: MeetingInput
    expected: dict[str, Any]
    clauses: list[Clause]
    annotations: dict[str, ClauseAnnotation]
    speaker_names: set[str]
    note_text: str


def _normalize(value: Any) -> str:
    text = str(value or "").casefold().replace("đ", "d")
    text = "".join(
        character
        for character in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(character)
    )
    return " ".join(_TOKEN_RE.findall(text))


def _tokens(value: Any) -> set[str]:
    return {token for token in _normalize(value).split() if token not in _STOPWORDS}


def _lexical_score(query: str, text: str) -> float:
    query_normalized = _normalize(query)
    text_normalized = _normalize(text)
    if not query_normalized or not text_normalized:
        return 0.0
    if query_normalized == text_normalized:
        return 1.0
    query_tokens = _tokens(query)
    text_tokens = _tokens(text)
    if not query_tokens or not text_tokens:
        return 0.0
    intersection = len(query_tokens & text_tokens)
    containment = intersection / len(query_tokens)
    jaccard = intersection / len(query_tokens | text_tokens)
    sequence = SequenceMatcher(None, query_normalized, text_normalized).ratio()
    substring = 1.0 if query_normalized in text_normalized else 0.0
    return max(substring, containment * 0.85 + jaccard * 0.15, sequence)


def _owner_names(value: Any) -> list[str]:
    return [
        item
        for item in (_normalize(part) for part in re.split(r"[;,&]", str(value or "")))
        if item
    ]


def _owner_supported(task: dict[str, Any], clause: Clause) -> bool:
    owners = _owner_names(task.get("assignee"))
    if not owners:
        return False
    speaker = _normalize(clause.speaker_name)
    text = _normalize(clause.text_raw)
    return any(owner == speaker or re.search(rf"\b{re.escape(owner)}\b", text) for owner in owners)


def _deadline_supported(task: dict[str, Any], clause: Clause) -> bool:
    deadline = _normalize(task.get("due_date_text"))
    if not deadline:
        return False
    text = _normalize(clause.text_raw)
    if deadline in text:
        return True
    deadline_tokens = _tokens(deadline)
    return bool(deadline_tokens) and len(deadline_tokens & _tokens(text)) / len(deadline_tokens) >= 0.75


def _creation_cue_supported(annotation: ClauseAnnotation) -> bool:
    flags = annotation.flags
    return bool(flags & _CREATION_FLAGS) or (
        "ACTION_VERB" in flags and not flags & (_NEGATIVE_FLAGS | _MUTATION_FLAGS)
    )


def _negative_guarded(clause: Clause, annotation: ClauseAnnotation) -> bool:
    flags = annotation.flags
    if flags & _HARD_NEGATIVE_MAPPING_FLAGS:
        return True
    if flags & {"BRAINSTORM", "SUGGESTION_ONLY"} and (
        not flags & _CREATION_FLAGS or annotation.score <= 0
    ):
        return True
    if clause.text_raw.rstrip().endswith("?") and (
        "DIRECT_ASSIGNMENT" not in flags or annotation.score <= 0
    ):
        return True
    return False


def _rank_task_candidates(
    task: dict[str, Any],
    clauses: list[Clause],
    annotations: dict[str, ClauseAnnotation],
) -> tuple[str, list[MatchCandidate]]:
    evidence = str(task.get("evidence") or "").strip()
    query = evidence or str(task.get("task_name") or "").strip()
    method = "evidence" if evidence else "task_name_fallback"
    ranked: list[MatchCandidate] = []
    query_normalized = _normalize(query)
    for clause in clauses:
        annotation = annotations[clause.clause_id]
        lexical = _lexical_score(query, clause.text_raw)
        owner = _owner_supported(task, clause)
        deadline = _deadline_supported(task, clause)
        creation = _creation_cue_supported(annotation)
        negative_guarded = _negative_guarded(clause, annotation)
        exact_evidence = bool(
            evidence
            and query_normalized
            and query_normalized in _normalize(clause.text_raw)
        )
        if exact_evidence:
            score = 1.0
        else:
            score = min(
                1.0,
                lexical * 0.75
                + (0.10 if owner else 0.0)
                + (0.10 if deadline else 0.0)
                + (0.05 if creation else 0.0),
            )
            if negative_guarded:
                score = max(0.0, score - 0.25)
        ranked.append(
            MatchCandidate(
                clause_id=clause.clause_id,
                score=round(score, 6),
                lexical_score=round(lexical, 6),
                owner_supported=owner,
                deadline_supported=deadline,
                creation_cue_supported=creation,
                negative_guarded=negative_guarded,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.clause_id))
    return method, ranked


def _action_label(annotation: ClauseAnnotation) -> ActionLabel:
    if annotation.flags & _CREATION_FLAGS and not annotation.flags & _NEGATIVE_FLAGS:
        return ActionLabel.CLEAR_ACTION
    return ActionLabel.POSSIBLE_ACTION


def map_expected_tasks(
    case: CaseData,
    *,
    mapping_threshold: float,
    mapping_margin: float,
) -> list[TaskMapping]:
    mappings: list[TaskMapping] = []
    for task_index, task in enumerate(case.expected.get("tasks", [])):
        method, ranked = _rank_task_candidates(task, case.clauses, case.annotations)
        top = ranked[0] if ranked else None
        runner_up_score = ranked[1].score if len(ranked) > 1 else 0.0
        margin = (top.score - runner_up_score) if top else 0.0
        exact_evidence = bool(
            top
            and method == "evidence"
            and _normalize(task.get("evidence"))
            in _normalize(next(clause.text_raw for clause in case.clauses if clause.clause_id == top.clause_id))
        )
        support = bool(
            top
            and (
                top.creation_cue_supported
                or (top.owner_supported and top.deadline_supported)
            )
            and not top.negative_guarded
        )
        unique_exact_evidence = exact_evidence and margin >= mapping_margin
        accepted = bool(
            top
            and (
                unique_exact_evidence
                or (
                    top.score >= mapping_threshold
                    and margin >= mapping_margin
                    and support
                )
            )
        )
        if accepted and top:
            annotation = case.annotations[top.clause_id]
            reason = (
                "unique_exact_evidence"
                if unique_exact_evidence
                else "unique_supported_match"
            )
            selected_clause_id = top.clause_id
            label = _action_label(annotation)
        else:
            reason = (
                "no_candidate"
                if top is None
                else "below_threshold"
                if top.score < mapping_threshold
                else "insufficient_unique_margin"
                if margin < mapping_margin
                else "missing_creation_support"
            )
            selected_clause_id = top.clause_id if top else None
            label = None
        mappings.append(
            TaskMapping(
                task_index=task_index,
                task_name=str(task.get("task_name") or ""),
                method=method,
                accepted=accepted,
                manual_review_required=not accepted,
                selected_clause_id=selected_clause_id,
                score=top.score if top else 0.0,
                margin=round(margin, 6),
                candidate_clause_ids=tuple(item.clause_id for item in ranked[:3]),
                candidate_scores=tuple(item.score for item in ranked[:3]),
                label=label,
                reason=reason,
            )
        )
    return mappings


def _load_case(case_dir: Path) -> CaseData:
    metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8-sig"))
    expected = json.loads((case_dir / "expected_output.json").read_text(encoding="utf-8-sig"))
    transcript_path = case_dir / metadata.get("transcript_file", "transcript.txt")
    transcript = transcript_path.read_text(encoding="utf-8-sig")
    meeting = MeetingInput(
        meeting_id=str(metadata.get("meeting_id") or case_dir.name),
        meeting_title=str(metadata.get("meeting_title") or case_dir.name),
        meeting_date=str(metadata.get("meeting_date") or ""),
        transcript_raw=transcript,
        file_name=transcript_path.name,
    )
    stages = preprocess_meeting(meeting)
    clauses = stages["clauses"]
    mentions = extract_date_mentions(clauses)
    date_clause_ids = {
        mention.clause_id
        for mention in mentions.values()
        if mention.purpose != "MEETING_DATE"
    }
    annotations = annotate_clauses(clauses, date_clause_ids)
    note_path = case_dir / "meeting_note.txt"
    return CaseData(
        case_id=case_dir.name,
        meeting=meeting,
        expected=expected,
        clauses=clauses,
        annotations=annotations,
        speaker_names={clause.speaker_name for clause in clauses if clause.speaker_name},
        note_text=note_path.read_text(encoding="utf-8-sig") if note_path.exists() else "",
    )


def _note_supported(clause: Clause, note_text: str) -> bool:
    clause_tokens = _tokens(clause.text_raw)
    note_tokens = _tokens(note_text)
    if len(clause_tokens) < 2 or not note_tokens:
        return False
    return len(clause_tokens & note_tokens) / len(clause_tokens) >= 0.5


def _false_create_clause_ids(case: CaseData) -> set[str]:
    result = process_meeting(
        case.meeting,
        ai_client=DisabledAiClient(),
        meeting_context_mode="assist",
    )
    actual = asdict(result)
    comparison = compare_case(case.case_id, case.expected, actual)
    clause_ids: set[str] = set()
    for task in comparison.unexpected_tasks:
        evidence = str(task.get("evidence") or "")
        if not evidence:
            continue
        ranked = sorted(
            (
                (_lexical_score(evidence, clause.text_raw), clause.clause_id)
                for clause in case.clauses
            ),
            reverse=True,
        )
        if ranked and ranked[0][0] >= 0.55:
            clause_ids.add(ranked[0][1])
    return clause_ids


def _semantic_text(
    clause: Clause,
    previous_clauses: list[str],
    next_clauses: list[str],
) -> str:
    previous = "\n".join(previous_clauses)
    following = "\n".join(next_clauses)
    focus = f"{clause.speaker_name}: {clause.text_raw}" if clause.speaker_name else clause.text_raw
    return f"[PREV]\n{previous}\n\n[FOCUS]\n{focus}\n\n[NEXT]\n{following}"


def _record_for_clause(
    case: CaseData,
    clause_index: int,
    *,
    label: ActionLabel | None,
    reasons: set[str],
    mappings: list[TaskMapping],
    manual_review_required: bool,
) -> dict[str, Any]:
    clause = case.clauses[clause_index]
    previous = case.clauses[clause_index - 1] if clause_index else None
    following = case.clauses[clause_index + 1] if clause_index + 1 < len(case.clauses) else None
    previous_texts = [previous.text_raw] if previous else []
    next_texts = [following.text_raw] if following else []
    mapping_payloads = [
        {
            **asdict(mapping),
            "label": mapping.label.value if mapping.label else None,
        }
        for mapping in mappings
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": f"action_{stable_hash(f'{case.case_id}|{clause.clause_id}', 20)}",
        "meeting_id": case.meeting.meeting_id,
        "case_id": case.case_id,
        "clause_id": clause.clause_id,
        "order_index": clause.order_index,
        "focus": clause.text_raw,
        "previous_clauses": previous_texts,
        "next_clauses": next_texts,
        "speaker": clause.speaker_name,
        "label": label.value if label else None,
        "eligible_for_training": label is not None and not manual_review_required,
        "manual_review_required": manual_review_required,
        "features": build_action_features(
            clause,
            case.annotations[clause.clause_id],
            previous_clause=previous,
            speaker_names=case.speaker_names,
            note_supported=_note_supported(clause, case.note_text),
        ),
        "semantic_text": _semantic_text(clause, previous_texts, next_texts),
        "selection_reasons": sorted(reasons),
        "task_mappings": mapping_payloads,
    }


def build_case_records(
    case: CaseData,
    mappings: list[TaskMapping],
    *,
    include_pipeline_false_creates: bool,
) -> list[dict[str, Any]]:
    index_by_id = {clause.clause_id: index for index, clause in enumerate(case.clauses)}
    positive_by_clause: dict[str, list[TaskMapping]] = defaultdict(list)
    manual_by_clause: dict[str, list[TaskMapping]] = defaultdict(list)
    protected_manual_candidates: set[str] = set()
    for mapping in mappings:
        if mapping.accepted and mapping.selected_clause_id:
            positive_by_clause[mapping.selected_clause_id].append(mapping)
        elif mapping.selected_clause_id:
            manual_by_clause[mapping.selected_clause_id].append(mapping)
            protected_manual_candidates.update(mapping.candidate_clause_ids)

    reasons_by_clause: dict[str, set[str]] = defaultdict(set)
    for clause_id in positive_by_clause:
        index = index_by_id[clause_id]
        if index:
            reasons_by_clause[case.clauses[index - 1].clause_id].add("adjacent_to_positive")
        if index + 1 < len(case.clauses):
            reasons_by_clause[case.clauses[index + 1].clause_id].add("adjacent_to_positive")

    for index, clause in enumerate(case.clauses):
        flags = case.annotations[clause.clause_id].flags
        for flag in sorted(flags & _NEGATIVE_FLAGS):
            reasons_by_clause[clause.clause_id].add(flag.casefold())
        if clause.text_raw.rstrip().endswith("?"):
            reasons_by_clause[clause.clause_id].add("question_punctuation")
        if flags & _MUTATION_FLAGS:
            reasons_by_clause[clause.clause_id].add("mutation_only")
        if "EXPLICIT_TASK_LABEL" in flags and clause.clause_id not in positive_by_clause:
            reasons_by_clause[clause.clause_id].add("recap_or_task_reference")
        if "REJECTION" in flags and index:
            reasons_by_clause[case.clauses[index - 1].clause_id].add("rejected_assignment")

    if include_pipeline_false_creates:
        for clause_id in _false_create_clause_ids(case):
            reasons_by_clause[clause_id].add("current_pipeline_false_create")

    records: list[dict[str, Any]] = []
    for index, clause in enumerate(case.clauses):
        clause_id = clause.clause_id
        if clause_id in positive_by_clause:
            labels = {mapping.label for mapping in positive_by_clause[clause_id]}
            label = (
                ActionLabel.CLEAR_ACTION
                if ActionLabel.CLEAR_ACTION in labels
                else ActionLabel.POSSIBLE_ACTION
            )
            records.append(
                _record_for_clause(
                    case,
                    index,
                    label=label,
                    reasons={"ground_truth_task_match"},
                    mappings=positive_by_clause[clause_id],
                    manual_review_required=False,
                )
            )
            continue
        if clause_id in manual_by_clause:
            records.append(
                _record_for_clause(
                    case,
                    index,
                    label=None,
                    reasons={"uncertain_ground_truth_mapping"},
                    mappings=manual_by_clause[clause_id],
                    manual_review_required=True,
                )
            )
            continue
        if clause_id in protected_manual_candidates:
            continue
        reasons = reasons_by_clause.get(clause_id, set())
        if not reasons:
            continue
        flags = case.annotations[clause_id].flags
        label = (
            ActionLabel.UPDATE_ONLY
            if flags & _MUTATION_FLAGS or "recap_or_task_reference" in reasons
            else ActionLabel.NON_ACTION
        )
        records.append(
            _record_for_clause(
                case,
                index,
                label=label,
                reasons=reasons,
                mappings=[],
                manual_review_required=False,
            )
        )
    return records


def build_group_folds(
    records: list[dict[str, Any]], fold_count: int, seed: int
) -> dict[str, Any]:
    eligible = [record for record in records if record["eligible_for_training"]]
    by_meeting: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in eligible:
        by_meeting[record["meeting_id"]].append(record)
    if fold_count < 2:
        raise ValueError("fold_count must be at least 2")
    if len(by_meeting) < fold_count:
        raise ValueError("fold_count cannot exceed the number of meeting groups")

    def group_key(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, str]:
        meeting_id, meeting_records = item
        tie_breaker = hashlib.sha256(f"{seed}|{meeting_id}".encode()).hexdigest()
        return (-len(meeting_records), tie_breaker)

    fold_meetings: list[list[str]] = [[] for _ in range(fold_count)]
    fold_sizes = [0] * fold_count
    for meeting_id, meeting_records in sorted(by_meeting.items(), key=group_key):
        target = min(range(fold_count), key=lambda index: (fold_sizes[index], index))
        fold_meetings[target].append(meeting_id)
        fold_sizes[target] += len(meeting_records)

    all_meetings = set(by_meeting)
    folds: list[dict[str, Any]] = []
    for fold_index, validation_ids in enumerate(fold_meetings):
        validation_set = set(validation_ids)
        train_set = all_meetings - validation_set
        validation_records = [record for record in eligible if record["meeting_id"] in validation_set]
        train_records = [record for record in eligible if record["meeting_id"] in train_set]
        folds.append(
            {
                "fold": fold_index,
                "train_meeting_ids": sorted(train_set),
                "validation_meeting_ids": sorted(validation_set),
                "train_record_count": len(train_records),
                "validation_record_count": len(validation_records),
                "train_label_counts": dict(sorted(Counter(record["label"] for record in train_records).items())),
                "validation_label_counts": dict(sorted(Counter(record["label"] for record in validation_records).items())),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": "deterministic_group_k_fold",
        "group_key": "meeting_id",
        "fold_count": fold_count,
        "seed": seed,
        "folds": folds,
    }


def _corpus_fingerprint(validation_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(validation_dir.rglob("*")):
        if not path.is_file():
            continue
        digest.update(path.relative_to(validation_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_dataset(
    validation_dir: Path,
    *,
    fold_count: int = DEFAULT_FOLD_COUNT,
    seed: int = 20260817,
    mapping_threshold: float = DEFAULT_MAPPING_THRESHOLD,
    mapping_margin: float = DEFAULT_MAPPING_MARGIN,
    include_pipeline_false_creates: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    case_dirs = sorted(
        path
        for path in validation_dir.iterdir()
        if path.is_dir()
        and (path / "metadata.json").exists()
        and (path / "expected_output.json").exists()
    )
    records: list[dict[str, Any]] = []
    mapping_audit: list[dict[str, Any]] = []
    total_clauses = 0
    expected_task_count = 0
    for case_dir in case_dirs:
        case = _load_case(case_dir)
        total_clauses += len(case.clauses)
        expected_task_count += len(case.expected.get("tasks", []))
        mappings = map_expected_tasks(
            case,
            mapping_threshold=mapping_threshold,
            mapping_margin=mapping_margin,
        )
        for mapping in mappings:
            mapping_audit.append(
                {
                    "case_id": case.case_id,
                    **asdict(mapping),
                    "label": mapping.label.value if mapping.label else None,
                }
            )
        records.extend(
            build_case_records(
                case,
                mappings,
                include_pipeline_false_creates=include_pipeline_false_creates,
            )
        )
    records.sort(key=lambda item: (item["case_id"], item["order_index"], item["clause_id"]))
    folds = build_group_folds(records, fold_count, seed)
    eligible = [record for record in records if record["eligible_for_training"]]
    manual = [record for record in records if record["manual_review_required"]]
    accepted_mappings = [item for item in mapping_audit if item["accepted"]]
    manual_mappings = [item for item in mapping_audit if item["manual_review_required"]]
    corpus_fingerprint = _corpus_fingerprint(validation_dir)
    positive_record_count = sum(
        record["label"] in {
            ActionLabel.CLEAR_ACTION.value,
            ActionLabel.POSSIBLE_ACTION.value,
        }
        for record in eligible
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "features_version": FEATURES_VERSION,
        "dataset_version": f"{BUILDER_VERSION}-{corpus_fingerprint[:12]}",
        "source": validation_dir.as_posix(),
        "source_corpus_sha256": corpus_fingerprint,
        "case_count": len(case_dirs),
        "clause_count": total_clauses,
        "expected_task_count": expected_task_count,
        "record_count": len(records),
        "eligible_training_record_count": len(eligible),
        "eligible_positive_record_count": positive_record_count,
        "eligible_non_positive_record_count": len(eligible) - positive_record_count,
        "manual_review_record_count": len(manual),
        "label_counts": dict(sorted(Counter(record["label"] for record in eligible).items())),
        "selection_reason_counts": dict(
            sorted(Counter(reason for record in records for reason in record["selection_reasons"]).items())
        ),
        "mapping": {
            "threshold": mapping_threshold,
            "margin": mapping_margin,
            "accepted_task_count": len(accepted_mappings),
            "manual_review_task_count": len(manual_mappings),
            "method_counts": dict(sorted(Counter(item["method"] for item in mapping_audit).items())),
            "reason_counts": dict(sorted(Counter(item["reason"] for item in mapping_audit).items())),
            "manual_review": manual_mappings,
        },
        "training_readiness": {
            "shadow_training_allowed": True,
            "production_threshold_tuning_allowed": not manual_mappings,
            "blocking_manual_review_task_count": len(manual_mappings),
            "note": (
                "Only records with eligible_for_training=true may be trained. "
                "Resolve manual mappings before production threshold tuning."
            ),
        },
        "hard_negative_policy": {
            "pipeline_false_creates": include_pipeline_false_creates,
            "adjacent_to_positive": True,
            "semantic_negative_cues": sorted(_NEGATIVE_FLAGS),
            "rejected_assignment": True,
            "recap_or_task_reference": True,
            "mutation_only": True,
            "random_easy_negatives": False,
        },
        "fold_count": fold_count,
        "fold_group_key": "meeting_id",
        "seed": seed,
    }
    return records, folds, report


def write_dataset(
    records: list[dict[str, Any]],
    folds: dict[str, Any],
    report: dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    train_text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    (output_dir / "train.jsonl").write_text(train_text, encoding="utf-8", newline="\n")
    (output_dir / "folds.json").write_text(
        json.dumps(folds, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output_dir / "dataset-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("validation_dir", nargs="?", type=Path, default=Path("data/validation"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/ml/action-classifier"),
    )
    parser.add_argument("--fold-count", type=int, default=DEFAULT_FOLD_COUNT)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--mapping-threshold", type=float, default=DEFAULT_MAPPING_THRESHOLD)
    parser.add_argument("--mapping-margin", type=float, default=DEFAULT_MAPPING_MARGIN)
    parser.add_argument(
        "--skip-pipeline-false-creates",
        action="store_true",
        help="Skip the slower rule-only false-create mining pass.",
    )
    args = parser.parse_args()
    records, folds, report = build_dataset(
        args.validation_dir,
        fold_count=args.fold_count,
        seed=args.seed,
        mapping_threshold=args.mapping_threshold,
        mapping_margin=args.mapping_margin,
        include_pipeline_false_creates=not args.skip_pipeline_false_creates,
    )
    write_dataset(records, folds, report, args.output_dir)
    print(json.dumps({key: value for key, value in report.items() if key != "mapping"}, ensure_ascii=False, indent=2))
    print(
        f"Mappings: accepted={report['mapping']['accepted_task_count']} "
        f"manual_review={report['mapping']['manual_review_task_count']}"
    )
    print(f"Output: {args.output_dir}")


if __name__ == "__main__":
    main()
