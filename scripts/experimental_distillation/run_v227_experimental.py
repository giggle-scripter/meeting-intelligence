"""Run the frozen V2.27 proposal ranker on one new transcript.

This is an opt-in, local CPU experiment.  It always runs V1 with the disabled
AI client, builds the V2.26 candidate union from that fresh V1 trace, and
applies the already-fitted V2.28 promotion artifacts.  It deliberately never
opens validation cases, expected outputs, or provider credentials.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.ai.client import DisabledAiClient  # noqa: E402
from backend.app.dates.resolver import resolve_date_mention  # noqa: E402
from backend.app.models import FinalTask, MeetingInput  # noqa: E402
from backend.app.output.summary_builder import build_summary  # noqa: E402
from backend.app.pipeline import process_meeting  # noqa: E402
from experiments.distilled_proposal_ranker.v2.protocol_bridge_v217 import (  # noqa: E402
    _date_mentions,
)
from scripts.experimental_distillation.run_v222_task_proposal_reranker_oof import (  # noqa: E402
    CrossClauseLogisticRanker,
)
from scripts.experimental_distillation.run_v223_union_proposal_oof import (  # noqa: E402
    UnionProposal,
    _baseline_rows,
    _bridge_rows,
    _features,
)
from scripts.experimental_distillation.run_v226_dev42_template_family_holdout import (  # noqa: E402
    _row_to_proposal,
)
from scripts.experimental_distillation.run_v227_dev42_volume_adaptive_policy import (  # noqa: E402
    _adaptive_select,
)
ARTIFACT_DIR = ROOT / "evaluation/runtime/experimental-distillation-v2/v228-frozen-promotion"
MODEL_NAME = "model.json"
POLICY_NAME = "frozen-policy.json"
MANIFEST_NAME = "manifest.json"
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
DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})"
    r"|(?<!\d)(?P<day2>\d{1,2})[/-](?P<month2>\d{1,2})[/-](?P<year2>20\d{2})(?!\d)"
)


@dataclass(frozen=True)
class RuntimeRow:
    """Minimal runtime candidate row used by the frozen source merge."""

    case_id: str
    task: dict[str, Any]
    source: str
    ordinal: int
    quality: tuple[float, int, int, str]
    provenance: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        def normalize(value: Any) -> str:
            return " ".join(re.findall(r"\w+", str(value or "").casefold().replace("đ", "d")))

        return normalize(self.task.get("task_name")), normalize(self.task.get("assignee"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _artifact_bundle(
    directory: Path,
    *,
    expected_tenant: str | None = None,
    expected_base_hashes: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Load a frozen base or an immutable tenant challenger.

    The candidate construction and selection below deliberately receive only
    the returned model and policy.  This keeps challenger serving on exactly
    the existing runner path while making every artifact load fail closed.
    """

    paths = {name: (directory / name).resolve() for name in (MODEL_NAME, POLICY_NAME, MANIFEST_NAME)}
    if any(not path.is_file() for path in paths.values()):
        missing = [name for name, path in paths.items() if not path.is_file()]
        raise RuntimeError("STOP_MISSING_PRIVATE_ARTIFACT:" + ",".join(missing))
    manifest = _load_json(paths[MANIFEST_NAME])
    schema = manifest.get("schema_version")
    if manifest.get("immutable") is not True:
        raise RuntimeError("STOP_INVALID_V228_MANIFEST" if manifest.get("schema_version") == "v228-manifest-v1" else "STOP_INVALID_RUNTIME_MANIFEST")
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise RuntimeError("STOP_MISSING_V228_ARTIFACT_HASHES" if manifest.get("schema_version") == "v228-manifest-v1" else "STOP_MISSING_RUNTIME_ARTIFACT_HASHES")
    if schema == "v228-manifest-v1":
        expected_artifacts = (MODEL_NAME, POLICY_NAME)
    elif schema == "v227-tenant-challenger-manifest-v1":
        expected_artifacts = CHALLENGER_ARTIFACTS
        if expected_tenant is not None and manifest.get("tenant_id") != expected_tenant:
            raise RuntimeError("STOP_CHALLENGER_TENANT_MISMATCH")
        if manifest.get("auto_promotion") is not False or manifest.get("active_model_mutated") is not False:
            raise RuntimeError("STOP_INVALID_CHALLENGER_POLICY")
        if expected_base_hashes is not None and manifest.get("base_artifact_hashes") != expected_base_hashes:
            raise RuntimeError("STOP_CHALLENGER_BASE_MISMATCH")
    else:
        raise RuntimeError("STOP_INVALID_RUNTIME_MANIFEST")
    if any(not (directory / name).is_file() for name in expected_artifacts):
        raise RuntimeError("STOP_MISSING_RUNTIME_ARTIFACT")
    if set(hashes) != set(expected_artifacts):
        raise RuntimeError("STOP_INVALID_RUNTIME_ARTIFACT_SET")
    actual = {name: _sha256((directory / name).resolve()) for name in expected_artifacts}
    for name, digest in actual.items():
        if hashes.get(name) != digest:
            raise RuntimeError(f"STOP_ARTIFACT_HASH_MISMATCH:{name}")
    model = _load_json(paths[MODEL_NAME])
    policy = _load_json(paths[POLICY_NAME])
    expected_model_schema = "v228-frozen-model-v1" if schema == "v228-manifest-v1" else "v227-tenant-challenger-model-v1"
    expected_policy_schema = "v228-frozen-policy-v1" if schema == "v228-manifest-v1" else "v227-tenant-challenger-policy-v1"
    if model.get("schema_version") != expected_model_schema:
        raise RuntimeError("STOP_INVALID_RUNTIME_MODEL")
    weights = model.get("weights")
    if model.get("feature_dimensions") != 768 or not isinstance(weights, list) or len(weights) != 768:
        raise RuntimeError("STOP_INVALID_RUNTIME_MODEL_DIMENSIONS")
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in weights):
        raise RuntimeError("STOP_INVALID_RUNTIME_MODEL_WEIGHTS")
    if not isinstance(model.get("bias"), (int, float)) or not math.isfinite(float(model["bias"])):
        raise RuntimeError("STOP_INVALID_RUNTIME_MODEL_BIAS")
    if policy.get("schema_version") != expected_policy_schema or not isinstance(policy.get("policy"), dict):
        raise RuntimeError("STOP_INVALID_RUNTIME_POLICY")
    if set(policy["policy"]) != REQUIRED_POLICY_FIELDS:
        raise RuntimeError("STOP_INVALID_RUNTIME_POLICY_FIELDS")
    return model, policy["policy"], {
        "model": actual[MODEL_NAME],
        "policy": actual[POLICY_NAME],
        "manifest": _sha256(paths[MANIFEST_NAME]),
    }


