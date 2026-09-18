"""Train a private, tenant-scoped V2.27 challenger from approved feedback.

The command is deliberately offline.  It reconstructs the same runtime
candidate union used by the V2.27 runner from each stored source transcript,
then uses only the approved final task list as labels.  The frozen full-fit
artifact is read only; no service, provider, GPU, network call, or promotion
path is involved.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.ai.client import DisabledAiClient  # noqa: E402
from backend.app.models import MeetingInput  # noqa: E402
from backend.app.pipeline import process_meeting  # noqa: E402
from scripts.experimental_distillation.run_v227_experimental import (  # noqa: E402
    ARTIFACT_DIR,
    _runtime_rows,
    _features,
)
from scripts.experimental_distillation.run_v222_task_proposal_reranker_oof import (  # noqa: E402
    CrossClauseLogisticRanker,
    FEATURE_DIMENSIONS,
)


DEFAULT_FEEDBACK_DIRECTORY = ROOT / "evaluation/runtime/v227-feedback"
MODEL_NAME = "model.json"
POLICY_NAME = "frozen-policy.json"
MANIFEST_NAME = "manifest.json"
# The API pins this exact V2.28 manifest before accepting work.  The offline
# trainer must consume the same frozen base; checking only the manifest's
# self-reported artifact hashes would allow a different full-fit package.
FROZEN_MANIFEST_SHA256 = "7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3"
CHALLENGER_ARTIFACTS = (
    MODEL_NAME,
    POLICY_NAME,
    "source-hashes.json",
    "coverage.json",
    "holdout-diagnostics.json",
)
REQUIRED_POLICY_FIELDS = {
    "adaptive_budget", "adaptive_threshold", "base_budget", "base_threshold",
    "bridge_weight", "field_completeness_min", "intermediate_share_min",
    "intermediate_weight", "score_mean_cap", "volume_cutoff",
}
TASK_FIELDS = {"task_name", "assignee", "start_date", "due_date", "due_date_text", "evidence", "status"}
TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
FORBIDDEN_TRACE_KEYS = frozenset({
    "expected", "expected_tasks", "gold", "labels", "oracle", "validation",
    "corrected_final_tasks", "ground_truth", "human_labels",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(_canonical(value) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _tenant(value: str) -> str:
    if not TENANT_RE.fullmatch(value):
        raise ValueError("STOP_INVALID_TENANT_ID")
    return value


def _approved_at(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("STOP_APPROVAL_METADATA_MISSING")
    reviewer, reviewed_at, approval = value.get("reviewer"), value.get("reviewed_at"), value.get("approval")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
        raise ValueError("STOP_APPROVAL_REVIEWER")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip() or len(reviewed_at) > 128:
        raise ValueError("STOP_APPROVAL_REVIEWED_AT")
    try:
        parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("STOP_APPROVAL_REVIEWED_AT_FORMAT") from exc
    if parsed.tzinfo is None or approval is not True:
        raise ValueError("STOP_APPROVAL_REQUIRED")
    return {"reviewer": reviewer.strip(), "reviewed_at": reviewed_at, "approval": True}


def _tasks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 1_000:
        raise ValueError("STOP_INVALID_APPROVED_TASKS")
    output: list[dict[str, Any]] = []
    for index, task in enumerate(value):
        if not isinstance(task, dict) or set(task) != TASK_FIELDS:
            raise ValueError(f"STOP_INVALID_APPROVED_TASK:{index}")
        if any(not isinstance(task[field], str) for field in TASK_FIELDS) or task["status"] != "Proposed":
            raise ValueError(f"STOP_INVALID_APPROVED_TASK:{index}")
        output.append(dict(task))
    return output


def _normalise(value: Any) -> str:
    return " ".join(re.findall(r"\w+", str(value or "").casefold().replace("đ", "d")))


def _identity(task: dict[str, Any]) -> tuple[str, str]:
    return _normalise(task.get("task_name")), _normalise(task.get("assignee"))


def _conservative_labels(approved: Sequence[dict[str, Any]], proposals: Sequence[Any]) -> tuple[list[int], list[dict[str, Any]]]:
    """Label only exact, source-visible identities; return uncovered approvals.

    The API task contract has no task key. The normalized task name and
    assignee pair must agree exactly. This avoids turning a fuzzy near match
    into a training positive.
    """
    labels = [0] * len(proposals)
    used: set[int] = set()
    uncovered: list[dict[str, Any]] = []
    for expected in approved:
        match: int | None = None
        for index, proposal in enumerate(proposals):
            if index in used:
                continue
            actual = proposal.task
            expected_name, expected_owner = _identity(expected)
            actual_name, actual_owner = _identity(actual)
            if (expected_name, expected_owner) == (actual_name, actual_owner) and expected_name:
                match = index
                break
        if match is None:
            uncovered.append(dict(expected))
        else:
            used.add(match)
            labels[match] = 1
    return labels, uncovered


def _validate_trace(trace: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(trace, dict) or not isinstance(trace.get("final_tasks"), list):
        raise ValueError("STOP_SAFE_FEATURE_RECONSTRUCTION_UNAVAILABLE")
    if FORBIDDEN_TRACE_KEYS & set(trace):
        raise ValueError("STOP_GOLD_DATA_IN_SOURCE_TRACE")
    return trace


def _reconstruct_trace(source: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Build an inference-only trace, or stop closed if it cannot be built."""
    if isinstance(source.get("trace"), dict):
        return _validate_trace(source["trace"]), "stored_inference_trace"
    transcript = source.get("transcript")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("STOP_SAFE_FEATURE_RECONSTRUCTION_UNAVAILABLE")
    meeting_id = str(source.get("meeting_id", ""))
    meeting_date = str(source.get("meeting_date", ""))
    title = str(source.get("meeting_title", "") or "meeting")
    if not meeting_id or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", meeting_date):
        raise ValueError("STOP_SAFE_FEATURE_RECONSTRUCTION_UNAVAILABLE")
    try:
        with tempfile.TemporaryDirectory(prefix="v227-feedback-trace-") as temporary:
            meeting = MeetingInput(
                meeting_id=meeting_id, meeting_title=title, meeting_date=meeting_date,
                meeting_date_source="REQUEST", transcript_raw=transcript,
                file_name=str(source.get("file_name", "meeting.txt")),
            )
            process_meeting(
                meeting, ai_client=DisabledAiClient(), trace_enabled=True,
                trace_directory=temporary, summary_topic=title,
                action_candidate_builder_mode="shadow", commitment_router_mode="shadow",
            )
            traces = sorted(Path(temporary).glob("*.json"))
            if len(traces) != 1:
                raise ValueError("STOP_SAFE_FEATURE_RECONSTRUCTION_UNAVAILABLE")
            trace = _validate_trace(_json(traces[0]))
            return trace, "replayed_disabled_v1_trace"
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed on parser drift
        raise ValueError("STOP_SAFE_FEATURE_RECONSTRUCTION_UNAVAILABLE") from exc


