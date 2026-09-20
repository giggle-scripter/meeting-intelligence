"""Deterministic meeting-grouped outer and inner fold construction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import ProtocolSnapshot
from .hashing import canonical_json_hash, sha256_file
from .inventory import build_trace_manifest
from .trace_reader import load_trace_set


def _tie_hash(seed: int, case_id: str) -> str:
    return hashlib.sha256(f"{seed}:{case_id}".encode()).hexdigest()


def assign_folds(cases: list[dict[str, Any]], fold_count: int, seed: int) -> dict[str, int]:
    ordered = sorted(cases, key=lambda row: (-row["expected_task_count"], _tie_hash(seed, row["case_id"])))
    state = [
        {"wave": {}, "length": {}, "tasks": 0, "cases": 0}
        for _ in range(fold_count)
    ]
    assignment: dict[str, int] = {}
    for row in ordered:
        keys = []
        for fold_id, fold in enumerate(state):
            keys.append(
                (
                    fold["wave"].get(row["wave"], 0),
                    fold["length"].get(row["length_class"], 0),
                    fold["tasks"],
                    fold["cases"],
                    fold_id,
                )
            )
        fold_id = min(range(fold_count), key=lambda index: keys[index])
        assignment[row["case_id"]] = fold_id
        fold = state[fold_id]
        fold["wave"][row["wave"]] = fold["wave"].get(row["wave"], 0) + 1
        fold["length"][row["length_class"]] = fold["length"].get(row["length_class"], 0) + 1
        fold["tasks"] += row["expected_task_count"]
        fold["cases"] += 1
    return assignment


def build_case_table(repo_root: Path, snapshot: ProtocolSnapshot) -> list[dict[str, Any]]:
    traces = load_trace_set(repo_root / snapshot.protocol.trace_path)
    rows: list[dict[str, Any]] = []
    for expected_path in sorted((repo_root / "data/validation").glob("*/expected_output.json")):
        case_dir = expected_path.parent
        case_id = case_dir.name
        metadata_path = case_dir / "metadata.json"
        transcript_path = case_dir / "transcript.txt"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        expected = json.loads(expected_path.read_text(encoding="utf-8-sig"))
        if metadata.get("case_id") != case_id or traces[case_id].get("meeting_id") != case_id:
            raise ValueError("case/meeting identity mismatch")
        parts = case_id.split("-")
        rows.append(
            {
                "case_id": case_id,
                "wave": f"W{metadata['wave']}",
                "length_class": parts[1],
                "expected_task_count": len(expected.get("tasks", [])),
                "clause_count": len(traces[case_id].get("clauses", [])),
                "metadata_sha256": sha256_file(metadata_path),
                "transcript_sha256": sha256_file(transcript_path),
                "expected_output_sha256": sha256_file(expected_path),
            }
        )
    return rows


def build_fold_manifest(repo_root: Path, snapshot: ProtocolSnapshot) -> dict[str, Any]:
    protocol = snapshot.protocol
    cases = build_case_table(repo_root, snapshot)
    trace_rows, _ = build_trace_manifest(repo_root, protocol.trace_path)
    baseline_trace_rows, _ = build_trace_manifest(repo_root, protocol.baseline_trace_path)
    outer = assign_folds(cases, protocol.outer_folds, protocol.fold_seed)
    if len(outer) != protocol.expected_cases or set(outer) != {row["case_id"] for row in cases}:
        raise ValueError("outer fold coverage failure")
    case_by_id = {row["case_id"]: row for row in cases}
    outer_records: list[dict[str, Any]] = []
    for outer_id in range(protocol.outer_folds):
        valid_ids = sorted(case_id for case_id, fold_id in outer.items() if fold_id == outer_id)
        train_rows = [row for row in cases if outer[row["case_id"]] != outer_id]
        inner = assign_folds(train_rows, protocol.inner_folds, protocol.fold_seed + outer_id + 1)
        if set(inner) & set(valid_ids) or set(inner) != {row["case_id"] for row in train_rows}:
            raise ValueError("inner/outer leakage")
        outer_records.append(
            {
                "outer_fold_id": outer_id,
                "train_case_ids": sorted(inner),
                "valid_case_ids": valid_ids,
                "inner_fold_by_case": dict(sorted(inner.items())),
                "expected_train_tasks": sum(case_by_id[item]["expected_task_count"] for item in inner),
                "expected_valid_tasks": sum(case_by_id[item]["expected_task_count"] for item in valid_ids),
            }
        )
    return {
        "schema_version": "distillation-fold-manifest-v1",
        "protocol_sha256": snapshot.sha256,
        "dataset_hash": canonical_json_hash(cases),
        "evidence_sha256": sha256_file(repo_root / protocol.evidence_path),
        "trace_manifest_sha256": canonical_json_hash(trace_rows),
        "baseline_trace_manifest_sha256": canonical_json_hash(baseline_trace_rows),
        "case_count": len(cases),
        "cases": cases,
        "outer_fold_by_case": dict(sorted(outer.items())),
        "outer_folds": outer_records,
    }


def assert_no_fold_leakage(manifest: dict[str, Any]) -> None:
    all_valid: list[str] = []
    for fold in manifest["outer_folds"]:
        train = set(fold["train_case_ids"])
        valid = set(fold["valid_case_ids"])
        if train & valid or set(fold["inner_fold_by_case"]) != train:
            raise ValueError("cross-fold training leak")
        all_valid.extend(valid)
    if len(all_valid) != len(set(all_valid)) or set(all_valid) != set(manifest["outer_fold_by_case"]):
        raise ValueError("outer validation coverage failure")