def _meeting_date_from_text(text: str) -> str | None:
    """Read an explicit date only from a meeting/date context line."""

    context_lines = [
        line for line in text.splitlines()
        if re.search(r"\b(?:meeting|date|ngày\s+họp|cuộc\s+họp|họp\s+ngày)\b", line, re.I)
    ]
    for match in DATE_RE.finditer("\n".join(context_lines)):
        groups = match.groupdict()
        if groups.get("year"):
            year, month, day = int(groups["year"]), int(groups["month"]), int(groups["day"])
        else:
            year, month, day = int(groups["year2"]), int(groups["month2"]), int(groups["day2"])
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def _meeting_date(value: str | None, transcript: str) -> tuple[str, str]:
    if value:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("meeting date must be YYYY-MM-DD") from exc
        return parsed.isoformat(), "REQUEST"
    parsed = _meeting_date_from_text(transcript)
    if parsed:
        return parsed, "TRANSCRIPT_CONTEXT"
    raise ValueError("STOP_MEETING_DATE_REQUIRED: pass --meeting-date or include an explicit date in transcript context")


def _intermediate_rows(case_id: str, trace: dict[str, Any], meeting_date: str) -> list[RuntimeRow]:
    mentions = _date_mentions(trace)
    rows: list[RuntimeRow] = []
    for index, raw in enumerate(trace.get("task_states", []) or []):
        if not isinstance(raw, dict) or raw.get("status") not in {"CONFIRMED", "PROVISIONAL"}:
            continue
        task_name = str(raw.get("task_name", ""))
        if not task_name:
            continue
        mention_id = str(raw.get("deadline_mention_id", "") or "")
        mention = mentions.get(mention_id)
        due_date = resolve_date_mention(meeting_date, meeting_date, mention) if mention else ""
        task = {
            "task_name": task_name,
            "assignee": str(raw.get("assignee", "")),
            "start_date": meeting_date,
            "due_date": due_date,
            "due_date_text": str(mention.raw_text) if mention else "",
            "deadline_mention_id": mention_id,
            "status": "Proposed",
        }
        rows.append(RuntimeRow(
            case_id, task, "intermediate_state", index,
            (float(raw.get("confidence", 0.0) or 0.0), 0, -index, task_name),
            ("task_states", str(raw.get("status", ""))),
        ))
    return rows