def _load_base(
    directory: Path,
    *,
    expected_manifest_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    directory = directory.resolve()
    expected_manifest_sha256 = expected_manifest_sha256 or FROZEN_MANIFEST_SHA256
    paths = {name: directory / name for name in (MODEL_NAME, POLICY_NAME, MANIFEST_NAME)}
    if any(not path.is_file() for path in paths.values()):
        raise ValueError("STOP_MISSING_FROZEN_FULL_FIT_ARTIFACT")
    if _sha256(paths[MANIFEST_NAME]) != expected_manifest_sha256:
        raise ValueError("STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH")
    manifest = _json(paths[MANIFEST_NAME])
    if manifest.get("schema_version") != "v228-manifest-v1" or manifest.get("immutable") is not True:
        raise ValueError("STOP_INVALID_FROZEN_FULL_FIT_MANIFEST")
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise ValueError("STOP_MISSING_FROZEN_ARTIFACT_HASHES")
    actual = {name: _sha256(paths[name]) for name in (MODEL_NAME, POLICY_NAME)}
    if any(hashes.get(name) != digest for name, digest in actual.items()):
        raise ValueError("STOP_FROZEN_FULL_FIT_HASH_MISMATCH")
    model, policy = _json(paths[MODEL_NAME]), _json(paths[POLICY_NAME])
    weights = model.get("weights")
    if model.get("feature_dimensions") != FEATURE_DIMENSIONS or not isinstance(weights, list) or len(weights) != FEATURE_DIMENSIONS:
        raise ValueError("STOP_INVALID_FROZEN_FULL_FIT_MODEL")
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in weights):
        raise ValueError("STOP_INVALID_FROZEN_FULL_FIT_MODEL")
    if not isinstance(model.get("bias"), (int, float)) or not math.isfinite(float(model["bias"])):
        raise ValueError("STOP_INVALID_FROZEN_FULL_FIT_MODEL")
    if policy.get("schema_version") != "v228-frozen-policy-v1" or set(policy.get("policy", {})) != REQUIRED_POLICY_FIELDS:
        raise ValueError("STOP_INVALID_FROZEN_FULL_FIT_POLICY")
    return model, policy, {"model": actual[MODEL_NAME], "policy": actual[POLICY_NAME], "manifest": _sha256(paths[MANIFEST_NAME])}


