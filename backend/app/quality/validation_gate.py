"""Integrity checks for locked-validation and independently reviewed blind splits."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


REQUIRED_FILES = ("metadata.json", "transcript.txt", "expected_output.json")
VALID_SPLIT_KINDS = {"LOCKED_VALIDATION", "BLIND"}


@dataclass(frozen=True)
class SplitVerification:
    split_id: str
    split_kind: str
    case_ids: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_split_manifest(path: Path) -> dict[str, Any]:
    """Load the deliberately small, reviewable split manifest."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("split manifest schema_version must be 1.0")
    if payload.get("split_kind") not in VALID_SPLIT_KINDS:
        raise ValueError("split manifest split_kind must be LOCKED_VALIDATION or BLIND")
    if not isinstance(payload.get("cases"), list) or not payload["cases"]:
        raise ValueError("split manifest must contain cases")
    return payload


def verify_split_manifest(manifest: dict[str, Any], dataset: Path) -> SplitVerification:
    """Verify reviewed ground truth and immutable source digests for every case."""

    errors: list[str] = []
    case_ids: list[str] = []
    corpus_digest = sha256()
    for item in manifest["cases"]:
        case_id = str(item.get("case_id", ""))
        if not case_id:
            errors.append("case record has no case_id")
            continue
        if case_id in case_ids:
            errors.append(f"duplicate case_id: {case_id}")
            continue
        case_ids.append(case_id)
        case_dir = dataset / case_id
        hashes = item.get("sha256", {})
        for filename in REQUIRED_FILES:
            source = case_dir / filename
            if not source.is_file():
                errors.append(f"{case_id}: missing {filename}")
                continue
            corpus_digest.update(f"{case_id}/{filename}\0".encode())
            corpus_digest.update(source.read_bytes())
            expected = hashes.get(filename)
            if expected is not None and (not isinstance(expected, str) or _digest(source) != expected):
                errors.append(f"{case_id}: digest mismatch for {filename}")
        metadata_path = case_dir / "metadata.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("case_id") != case_id:
                errors.append(f"{case_id}: metadata case_id mismatch")
            ground_truth = metadata.get("ground_truth", {})
            if not ground_truth.get("available") or not ground_truth.get("reviewer"):
                errors.append(f"{case_id}: ground truth is not reviewed")

    expected_corpus_digest = manifest.get("corpus_sha256")
    if not isinstance(expected_corpus_digest, str) or corpus_digest.hexdigest() != expected_corpus_digest:
        errors.append("split corpus digest mismatch")
    if not manifest.get("frozen_at"):
        errors.append("split manifest must declare frozen_at")

    if manifest["split_kind"] == "BLIND":
        independence = manifest.get("independence", {})
        if independence.get("tuned_against") is not False:
            errors.append("blind split must declare tuned_against=false")
        if not independence.get("frozen_at") or not independence.get("reviewer"):
            errors.append("blind split must declare frozen_at and reviewer")
        if len(case_ids) < 20:
            errors.append("blind split must contain at least 20 reviewed meetings")

    return SplitVerification(
        split_id=str(manifest.get("split_id", "")),
        split_kind=str(manifest["split_kind"]),
        case_ids=tuple(case_ids),
        errors=tuple(errors),
    )


def verify_evaluation_report(report: dict[str, Any], case_ids: tuple[str, ...]) -> list[str]:
    """Require a complete, error-free evaluator report for exactly the split."""

    errors: list[str] = []
    actual_ids = [str(item.get("case_id", "")) for item in report.get("case_execution", [])]
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("evaluation report has duplicate case executions")
    if set(actual_ids) != set(case_ids):
        errors.append("evaluation report cases do not match split manifest")
    if report.get("execution_errors"):
        errors.append("evaluation report has execution errors")
    metrics = report.get("reviewed_metrics") or report.get("metrics") or {}
    if int(metrics.get("case_count", 0)) != len(case_ids):
        errors.append("evaluation report metrics do not cover the full split")
    return errors


def task_f1(metrics: dict[str, Any]) -> float:
    precision = float(metrics.get("task_identity_precision", 0.0))
    recall = float(metrics.get("task_identity_recall", 0.0))
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