def _runtime_rows(case_id: str, trace: dict[str, Any], meeting_date: str) -> list[UnionProposal]:
    """Build V2.26's baseline/bridge/intermediate union from runtime fields."""

    rows: list[RuntimeRow] = []
    for index, item in enumerate(_baseline_rows(case_id, trace)):
        rows.append(RuntimeRow(case_id, dict(item.task), "accepted_final", index, (0.95, 0, -index, item.title), ("final_tasks",)))
    rows.extend(_intermediate_rows(case_id, trace, meeting_date))
    for index, item in enumerate(_bridge_rows(case_id, trace, meeting_date)):
        rows.append(RuntimeRow(case_id, dict(item.task), "occurrence_bridge", index, (float(item.confidence), 0, -index, item.title), ("runtime_occurrence_bridge", item.bridge_retrieval)))
    # V2.26's merge order is source-priority then ordinal; it is reproduced
    # here without importing its evaluation runner or opening any labels.
    priority = {"accepted_final": 0, "intermediate_state": 1, "occurrence_bridge": 3}
    ordered = sorted(rows, key=lambda row: (priority.get(row.source, 99), row.ordinal, row.key, row.task.get("task_name", "")))
    output: list[RuntimeRow] = []
    by_key: dict[tuple[str, str], int] = {}
    for row in ordered:
        if not row.key[0]:
            continue
        previous_index = by_key.get(row.key)
        if previous_index is None:
            by_key[row.key] = len(output)
            output.append(row)
            continue
        previous = output[previous_index]
        task = dict(previous.task)
        for field in ("assignee", "start_date", "due_date", "due_date_text", "deadline_mention_id", "status"):
            if not task.get(field) and row.task.get(field):
                task[field] = row.task[field]
        output[previous_index] = RuntimeRow(
            previous.case_id, task, previous.source, previous.ordinal, previous.quality,
            tuple(dict.fromkeys(previous.provenance + row.provenance)),
        )
    proposals = [_row_to_proposal(row) for row in output]
    ranked = sorted(proposals, key=lambda item: item.proposal_id)
    confidence_order = sorted(ranked, key=lambda item: (-item.confidence, item.proposal_id))
    ranks = {item.proposal_id: index + 1 for index, item in enumerate(confidence_order)}
    return [
        UnionProposal(**{**item.__dict__, "source_rank": index + 1, "confidence_rank": ranks[item.proposal_id]})
        for index, item in enumerate(ranked)
    ]