def _existing_challenger(
    output: Path,
    *,
    tenant_id: str,
    run_digest: str,
    base_hashes: dict[str, str],
) -> dict[str, Any]:
    """Verify and reuse a complete challenger from an identical input run."""
    if not output.is_dir():
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    required = set(CHALLENGER_ARTIFACTS) | {MANIFEST_NAME, "status.json"}
    if set(path.name for path in output.iterdir()) != required:
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    try:
        manifest = _json(output / MANIFEST_NAME)
        status = _json(output / "status.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS") from exc
    if (
        manifest.get("schema_version") != "v227-tenant-challenger-manifest-v1"
        or manifest.get("immutable") is not True
        or manifest.get("tenant_id") != tenant_id
        or manifest.get("run_digest") != run_digest
        or manifest.get("base_artifact_hashes") != base_hashes
        or manifest.get("auto_promotion") is not False
        or manifest.get("active_model_mutated") is not False
    ):
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(CHALLENGER_ARTIFACTS):
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    if any(hashes.get(name) != _sha256(output / name) for name in CHALLENGER_ARTIFACTS):
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    if (
        status.get("schema_version") != "v227-feedback-status-v1"
        or status.get("status") != "complete"
        or status.get("promotion") != "NOT_READY"
        or status.get("manifest_sha256") != _sha256(output / MANIFEST_NAME)
    ):
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    coverage = _json(output / "coverage.json")
    return {"status": "complete", "tenant_id": tenant_id, "output": str(output), "manifest": manifest, "coverage": coverage}


def _groups(records: Sequence[dict[str, Any]]) -> list[list[tuple[dict[int, float], int]]]:
    groups: list[list[tuple[dict[int, float], int]]] = []
    for record in records:
        proposals = record["proposals"]
        labels = record["labels"]
        groups.append([(_features(row, proposals), label) for row, label in zip(proposals, labels, strict=True)])
    return groups


def _replay(base: dict[str, Any], groups: Sequence[Sequence[tuple[dict[int, float], int]]], *, epochs: int = 3, learning_rate: float = 0.10) -> CrossClauseLogisticRanker:
    """Continue deterministic updates from the frozen base weights."""
    ranker = CrossClauseLogisticRanker(dimensions=FEATURE_DIMENSIONS, weights=[float(x) for x in base["weights"]], bias=float(base["bias"]))
    for _ in range(epochs):
        for group in groups:
            positives = sum(int(target) for _, target in group)
            negatives = len(group) - positives
            if not positives or not negatives:
                continue
            positive_weight = min(4.0, negatives / max(1, positives))
            gradients = [0.0] * FEATURE_DIMENSIONS
            bias_gradient = 0.0
            total = 0.0
            for features, target in group:
                probability = ranker.probability(features)
                weight = positive_weight if int(target) else 1.0
                error = (probability - float(target)) * weight
                bias_gradient += error
                for index, value in features.items():
                    gradients[index] += error * value
                total += weight
            scale = 1.0 / max(1.0, total)
            for index in range(FEATURE_DIMENSIONS):
                ranker.weights[index] -= learning_rate * (gradients[index] * scale + ranker.l2 * ranker.weights[index])
            ranker.bias -= learning_rate * bias_gradient * scale
    return ranker


def _diagnostic(ranker: CrossClauseLogisticRanker, records: Sequence[dict[str, Any]], threshold: float = 0.5) -> dict[str, Any]:
    tp = fp = fn = 0
    for record in records:
        for proposal, label in zip(record["proposals"], record["labels"], strict=True):
            predicted = ranker.probability(_features(proposal, record["proposals"])) >= threshold
            tp += int(predicted and label)
            fp += int(predicted and not label)
            fn += int((not predicted) and label)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"threshold": threshold, "true_positive": tp, "false_positive": fp, "false_negative": fn, "precision": precision, "recall": recall, "f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0}


def train(
    *, tenant_id: str, feedback_directory: Path = DEFAULT_FEEDBACK_DIRECTORY,
    base_artifact_directory: Path = ARTIFACT_DIR, output: Path | None = None,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    feedback_root = (feedback_directory.resolve() / tenant_id).resolve()
    if feedback_root.parent != feedback_directory.resolve() or not feedback_root.is_dir():
        raise ValueError("STOP_FEEDBACK_TENANT_DIRECTORY_MISSING")
    base_model, base_policy, base_hashes = _load_base(base_artifact_directory)
    source_dir, feedback_dir = feedback_root / "sources", feedback_root / "feedback"
    feedback_paths = sorted(feedback_dir.glob("*.json")) if feedback_dir.is_dir() else []
    if not feedback_paths:
        raise ValueError("STOP_NO_APPROVED_FEEDBACK")
    records: list[dict[str, Any]] = []
    source_hashes: dict[str, Any] = {}
    for feedback_path in feedback_paths:
        if feedback_path.resolve().parent != feedback_dir.resolve():
            raise ValueError("STOP_FEEDBACK_PATH_ESCAPE")
        feedback = _json(feedback_path)
        if feedback.get("schema_version") != "v227-feedback-correction-v1" or feedback.get("tenant_id") != tenant_id:
            raise ValueError("STOP_INVALID_FEEDBACK_RECORD")
        if feedback_path.stem != str(feedback.get("job_id", "")):
            raise ValueError("STOP_FEEDBACK_JOB_ID_MISMATCH")
        stored_hash = feedback.get("feedback_hash")
        unsigned = {key: value for key, value in feedback.items() if key != "feedback_hash"}
        if not isinstance(stored_hash, str) or hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest() != stored_hash:
            raise ValueError("STOP_FEEDBACK_HASH_MISMATCH")
        approval = _approved_at(feedback.get("approval_metadata"))
        source_hash = str(feedback.get("content_hash", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ValueError("STOP_INVALID_SOURCE_HASH")
        source_path = source_dir / f"{source_hash}.json"
        if source_path.resolve().parent != source_dir.resolve():
            raise ValueError("STOP_SOURCE_PATH_ESCAPE")
        source = _json(source_path) if source_path.is_file() else None
        if not isinstance(source, dict) or source.get("tenant_id") != tenant_id or source.get("content_hash") != source_hash:
            raise ValueError("STOP_SOURCE_FEEDBACK_HASH_MISMATCH")
        transcript = source.get("transcript")
        if not isinstance(transcript, str) or not transcript:
            raise ValueError("STOP_TRANSCRIPT_HASH_MISMATCH")
        raw_upload_sha256 = source.get("raw_upload_sha256")
        if not isinstance(raw_upload_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_upload_sha256):
            raise ValueError("STOP_RAW_UPLOAD_HASH_MISSING")
        transcript_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        if transcript_hash != source.get("transcript_sha256") or feedback.get("source_transcript_sha256") != transcript_hash:
            raise ValueError("STOP_TRANSCRIPT_HASH_MISMATCH")
        # The API hashes the bytes as uploaded.  Those bytes can be UTF-16 or
        # a packaged transcript whose decoded text differs byte-for-byte from
        # the UTF-8 transcript used for replay.  Keep this identity check
        # separate from the decoded transcript integrity check above.
        modality = source.get("source_modality", "transcript")
        if modality not in {"transcript", "audio"}:
            raise ValueError("STOP_SOURCE_MODALITY_INVALID")
        identity = [source.get("file_name", "meeting.txt"), source.get("meeting_id", ""), source.get("meeting_title", ""), source.get("meeting_date", ""), raw_upload_sha256]
        if modality == "audio":
            identity.insert(0, modality)
        expected_content_hash = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        if expected_content_hash != source_hash:
            raise ValueError("STOP_SOURCE_CONTENT_HASH_MISMATCH")
        approved = _tasks(feedback.get("corrected_final_tasks"))
        trace, reconstruction = _reconstruct_trace(source)
        proposals = _runtime_rows(str(source.get("meeting_id")), trace, str(source.get("meeting_date")))
        labels, uncovered = _conservative_labels(approved, proposals)
        record = {"job_id": str(feedback["job_id"]), "source_hash": source_hash, "feedback_hash": stored_hash, "proposals": proposals, "labels": labels, "uncovered": uncovered, "approval": approval, "reconstruction": reconstruction}
        records.append(record)
        source_hashes[str(feedback["job_id"])] = {"source_path": source_path.name, "source_sha256": _sha256(source_path), "feedback_sha256": _sha256(feedback_path), "content_hash": source_hash, "source_modality": modality, "raw_upload_sha256": raw_upload_sha256, "transcript_sha256": transcript_hash}
    digest_input = {"tenant_id": tenant_id, "base": base_hashes, "records": source_hashes}
    run_digest = hashlib.sha256(_canonical(digest_input).encode("utf-8")).hexdigest()
    output = (output or feedback_root / "challengers" / run_digest[:24]).resolve()
    if output == Path(base_artifact_directory).resolve() or Path(base_artifact_directory).resolve() in output.parents:
        raise ValueError("STOP_OUTPUT_OVERWRITES_BASE_ARTIFACT")
    if output.exists():
        return _existing_challenger(output, tenant_id=tenant_id, run_digest=run_digest, base_hashes=base_hashes)
    groups = _groups(records)
    challenger = _replay(base_model, groups)
    holdouts: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        fit_groups = _groups(records[:index] + records[index + 1:])
        heldout_ranker = _replay(base_model, fit_groups)
        holdouts.append({"job_id": record["job_id"], "candidate_count": len(record["proposals"]), "approved_task_count": sum(record["labels"]), "uncovered_task_count": len(record["uncovered"]), "base": _diagnostic(CrossClauseLogisticRanker(dimensions=FEATURE_DIMENSIONS, weights=list(base_model["weights"]), bias=float(base_model["bias"])), [record]), "leave_one_meeting_out": _diagnostic(heldout_ranker, [record]), "replay": _diagnostic(challenger, [record])})
    coverage = {"schema_version": "v227-feedback-coverage-v1", "meeting_count": len(records), "candidate_count": sum(len(item["proposals"]) for item in records), "approved_task_count": sum(sum(item["labels"]) + len(item["uncovered"]) for item in records), "covered_task_count": sum(sum(item["labels"]) for item in records), "uncovered_tasks": [{"job_id": item["job_id"], "tasks": item["uncovered"]} for item in records if item["uncovered"]], "reconstruction_modes": sorted({item["reconstruction"] for item in records})}
    model = {"schema_version": "v227-tenant-challenger-model-v1", "immutable": True, "feature_dimensions": FEATURE_DIMENSIONS, "weights": [float(x) for x in challenger.weights], "bias": float(challenger.bias), "base_model_sha256": base_hashes["model"], "replay_meetings": len(records), "epochs": 3, "learning_rate": 0.10}
    policy = {"schema_version": "v227-tenant-challenger-policy-v1", "immutable": True, "base_policy_sha256": base_hashes["policy"], "policy": base_policy}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        _write_json(temporary / MODEL_NAME, model)
        _write_json(temporary / POLICY_NAME, policy)
        _write_json(temporary / "source-hashes.json", {"schema_version": "v227-feedback-source-hashes-v1", "tenant_id": tenant_id, "base_artifact_hashes": base_hashes, "meetings": source_hashes})
        _write_json(temporary / "coverage.json", coverage)
        _write_json(temporary / "holdout-diagnostics.json", {"schema_version": "v227-feedback-holdout-v1", "method": "leave_one_approved_meeting_out", "threshold": 0.5, "meetings": holdouts})
        manifest = {"schema_version": "v227-tenant-challenger-manifest-v1", "immutable": True, "tenant_id": tenant_id, "run_digest": run_digest, "base_artifact_hashes": base_hashes, "artifact_hashes": {name: _sha256(temporary / name) for name in CHALLENGER_ARTIFACTS}, "auto_promotion": False, "active_model_mutated": False, "provider_call_count": 0, "network_calls": 0, "gpu_used": False}
        _write_json(temporary / MANIFEST_NAME, manifest)
        _write_json(temporary / "status.json", {"schema_version": "v227-feedback-status-v1", "status": "complete", "promotion": "NOT_READY", "manifest_sha256": _sha256(temporary / MANIFEST_NAME)})
        # A directory rename is atomic, so readers see either no challenger or
        # the complete package.  If another identical run won the race, reuse
        # and verify its package rather than overwriting it.
        if output.exists():
            return _existing_challenger(output, tenant_id=tenant_id, run_digest=run_digest, base_hashes=base_hashes)
        os.replace(temporary, output)
        temporary = Path()
    except FileExistsError:
        if output.exists():
            return _existing_challenger(output, tenant_id=tenant_id, run_digest=run_digest, base_hashes=base_hashes)
        raise ValueError("STOP_IMMUTABLE_CHALLENGER_EXISTS")
    finally:
        if temporary != Path() and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return {"status": "complete", "tenant_id": tenant_id, "output": str(output), "manifest": manifest, "coverage": coverage}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--feedback-directory", type=Path, default=DEFAULT_FEEDBACK_DIRECTORY)
    parser.add_argument("--base-artifact-directory", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = train(tenant_id=args.tenant_id, feedback_directory=args.feedback_directory, base_artifact_directory=args.base_artifact_directory, output=args.output)
    except Exception as exc:  # noqa: BLE001 - fail closed and avoid private data in output
        print(json.dumps({"status": "STOP", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": result["status"], "tenant_id": result["tenant_id"], "output": result["output"], "meeting_count": result["coverage"]["meeting_count"], "uncovered_task_count": sum(len(item["tasks"]) for item in result["coverage"]["uncovered_tasks"])}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