def run(
    transcript_path: Path,
    *,
    output_path: Path | None = None,
    meeting_date: str | None = None,
    meeting_id: str | None = None,
    meeting_title: str | None = None,
    artifact_directory: Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    source = transcript_path.resolve()
    if not source.is_file() or source.suffix.lower() not in {".txt", ".vtt", ".srt"}:
        raise ValueError("transcript must be an existing .txt, .vtt, or .srt file")
    transcript = source.read_text(encoding="utf-8-sig")
    effective_date, date_source = _meeting_date(meeting_date, transcript)
    model, policy, artifact_hashes = _artifact_bundle(artifact_directory.resolve())
    content_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    effective_id = meeting_id or f"experimental-{content_hash[:16]}"
    effective_title = meeting_title or source.stem
    meeting = MeetingInput(
        meeting_id=effective_id,
        meeting_title=effective_title,
        meeting_date=effective_date,
        meeting_date_source=date_source,
        transcript_raw=transcript,
        file_name=source.name,
    )
    with tempfile.TemporaryDirectory(prefix="v227-experimental-trace-") as temp_dir:
        result = process_meeting(
            meeting,
            ai_client=DisabledAiClient(),
            trace_enabled=True,
            trace_directory=temp_dir,
            summary_topic=effective_title,
            # These deterministic V1 shadow records are the runtime-only
            # inputs used by the frozen V2.26 candidate expansion.  Shadow
            # mode does not call a provider and does not change V1's public
            # extraction result.
            action_candidate_builder_mode="shadow",
            commitment_router_mode="shadow",
        )
        traces = sorted(Path(temp_dir).glob("*.json"))
        if len(traces) != 1:
            raise RuntimeError(f"STOP_FRESH_V1_TRACE:{len(traces)}")
        trace_path = traces[0]
        trace = _load_json(trace_path)
        if trace.get("pipeline_version") != "v1" or str(trace.get("meeting_id")) != effective_id:
            raise RuntimeError("STOP_INVALID_FRESH_V1_TRACE")
        pool = _runtime_rows(effective_id, trace, effective_date)
        ranker = CrossClauseLogisticRanker(
            dimensions=768,
            weights=[float(value) for value in model["weights"]],
            bias=float(model["bias"]),
        )
        scores = [
            ranker.probability(_features(item, pool))
            * (float(policy["bridge_weight"]) if item.origin == "bridge" else float(policy["intermediate_weight"]) if item.origin == "intermediate" else 1.0)
            for item in pool
        ]
        selected, policy_context, adaptive = _adaptive_select(pool, scores, policy)
        tasks = []
        for item in selected:
            task = dict(item.task)
            task.setdefault("evidence", item.context or item.title)
            tasks.append({key: task.get(key, "") for key in ("task_name", "assignee", "start_date", "due_date", "due_date_text", "evidence", "status")})
        selected_models = [FinalTask(**task) for task in tasks]
        selected_summary = build_summary(
            result.meeting_title,
            selected_models,
            meeting_note_present=False,
        )
        experimental_diagnostics = {
            "pipeline_version": "v227_experimental",
            "experimental_not_validated": True,
            "candidate_source": "V2.26 final_plus_bridge_plus_intermediate",
            "candidate_count": len(pool),
            "selected_count": len(selected),
            "provider_call_count": 0,
            "adaptive": adaptive,
            "policy_context": policy_context,
            "artifact_hashes": artifact_hashes,
            "trace_sha256": _sha256(trace_path),
            "full_fit_model_reused": True,
            "promotion_recommendation": "NOT_READY",
        }
        payload = {
            "meeting_title": result.meeting_title,
            "summary": selected_summary,
            "tasks": tasks,
            "diagnostics": {
                **result.diagnostics.__dict__,
                "ai_provider_enabled": False,
                "ai_provider_call_count": 0,
                "meeting_date_source": date_source,
                "effective_meeting_date": effective_date,
                "experimental_not_validated": True,
                "candidate_count": len(pool),
                "selected_count": len(selected),
                "provider_call_count": 0,
                "artifact_hashes": artifact_hashes,
            },
            "unresolved_window_ids": list(result.unresolved_window_ids),
            "experimental": experimental_diagnostics,
        }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path, help="new .txt, .vtt, or .srt transcript")
    parser.add_argument("--output", type=Path, required=True, help="standard meeting/task JSON output")
    parser.add_argument("--meeting-date", help="meeting date (YYYY-MM-DD); otherwise parse explicit transcript context")
    parser.add_argument("--meeting-id")
    parser.add_argument("--meeting-title")
    parser.add_argument("--artifact-directory", type=Path, default=ARTIFACT_DIR)
    args = parser.parse_args()
    try:
        payload = run(
            args.transcript,
            output_path=args.output,
            meeting_date=args.meeting_date,
            meeting_id=args.meeting_id,
            meeting_title=args.meeting_title,
            artifact_directory=args.artifact_directory,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with one diagnostic
        print(json.dumps({"status": "STOP", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "complete", "candidate_count": payload["experimental"]["candidate_count"], "selected_count": payload["experimental"]["selected_count"], "provider_call_count": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
