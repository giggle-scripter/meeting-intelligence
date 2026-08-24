"""Run a fixture dataset against the local pipeline or a deployed API."""

import argparse
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.evaluation import aggregate_results, compare_case
from backend.app.ai import OpenAiResponsesClient
from backend.app.config import Settings
from backend.app.ingestion import build_meeting_package
from backend.app.models import MeetingInput, MeetingNoteInput
from backend.app.pipeline import process_meeting_by_version


PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "v1")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1-ledger-proposal-v1")
MODEL_VERSION = os.getenv("OPENAI_MODEL", "deterministic")
REASONING_EFFORT_VERSION = os.getenv("OPENAI_REASONING_EFFORT", "not_applicable")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class FatalBenchmarkError(RuntimeError):
    """A provider/account failure for which continuing would waste every case."""


def _local_openai_client() -> OpenAiResponsesClient:
    """Build the same OpenAI Responses client used by the FastAPI runtime."""

    settings = Settings.from_env()
    if not settings.openai_api_key:
        raise FatalBenchmarkError(
            "OPENAI_API_KEY is required when --local-openai is enabled"
        )
    return OpenAiResponsesClient(
        settings.openai_api_key,
        settings.openai_model,
        settings.openai_reasoning_effort,
        settings.ai_timeout_seconds,
        settings.ai_fallback_debug,
        settings.openai_input_usd_per_1m,
        settings.openai_cached_input_usd_per_1m,
        settings.openai_output_usd_per_1m,
    )


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After", "").strip()
    try:
        return max(0.0, min(float(value), 30.0))
    except ValueError:
        return min(2.0**attempt, 8.0)


def _raise_for_benchmark_status(response: httpx.Response) -> None:
    body = response.text[:2000].casefold()
    if "credit_balance_exhausted" in body or "insufficient_quota" in body:
        raise FatalBenchmarkError("Provider credit balance is exhausted; benchmark aborted")
    response.raise_for_status()


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    **kwargs,
) -> httpx.Response:
    """Retry connect/DNS/timeouts and bounded 429/5xx responses only."""

    for attempt in range(attempts):
        try:
            response = client.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException):
            if attempt == attempts - 1:
                raise
            time.sleep(min(2.0**attempt, 8.0))
            continue
        if response.status_code not in RETRYABLE_STATUS_CODES:
            _raise_for_benchmark_status(response)
            return response
        _raise_for_benchmark_status(response) if attempt == attempts - 1 else None
        time.sleep(_retry_after_seconds(response, attempt))
    raise RuntimeError("HTTP retry loop exited unexpectedly")


def _load_case(case_dir: Path) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    metadata_path = case_dir / "metadata.json"
    transcript_path = next(iter(case_dir.glob("transcript.*")), None)
    expected_path = case_dir / "expected_output.json"
    if not metadata_path.exists() or transcript_path is None or not expected_path.exists():
        return None
    return (
        json.loads(metadata_path.read_text(encoding="utf-8")),
        transcript_path.read_text(encoding="utf-8-sig"),
        transcript_path.name,
        json.loads(expected_path.read_text(encoding="utf-8")),
    )


def _request_payload(metadata: dict[str, Any], transcript: str, file_name: str, note: str | None = None) -> dict[str, Any]:
    payload = {
        "meeting_id": metadata["case_id"],
        "meeting_title": metadata["meeting_title"],
        "meeting_date": metadata["meeting_date"],
        "file_name": file_name,
        "transcript": transcript,
        "speaker_aliases": metadata.get("speaker_aliases", {}),
    }
    if note:
        payload["meeting_note"] = {"content": note, "author": "Thư ký", "source": "SECRETARY"}
    return payload


def _run_api(endpoint: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    with httpx.Client(timeout=timeout) as client:
        response = _request_with_retry(client, "POST", endpoint, json=payload, headers=headers)
    return response.json()


def _run_job_api(
    endpoint: str,
    api_key: str,
    metadata: dict[str, Any],
    transcript: str,
    file_name: str,
    timeout: float,
    poll_interval: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Submit a file-processing job and poll without holding one long request open."""

    headers = {
        "Content-Type": "application/octet-stream",
        "X-File-Name": f"{metadata['case_id']}-{file_name}",
        "X-Meeting-Id": metadata["case_id"],
        "X-Meeting-Date": metadata["meeting_date"],
    }
    if api_key:
        headers["X-API-Key"] = api_key

    started = time.monotonic()
    submit_timeout = min(30.0, timeout)
    with httpx.Client(timeout=submit_timeout) as client:
        response = _request_with_retry(
            client,
            "POST",
            endpoint,
            content=transcript.encode("utf-8"),
            headers=headers,
        )
        submission = response.json()
        job_id = str(submission.get("job_id", ""))
        if not job_id:
            raise RuntimeError("Job submission response does not contain job_id")

        job_base = endpoint.rsplit("/process-file", 1)[0]
        status_url = f"{job_base}/{job_id}"
        poll_count = 0
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Job {job_id} did not finish within {timeout:.0f} seconds"
                )

            time.sleep(min(poll_interval, timeout - elapsed))
            poll_count += 1
            status_response = _request_with_retry(
                client,
                "GET",
                status_url,
                headers={"X-API-Key": api_key} if api_key else {},
            )
            job = status_response.json()
            job_status = job.get("status")
            if job_status == "succeeded":
                result = job.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"Job {job_id} succeeded without a result object")
                return result, {
                    "job_id": job_id,
                    "status": job_status,
                    "poll_count": poll_count,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            if job_status == "failed":
                error = str(job.get("error") or "unknown error")
                if "credit balance" in error.casefold() or "credit_balance_exhausted" in error.casefold():
                    raise FatalBenchmarkError(error)
                raise RuntimeError(f"Job {job_id} failed: {error}")
            if job_status not in {"queued", "running"}:
                raise RuntimeError(f"Job {job_id} returned unexpected status: {job_status!r}")


def _classify_ai_route(diagnostics: dict[str, Any]) -> str:
    """Classify whether a case stayed deterministic or required AI assistance."""
    ai_windows = int(diagnostics.get("ai_window_count", 0))
    if ai_windows == 0:
        return "rule_only"
    if int(diagnostics.get("ai_provider_call_count", 0)) == 0:
        return (
            "ai_fallback_skipped_no_candidate"
            if bool(diagnostics.get("ai_provider_enabled", False))
            else "ai_fallback_not_configured"
        )
    if int(diagnostics.get("ai_fallback_error_count", 0)) > 0:
        return "ai_fallback_failed"
    if int(diagnostics.get("unresolved_window_count", 0)) > 0:
        return "ai_fallback_unresolved"
    return "ai_fallback_resolved"


def _write_resume_checkpoint(
    path: Path | None,
    comparisons: list,
    execution_details: dict[str, dict[str, Any]],
    case_execution: list[dict[str, Any]],
    job_execution: list[dict[str, Any]],
) -> None:
    if path is None:
        return
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": MODEL_VERSION,
        "reasoning_effort": REASONING_EFFORT_VERSION,
        "completed_case_count": len(comparisons),
        "cases": [asdict(item) for item in comparisons],
        "case_diagnostics": execution_details,
        "case_execution": case_execution,
        "job_execution": job_execution,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 4:
                # A checkpoint is resumability metadata, not the benchmark
                # result. Windows indexers/AV can briefly hold the target;
                # do not discard completed evaluation work after bounded
                # atomic-replace retries.
                print(
                    f"WARNING: checkpoint replace remained locked: {path}",
                    file=sys.stderr,
                )
                temporary.unlink(missing_ok=True)
                break
            time.sleep(0.05 * (attempt + 1))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    endpoint_group = parser.add_mutually_exclusive_group()
    endpoint_group.add_argument(
        "--endpoint",
        help="Synchronous JSON POST endpoint. Omit to run the Python pipeline in-process.",
    )
    endpoint_group.add_argument(
        "--job-endpoint",
        help=(
            "Asynchronous file job endpoint, for example "
            "http://127.0.0.1:8010/api/v1/meetings/jobs/process-file."
        ),
    )
    endpoint_group.add_argument(
        "--local-openai",
        action="store_true",
        help=(
            "Run in-process with the OpenAI Responses client configured from "
            "OPENAI_* environment variables; no API server is required."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("POWER_AUTOMATE_API_KEY", ""),
        help="X-API-Key value. Defaults to POWER_AUTOMATE_API_KEY.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3600.0,
        help="Seconds for one synchronous request or the full submit-and-poll job lifecycle.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=15.0,
        help="Seconds between job status polls when --job-endpoint is used.",
    )
    parser.add_argument(
        "--full-timeout",
        type=float,
        default=0.0,
        help="Maximum seconds for the full benchmark; zero disables this limit.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed cases from an existing --report and run only the remainder.",
    )
    parser.add_argument("--report", type=Path, help="Optional JSON report path.")
    parser.add_argument("--pipeline-version", choices=("v1", "v2", "shadow"), default=PIPELINE_VERSION)
    parser.add_argument(
        "--min-task-precision",
        type=float,
        default=0.90,
        help="Minimum task precision for the V2 quality gate.",
    )
    parser.add_argument(
        "--min-task-recall",
        type=float,
        default=0.85,
        help="Minimum task recall for the V2 quality gate.",
    )
    parser.add_argument("--context-mode", choices=("off", "assist", "shadow"), default="assist")
    parser.add_argument("--ai-cost-gate-mode", choices=("off", "enforce"), default="off")
    parser.add_argument("--ai-cost-max-provider-calls", type=int, default=3)
    parser.add_argument("--ai-cost-max-payload-characters", type=int, default=20_000)
    parser.add_argument("--ai-cost-max-estimated-usd", type=float)
    parser.add_argument(
        "--action-classifier-mode",
        choices=("off", "shadow", "assist"),
        default="off",
        help="Local mode only; API modes use server configuration.",
    )
    parser.add_argument(
        "--action-classifier-model-path",
        type=Path,
        default=Path("data/ml/action-classifier/model/action-clf-v1.json"),
        help="Portable classifier artifact used by local shadow evaluation.",
    )
    parser.add_argument(
        "--action-candidate-builder-mode", choices=("off", "shadow"), default="off"
    )
    parser.add_argument(
        "--action-candidate-builder-version", default="action-candidate-v2"
    )
    parser.add_argument(
        "--commitment-router-mode", choices=("off", "shadow", "assist"), default="off"
    )
    parser.add_argument(
        "--commitment-router-version", default="commitment-router-v2"
    )
    parser.add_argument(
        "--commitment-router-active-types",
        default="DIRECT_ASSIGNMENT,SELF_COMMITMENT",
        help="Comma-separated positive authority types for local create.",
    )
    parser.add_argument(
        "--action-canonicalization-mode", choices=("off", "shadow"), default="off"
    )
    parser.add_argument(
        "--action-canonicalization-version", default="action-canonicalization-v2"
    )
    parser.add_argument(
        "--recap-reconciliation-mode", choices=("off", "shadow"), default="off"
    )
    parser.add_argument("--owner-grounding-mode", choices=("off", "shadow"), default="off")
    parser.add_argument("--deadline-grounding-mode", choices=("off", "shadow"), default="off")
    parser.add_argument(
        "--candidate-router-mode",
        choices=("off", "shadow", "assist"),
        default="off",
        help="Local router; its non-off mode must match the classifier mode.",
    )
    parser.add_argument("--task-create-proposal", action="store_true")
    parser.add_argument("--ai-create-proposal", action="store_true")
    parser.add_argument("--ai-create-max-proposals", type=int, default=3)
    parser.add_argument(
        "--ai-quality-uplift-mode", choices=("off", "shadow"), default="off"
    )
    parser.add_argument(
        "--task-semantic-linker-mode",
        choices=("off", "shadow"),
        default="off",
    )
    parser.add_argument(
        "--task-link-embedding-model-name",
        default=os.getenv(
            "EMBEDDING_MODEL_NAME",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
    )
    parser.add_argument("--task-link-semantic-weight", type=float, default=0.55)
    parser.add_argument("--task-link-lexical-weight", type=float, default=0.20)
    parser.add_argument("--task-link-topic-weight", type=float, default=0.10)
    parser.add_argument("--task-link-owner-weight", type=float, default=0.10)
    parser.add_argument("--task-link-recency-weight", type=float, default=0.05)
    parser.add_argument("--task-link-strong-threshold", type=float, default=0.78)
    parser.add_argument("--task-link-min-margin", type=float, default=0.12)
    parser.add_argument("--task-link-ai-threshold", type=float, default=0.60)
    parser.add_argument("--task-link-recency-horizon", type=int, default=200)
    parser.add_argument("--task-link-top-k", type=int, default=5)
    parser.add_argument(
        "--task-link-scoring-version", default="task-link-scoring-v1"
    )
    parser.add_argument(
        "--context-retrieval-mode", choices=("off", "shadow"), default="off"
    )
    parser.add_argument("--context-max-clauses", type=int, default=30)
    parser.add_argument("--context-max-characters", type=int, default=12_000)
    parser.add_argument("--context-max-tasks", type=int, default=5)
    parser.add_argument("--context-local-before", type=int, default=3)
    parser.add_argument("--context-local-after", type=int, default=5)
    parser.add_argument("--context-max-topic-clauses", type=int, default=12)
    parser.add_argument("--context-max-topics", type=int, default=3)
    parser.add_argument("--context-max-history-events", type=int, default=3)
    parser.add_argument("--context-topic-boundary-threshold", type=float, default=0.42)
    parser.add_argument("--context-topic-smoothing-window", type=int, default=3)
    parser.add_argument("--context-retrieval-version", default="context-retriever-v1")
    parser.add_argument(
        "--ai-mutation-router-mode", choices=("off", "shadow", "assist"), default="off"
    )
    parser.add_argument(
        "--ai-mutation-prompt-version", default="mutation-resolution-v2"
    )
    parser.add_argument("--ai-mutation-min-confidence", type=float, default=0.70)
    parser.add_argument(
        "--note-dual-view-mode", choices=("off", "shadow", "assist"), default="off"
    )
    parser.add_argument("--note-claim-max-transcript-clauses", type=int, default=8)
    parser.add_argument("--note-claim-max-topics", type=int, default=3)
    parser.add_argument("--note-claim-grounding-threshold", type=float, default=0.72)
    parser.add_argument("--note-claim-grounding-margin", type=float, default=0.12)
    parser.add_argument("--note-dual-view-version", default="note-dual-view-v1")
    parser.add_argument(
        "--temporal-semantics-mode", choices=("off", "shadow", "assist"), default="off"
    )
    parser.add_argument("--temporal-parser-version", default="temporal-parser-v1")
    parser.add_argument("--temporal-working-day-policy", default="weekdays-only-v1")
    parser.add_argument("--temporal-min-confidence", type=float, default=1.0)
    parser.add_argument("--action-clear-threshold", type=float, default=0.82)
    parser.add_argument("--action-ai-threshold", type=float, default=0.45)
    parser.add_argument(
        "--candidate-threshold-version",
        default="candidate-router-thresholds-v1",
    )
    parser.add_argument("--without-meeting-notes", action="store_true")
    parser.add_argument(
        "--reviewed-only",
        action="store_true",
        help="Run only cases whose metadata marks ground_truth.available=true.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run one case ID. Repeat the option to select multiple cases.",
    )
    parser.add_argument(
        "--case-csv",
        type=Path,
        help="Run case IDs listed in a CSV column named case_id.",
    )
    args = parser.parse_args()
    if (
        args.candidate_router_mode != "off"
        and args.action_classifier_mode != args.candidate_router_mode
    ):
        parser.error(
            "non-off candidate router and action classifier modes must match"
        )
    if args.task_create_proposal and args.candidate_router_mode != "assist":
        parser.error("--task-create-proposal requires --candidate-router-mode assist")
    if args.ai_create_proposal and not args.task_create_proposal:
        parser.error("--ai-create-proposal requires --task-create-proposal")
    if args.ai_quality_uplift_mode == "shadow" and (
        args.action_classifier_mode != "shadow"
        or args.candidate_router_mode != "shadow"
        or args.action_candidate_builder_mode != "shadow"
        or args.commitment_router_mode != "shadow"
    ):
        parser.error(
            "--ai-quality-uplift-mode shadow requires classifier, candidate, "
            "action-candidate, and commitment-router shadow modes"
        )
    if (
        args.context_retrieval_mode == "shadow"
        and args.task_semantic_linker_mode != "shadow"
    ):
        parser.error(
            "--context-retrieval-mode shadow requires --task-semantic-linker-mode shadow"
        )
    if (
        args.ai_mutation_router_mode == "shadow"
        and (args.task_semantic_linker_mode != "shadow" or args.context_retrieval_mode != "shadow")
    ):
        parser.error("--ai-mutation-router-mode shadow requires semantic/context shadow")
    if (
        args.ai_mutation_router_mode == "assist"
        and (args.candidate_router_mode != "assist" or args.task_semantic_linker_mode != "shadow" or args.context_retrieval_mode != "shadow")
    ):
        parser.error("--ai-mutation-router-mode assist requires candidate assist and semantic/context shadow")
    if args.note_dual_view_mode != "off" and args.context_mode == "off":
        parser.error("--note-dual-view-mode requires --context-mode shadow or assist")
    if args.temporal_working_day_policy != "weekdays-only-v1":
        parser.error("--temporal-working-day-policy must be weekdays-only-v1")
    if args.temporal_min_confidence != 1.0:
        parser.error("--temporal-min-confidence must be exactly 1.0")
    if args.ai_cost_max_provider_calls <= 0 or args.ai_cost_max_payload_characters <= 0:
        parser.error("AI cost gate limits must be positive")
    if args.ai_cost_max_estimated_usd is not None and args.ai_cost_max_estimated_usd < 0:
        parser.error("--ai-cost-max-estimated-usd must be non-negative")
    checkpoint_path = (
        args.report.with_suffix(args.report.suffix + ".checkpoint.json")
        if args.report
        else None
    )
    selected_case_ids = set(args.case_id)
    if args.case_csv:
        with args.case_csv.open(encoding="utf-8-sig", newline="") as handle:
            selected_case_ids.update(
                row["case_id"]
                for row in csv.DictReader(handle)
                if row.get("case_id")
            )
    comparisons = []
    reviewed_comparisons = []
    exploratory_comparisons = []
    execution_details: dict[str, dict[str, Any]] = {}
    case_execution: list[dict[str, Any]] = []
    job_execution: list[dict[str, Any]] = []
    execution_errors: list[dict[str, str]] = []
    discovered_case_count = 0
    unreviewed_ground_truth: list[str] = []
    suspect_empty_ground_truth: list[str] = []
    benchmark_started = time.monotonic()
    resumed_case_ids: set[str] = set()
    resume_source = None
    if args.resume and args.report:
        if args.report.exists():
            resume_source = args.report
        elif checkpoint_path and checkpoint_path.exists():
            resume_source = checkpoint_path
    if resume_source:
        previous = json.loads(resume_source.read_text(encoding="utf-8"))
        from backend.app.evaluation import CaseComparison

        for item in previous.get("cases", []):
            comparison = CaseComparison(**item)
            comparisons.append(comparison)
            resumed_case_ids.add(comparison.case_id)
        execution_details.update(previous.get("case_diagnostics", {}))
        case_execution.extend(previous.get("case_execution", []))
        job_execution.extend(previous.get("job_execution", []))
        print(f"Resuming with {len(resumed_case_ids)} completed case(s)")

    candidate_case_dirs = [
        path
        for path in sorted(path for path in args.dataset.iterdir() if path.is_dir())
        if not selected_case_ids or path.name in selected_case_ids
    ]
    expected_case_count = len(candidate_case_dirs)

    for case_dir in candidate_case_dirs:
        if args.full_timeout and time.monotonic() - benchmark_started >= args.full_timeout:
            execution_errors.append({"case_id": case_dir.name, "error": "FULL_BENCHMARK_TIMEOUT"})
            break
        loaded = _load_case(case_dir)
        if loaded is None:
            continue
        discovered_case_count += 1
        metadata, transcript, file_name, expected = loaded
        is_reviewed = metadata.get("ground_truth", {}).get("available", False)
        if metadata["case_id"] in resumed_case_ids:
            comparison = next(item for item in comparisons if item.case_id == metadata["case_id"])
            (reviewed_comparisons if is_reviewed else exploratory_comparisons).append(comparison)
            continue
        if not is_reviewed:
            unreviewed_ground_truth.append(metadata["case_id"])
            if args.reviewed_only:
                continue
        note_path = case_dir / "meeting_note.txt"
        note = None if args.without_meeting_notes or not note_path.exists() else note_path.read_text(encoding="utf-8-sig")
        payload = _request_payload(metadata, transcript, file_name, note)
        try:
            if args.job_endpoint:
                actual, job_info = _run_job_api(
                    args.job_endpoint,
                    args.api_key,
                    metadata,
                    build_meeting_package(transcript, note),
                    file_name,
                    args.timeout,
                    args.poll_interval,
                )
                job_execution.append({"case_id": metadata["case_id"], **job_info})
            elif args.endpoint:
                actual = _run_api(args.endpoint, args.api_key, payload, args.timeout)
            else:
                actual = asdict(
                    process_meeting_by_version(
                        MeetingInput(
                            metadata["case_id"],
                            metadata["meeting_title"],
                            metadata["meeting_date"],
                            transcript,
                            file_name,
                            MeetingNoteInput(note, "Thư ký", "SECRETARY") if note else None,
                        ),
                        pipeline_version=args.pipeline_version,
                        ai_client=(
                            _local_openai_client() if args.local_openai else None
                        ),
                        ai_cost_gate_mode=args.ai_cost_gate_mode,
                        ai_cost_max_provider_calls_per_meeting=(
                            args.ai_cost_max_provider_calls
                        ),
                        ai_cost_max_payload_characters=(
                            args.ai_cost_max_payload_characters
                        ),
                        ai_cost_max_estimated_usd_per_meeting=(
                            args.ai_cost_max_estimated_usd
                        ),
                        meeting_context_mode=args.context_mode,
                        action_classifier_mode=args.action_classifier_mode,
                        action_classifier_model_path=str(
                            args.action_classifier_model_path
                        ),
                        action_candidate_builder_mode=args.action_candidate_builder_mode,
                        action_candidate_builder_version=args.action_candidate_builder_version,
                        commitment_router_mode=args.commitment_router_mode,
                        commitment_router_version=args.commitment_router_version,
                        commitment_router_active_types=tuple(
                            value.strip().upper()
                            for value in args.commitment_router_active_types.split(",")
                            if value.strip()
                        ),
                        action_canonicalization_mode=args.action_canonicalization_mode,
                        action_canonicalization_version=args.action_canonicalization_version,
                        recap_reconciliation_mode=args.recap_reconciliation_mode,
                        owner_grounding_mode=args.owner_grounding_mode,
                        deadline_grounding_mode=args.deadline_grounding_mode,
                        candidate_router_mode=args.candidate_router_mode,
                        action_clear_threshold=args.action_clear_threshold,
                        action_ai_threshold=args.action_ai_threshold,
                        candidate_threshold_version=args.candidate_threshold_version,
                        task_create_proposal_enabled=args.task_create_proposal,
                        ai_create_proposal_enabled=args.ai_create_proposal,
                        ai_create_max_proposals_per_meeting=(
                            args.ai_create_max_proposals
                        ),
                        ai_quality_uplift_mode=args.ai_quality_uplift_mode,
                        task_semantic_linker_mode=args.task_semantic_linker_mode,
                        task_link_embedding_model_name=(
                            args.task_link_embedding_model_name
                        ),
                        task_link_semantic_weight=args.task_link_semantic_weight,
                        task_link_lexical_weight=args.task_link_lexical_weight,
                        task_link_topic_weight=args.task_link_topic_weight,
                        task_link_owner_weight=args.task_link_owner_weight,
                        task_link_recency_weight=args.task_link_recency_weight,
                        task_link_strong_threshold=args.task_link_strong_threshold,
                        task_link_min_margin=args.task_link_min_margin,
                        task_link_ai_threshold=args.task_link_ai_threshold,
                        task_link_recency_horizon_clauses=(
                            args.task_link_recency_horizon
                        ),
                        task_link_top_k=args.task_link_top_k,
                        task_link_scoring_version=args.task_link_scoring_version,
                        context_retrieval_mode=args.context_retrieval_mode,
                        context_max_clauses=args.context_max_clauses,
                        context_max_characters=args.context_max_characters,
                        context_max_tasks=args.context_max_tasks,
                        context_local_before=args.context_local_before,
                        context_local_after=args.context_local_after,
                        context_max_topic_clauses=args.context_max_topic_clauses,
                        context_max_topics=args.context_max_topics,
                        context_max_history_events_per_task=(
                            args.context_max_history_events
                        ),
                        context_topic_boundary_threshold=(
                            args.context_topic_boundary_threshold
                        ),
                        context_topic_smoothing_window=(
                            args.context_topic_smoothing_window
                        ),
                        context_retrieval_version=args.context_retrieval_version,
                        ai_mutation_router_mode=args.ai_mutation_router_mode,
                        ai_mutation_prompt_version=args.ai_mutation_prompt_version,
                        ai_mutation_min_confidence=args.ai_mutation_min_confidence,
                        note_dual_view_mode=args.note_dual_view_mode,
                        note_claim_max_transcript_clauses=args.note_claim_max_transcript_clauses,
                        note_claim_max_topics=args.note_claim_max_topics,
                        note_claim_grounding_threshold=args.note_claim_grounding_threshold,
                        note_claim_grounding_margin=args.note_claim_grounding_margin,
                        note_dual_view_version=args.note_dual_view_version,
                        temporal_semantics_mode=args.temporal_semantics_mode,
                        temporal_parser_version=args.temporal_parser_version,
                        temporal_working_day_policy=args.temporal_working_day_policy,
                        temporal_min_confidence=args.temporal_min_confidence,
                    )
                )
        except FatalBenchmarkError as exc:
            execution_errors.append({"case_id": metadata["case_id"], "error": str(exc)})
            print(f"{metadata['case_id']}: FATAL: {exc}")
            break
        except (httpx.HTTPError, RuntimeError, TimeoutError) as exc:
            execution_errors.append({"case_id": metadata["case_id"], "error": str(exc)})
            print(f"{metadata['case_id']}: ERROR: {exc}")
            continue
        comparison = compare_case(metadata["case_id"], expected, actual)
        diagnostics = actual.get("diagnostics", {})
        execution_details[metadata["case_id"]] = diagnostics
        case_execution.append(
            {
                "case_id": metadata["case_id"],
                "route": _classify_ai_route(diagnostics),
                "ai_window_count": int(diagnostics.get("ai_window_count", 0)),
                "ai_provider_call_count": int(
                    diagnostics.get("ai_provider_call_count", 0)
                ),
                "ai_event_count": int(diagnostics.get("ai_event_count", 0)),
                "ai_fallback_error_count": int(
                    diagnostics.get("ai_fallback_error_count", 0)
                ),
                "unresolved_window_count": int(
                    diagnostics.get("unresolved_window_count", 0)
                ),
                "unresolved_mutation_count": int(
                    diagnostics.get("unresolved_mutation_count", 0)
                ),
                "provisional_task_created_count": int(
                    diagnostics.get("provisional_task_created_count", 0)
                ),
                "provisional_task_promoted_count": int(
                    diagnostics.get("provisional_task_promoted_count", 0)
                ),
                "provisional_promotion_blocked_count": int(
                    diagnostics.get("provisional_promotion_blocked_count", 0)
                ),
                "ambiguous_identity_mutation_blocked_count": int(
                    diagnostics.get(
                        "ambiguous_identity_mutation_blocked_count", 0
                    )
                ),
                "sibling_identity_split_count": int(
                    diagnostics.get("sibling_identity_split_count", 0)
                ),
                "ai_contract_rejection_count": int(
                    diagnostics.get("ai_contract_rejection_count", 0)
                ),
                "ai_structural_contract_rejection_count": int(
                    diagnostics.get("ai_structural_contract_rejection_count", 0)
                ),
                "ai_semantic_rejection_count": int(
                    diagnostics.get("ai_semantic_rejection_count", 0)
                ),
                "ai_unknown_task_id_rejection_count": int(
                    diagnostics.get("ai_unknown_task_id_rejection_count", 0)
                ),
                "ai_invalid_source_clause_rejection_count": int(
                    diagnostics.get("ai_invalid_source_clause_rejection_count", 0)
                ),
                "ai_invalid_anchor_clause_rejection_count": int(
                    diagnostics.get("ai_invalid_anchor_clause_rejection_count", 0)
                ),
                "ai_non_concrete_action_rejection_count": int(
                    diagnostics.get("ai_non_concrete_action_rejection_count", 0)
                ),
                "ai_invalid_assignee_rejection_count": int(
                    diagnostics.get("ai_invalid_assignee_rejection_count", 0)
                ),
                "unauthorized_creation_blocked_count": int(
                    diagnostics.get("unauthorized_creation_blocked_count", 0)
                ),
                "ledger_unknown_task_id_rejection_count": int(
                    diagnostics.get("ledger_unknown_task_id_rejection_count", 0)
                ),
                "ai_quality_create_candidate_count": int(
                    diagnostics.get("ai_quality_create_candidate_count", 0)
                ),
                "ai_quality_create_eligible_count": int(
                    diagnostics.get("ai_quality_create_eligible_count", 0)
                ),
                "ai_quality_create_selected_count": int(
                    diagnostics.get("ai_quality_create_selected_count", 0)
                ),
                "ai_quality_create_exclusion_reasons": diagnostics.get(
                    "ai_quality_create_exclusion_reasons", {}
                ),
            }
        )
        if (
            not expected.get("tasks")
            and actual.get("tasks")
            and any(
                marker in transcript.casefold()
                for marker in ("chốt lại", "tổng kết", "recap", "final list")
            )
        ):
            suspect_empty_ground_truth.append(metadata["case_id"])
        comparisons.append(comparison)
        if is_reviewed:
            reviewed_comparisons.append(comparison)
        else:
            exploratory_comparisons.append(comparison)
        _write_resume_checkpoint(
            checkpoint_path,
            comparisons,
            execution_details,
            case_execution,
            job_execution,
        )
        print(f"{metadata['case_id']}: {'PASS' if comparison.passed else 'FAIL'}")
        for error in comparison.field_errors:
            print(
                f"  {error['task_name']} / {error['field']}: "
                f"expected={error['expected']!r}, actual={error['actual']!r}"
            )
        for task in comparison.missing_tasks:
            print(f"  missing: {task.get('task_name', '')} / {task.get('assignee', '')}")
        for task in comparison.unexpected_tasks:
            print(f"  unexpected: {task.get('task_name', '')} / {task.get('assignee', '')}")

    rejection_diagnostic_names = (
        "ai_contract_rejection_count",
        "ai_structural_contract_rejection_count",
        "ai_semantic_rejection_count",
        "ai_unknown_task_id_rejection_count",
        "ai_invalid_source_clause_rejection_count",
        "ai_invalid_anchor_clause_rejection_count",
        "ai_non_concrete_action_rejection_count",
        "ai_invalid_assignee_rejection_count",
    )
    # Older checkpoints may have complete per-case diagnostics but a narrower
    # case_execution projection. Rehydrate the projection during --resume so
    # no provider call is needed merely to adopt new telemetry fields.
    for item in case_execution:
        diagnostics = execution_details.get(str(item.get("case_id", "")), {})
        for name in rejection_diagnostic_names:
            item[name] = int(diagnostics.get(name, 0))
        for name in (
            "ai_quality_create_candidate_count",
            "ai_quality_create_eligible_count",
            "ai_quality_create_selected_count",
        ):
            item[name] = int(diagnostics.get(name, 0))
        item["ai_quality_create_exclusion_reasons"] = diagnostics.get(
            "ai_quality_create_exclusion_reasons", {}
        )

    metrics = aggregate_results(comparisons)
    reviewed_metrics = (
        aggregate_results(reviewed_comparisons) if reviewed_comparisons else None
    )
    exploratory_metrics = aggregate_results(exploratory_comparisons)
    diagnostic_values = list(execution_details.values())
    pipeline_diagnostics = {
        "candidate_window_count": sum(
            int(item.get("candidate_window_count", 0)) for item in diagnostic_values
        ),
        "rule_event_count": sum(
            int(item.get("rule_event_count", 0)) for item in diagnostic_values
        ),
        "ai_event_count": sum(
            int(item.get("ai_event_count", 0)) for item in diagnostic_values
        ),
        "ai_window_count": sum(
            int(item.get("ai_window_count", 0)) for item in diagnostic_values
        ),
        "ai_batch_count": sum(
            int(item.get("ai_batch_count", 0)) for item in diagnostic_values
        ),
        "ai_context_clause_count": sum(
            int(item.get("ai_context_clause_count", 0))
            for item in diagnostic_values
        ),
        "unresolved_window_count": sum(
            int(item.get("unresolved_window_count", 0))
            for item in diagnostic_values
        ),
        "event_count_before_deduplication": sum(
            int(item.get("event_count_before_deduplication", 0))
            for item in diagnostic_values
        ),
        "event_count_after_deduplication": sum(
            int(item.get("event_count_after_deduplication", 0))
            for item in diagnostic_values
        ),
        "terminal_replay_blocked_count": sum(
            int(item.get("terminal_replay_blocked_count", 0))
            for item in diagnostic_values
        ),
    }
    for name in (
        "ledger_task_created_count",
        "ledger_task_updated_count",
        "exact_id_link_count",
        "exact_alias_link_count",
        "semantic_link_count",
        "unresolved_mutation_count",
        "duplicate_task_merge_count",
        "provisional_task_created_count",
        "provisional_task_promoted_count",
        "provisional_promotion_blocked_count",
        "ambiguous_identity_mutation_blocked_count",
        "sibling_identity_split_count",
        "ai_contract_rejection_count",
        "ai_structural_contract_rejection_count",
        "ai_semantic_rejection_count",
        "ai_unknown_task_id_rejection_count",
        "ai_invalid_source_clause_rejection_count",
        "ai_invalid_anchor_clause_rejection_count",
        "ai_non_concrete_action_rejection_count",
        "ai_invalid_assignee_rejection_count",
        "ai_fallback_error_count",
        "unauthorized_creation_blocked_count",
        "ledger_unknown_task_id_rejection_count",
        "action_classifier_clause_count",
        "action_classifier_would_create_count",
        "action_classifier_would_review_count",
        "action_classifier_would_update_count",
        "action_classifier_rule_action_clause_count",
        "action_classifier_rule_agreement_count",
        "action_classifier_rule_disagreement_count",
        "action_classifier_error_count",
        "candidate_evidence_count",
        "candidate_decision_count",
        "candidate_ai_create_check_suppressed_count",
        "candidate_router_error_count",
        "task_create_proposal_call_count",
        "task_create_proposal_accepted_count",
        "task_create_proposal_no_action_count",
        "task_create_proposal_unresolved_count",
        "task_create_proposal_rejected_count",
        "task_semantic_query_count",
        "task_semantic_scored_query_count",
        "task_semantic_production_agreement_count",
        "task_semantic_production_disagreement_count",
        "task_semantic_ambiguous_sibling_count",
        "task_semantic_linker_error_count",
        "context_bundle_count",
        "context_total_clause_count",
        "context_total_character_count",
        "context_total_task_count",
        "context_total_history_event_count",
        "context_total_note_cue_count",
        "context_clause_cap_hit_count",
        "context_character_cap_hit_count",
        "context_retrieval_error_count",
        "ai_quality_create_candidate_count",
        "ai_quality_create_eligible_count",
        "ai_quality_create_selected_count",
    ):
        pipeline_diagnostics[name] = sum(
            int(item.get(name, 0)) for item in diagnostic_values
        )
    ai_quality_exclusions: dict[str, int] = {}
    for item in diagnostic_values:
        for reason, count in item.get("ai_quality_create_exclusion_reasons", {}).items():
            ai_quality_exclusions[reason] = ai_quality_exclusions.get(reason, 0) + int(count)
    pipeline_diagnostics["ai_quality_create_exclusion_reasons"] = dict(
        sorted(ai_quality_exclusions.items())
    )
    candidate_count = pipeline_diagnostics["candidate_window_count"]
    pipeline_diagnostics["weighted_ai_call_rate"] = (
        sum(
            float(item.get("ai_call_rate", 0.0))
            * int(item.get("candidate_window_count", 0))
            for item in diagnostic_values
        )
        / candidate_count
        if candidate_count
        else 0.0
    )
    total_clause_count = sum(
        int(item.get("clause_count", 0)) for item in diagnostic_values
    )
    prediction_counts: dict[str, int] = {}
    for item in diagnostic_values:
        for label, count in item.get(
            "action_classifier_prediction_counts", {}
        ).items():
            prediction_counts[label] = prediction_counts.get(label, 0) + int(count)
    pipeline_diagnostics["action_classifier_prediction_counts"] = dict(
        sorted(prediction_counts.items())
    )
    pipeline_diagnostics["action_classifier_versions"] = sorted(
        {
            str(item.get("action_classifier_version", "disabled"))
            for item in diagnostic_values
        }
    )
    pipeline_diagnostics["embedding_model_versions"] = sorted(
        {
            str(item.get("embedding_model_version", "disabled"))
            for item in diagnostic_values
        }
    )
    candidate_route_counts: dict[str, int] = {}
    for item in diagnostic_values:
        for route, count in item.get("candidate_route_counts", {}).items():
            candidate_route_counts[route] = candidate_route_counts.get(
                route, 0
            ) + int(count)
    pipeline_diagnostics["candidate_route_counts"] = dict(
        sorted(candidate_route_counts.items())
    )
    proposal_rejection_reasons: dict[str, int] = {}
    for item in diagnostic_values:
        for reason, count in item.get(
            "task_create_proposal_rejection_reasons", {}
        ).items():
            proposal_rejection_reasons[reason] = (
                proposal_rejection_reasons.get(reason, 0) + int(count)
            )
    pipeline_diagnostics["task_create_proposal_rejection_reasons"] = dict(
        sorted(proposal_rejection_reasons.items())
    )
    task_semantic_route_counts: dict[str, int] = {}
    task_semantic_reason_counts: dict[str, int] = {}
    for item in diagnostic_values:
        for route, count in item.get("task_semantic_route_counts", {}).items():
            task_semantic_route_counts[route] = (
                task_semantic_route_counts.get(route, 0) + int(count)
            )
        for reason, count in item.get("task_semantic_reason_counts", {}).items():
            task_semantic_reason_counts[reason] = (
                task_semantic_reason_counts.get(reason, 0) + int(count)
            )
    pipeline_diagnostics["task_semantic_route_counts"] = dict(
        sorted(task_semantic_route_counts.items())
    )
    pipeline_diagnostics["task_semantic_reason_counts"] = dict(
        sorted(task_semantic_reason_counts.items())
    )
    semantic_query_count = pipeline_diagnostics["task_semantic_query_count"]
    semantic_scored_count = pipeline_diagnostics["task_semantic_scored_query_count"]
    pipeline_diagnostics["task_semantic_mean_top1_score"] = (
        sum(
            float(item.get("task_semantic_mean_top1_score", 0.0))
            * int(item.get("task_semantic_scored_query_count", 0))
            for item in diagnostic_values
        )
        / semantic_scored_count
        if semantic_scored_count else 0.0
    )
    pipeline_diagnostics["task_semantic_mean_margin"] = (
        sum(
            float(item.get("task_semantic_mean_margin", 0.0))
            * int(item.get("task_semantic_query_count", 0))
            for item in diagnostic_values
        )
        / semantic_query_count
        if semantic_query_count else 0.0
    )
    pipeline_diagnostics["context_max_clause_count_observed"] = max(
        (int(item.get("context_max_clause_count_observed", 0)) for item in diagnostic_values),
        default=0,
    )
    pipeline_diagnostics["context_max_character_count_observed"] = max(
        (
            int(item.get("context_max_character_count_observed", 0))
            for item in diagnostic_values
        ),
        default=0,
    )
    context_tier_clause_counts: dict[str, int] = {}
    for item in diagnostic_values:
        for tier, count in item.get("context_tier_clause_counts", {}).items():
            context_tier_clause_counts[tier] = (
                context_tier_clause_counts.get(tier, 0) + int(count)
            )
    pipeline_diagnostics["context_tier_clause_counts"] = dict(
        sorted(context_tier_clause_counts.items())
    )
    pipeline_diagnostics["candidate_router_versions"] = sorted(
        {
            str(item.get("candidate_router_version", "disabled"))
            for item in diagnostic_values
        }
    )
    pipeline_diagnostics["candidate_threshold_versions"] = sorted(
        {
            str(item.get("candidate_threshold_version", "disabled"))
            for item in diagnostic_values
        }
    )
    for output_name, source_name in (
        ("task_semantic_linker_versions", "task_semantic_linker_version"),
        ("task_semantic_index_versions", "task_semantic_index_version"),
        ("task_semantic_scoring_versions", "task_semantic_scoring_version"),
        (
            "task_semantic_embedding_model_versions",
            "task_semantic_embedding_model_version",
        ),
        ("context_retrieval_versions", "context_retrieval_version"),
        ("context_topic_index_versions", "context_topic_index_version"),
        ("context_embedding_model_versions", "context_embedding_model_version"),
    ):
        pipeline_diagnostics[output_name] = sorted(
            {str(item.get(source_name, "disabled")) for item in diagnostic_values}
        )
    pipeline_diagnostics["ai_clause_coverage"] = (
        pipeline_diagnostics["ai_context_clause_count"] / total_clause_count
        if total_clause_count
        else 0.0
    )
    fallback_metrics = {
        route: sum(item["route"] == route for item in case_execution)
        for route in (
            "rule_only",
            "ai_fallback_not_configured",
            "ai_fallback_skipped_no_candidate",
            "ai_fallback_resolved",
            "ai_fallback_unresolved",
            "ai_fallback_failed",
        )
    }
    if unreviewed_ground_truth:
        print(
            "WARNING: "
            f"{len(unreviewed_ground_truth)}/{discovered_case_count} cases are not marked "
            "as reviewed ground truth."
        )
    if suspect_empty_ground_truth:
        print(
            "WARNING: empty expected tasks conflict with recap/task candidates in "
            f"{len(suspect_empty_ground_truth)} cases."
        )
    if execution_errors:
        print(f"ERROR: {len(execution_errors)} case(s) could not be evaluated.")
    print(
        "Cases: {passed_case_count}/{case_count} | "
        "precision={task_precision:.3f} recall={task_recall:.3f} f1={task_identity_f1:.3f} "
        "field_accuracy={field_accuracy:.3f}".format(**metrics)
    )
    print(
        "Routes: rule_only={rule_only} ai_not_configured={ai_fallback_not_configured} "
        "ai_skipped_no_candidate={ai_fallback_skipped_no_candidate} "
        "ai_resolved={ai_fallback_resolved} "
        "ai_unresolved={ai_fallback_unresolved} ai_failed={ai_fallback_failed}".format(
            **fallback_metrics
        )
    )
    if reviewed_metrics:
        print(
            "Reviewed: {passed_case_count}/{case_count} | "
            "precision={task_precision:.3f} recall={task_recall:.3f} f1={task_identity_f1:.3f} "
            "field_accuracy={field_accuracy:.3f}".format(**reviewed_metrics)
        )
    else:
        print("Reviewed: 0 cases; no publishable quality metric is available.")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "pipeline_version": args.pipeline_version,
            "meeting_context_mode": args.context_mode,
            "action_classifier_mode": args.action_classifier_mode,
            "action_classifier_model_path": str(args.action_classifier_model_path),
            "action_candidate_builder_mode": args.action_candidate_builder_mode,
            "action_candidate_builder_version": args.action_candidate_builder_version,
            "commitment_router_mode": args.commitment_router_mode,
            "commitment_router_version": args.commitment_router_version,
            "commitment_router_active_types": args.commitment_router_active_types,
            "action_canonicalization_mode": args.action_canonicalization_mode,
            "action_canonicalization_version": args.action_canonicalization_version,
            "recap_reconciliation_mode": args.recap_reconciliation_mode,
            "owner_grounding_mode": args.owner_grounding_mode,
            "deadline_grounding_mode": args.deadline_grounding_mode,
            "candidate_router_mode": args.candidate_router_mode,
            "task_create_proposal_enabled": args.task_create_proposal,
            "ai_create_proposal_enabled": args.ai_create_proposal,
            "ai_create_max_proposals_per_meeting": args.ai_create_max_proposals,
            "ai_quality_uplift_mode": args.ai_quality_uplift_mode,
            "ai_cost_gate_mode": args.ai_cost_gate_mode,
            "ai_cost_max_provider_calls_per_meeting": args.ai_cost_max_provider_calls,
            "ai_cost_max_payload_characters": args.ai_cost_max_payload_characters,
            "ai_cost_max_estimated_usd_per_meeting": args.ai_cost_max_estimated_usd,
            "task_semantic_linker_mode": args.task_semantic_linker_mode,
            "task_link_embedding_model_name": args.task_link_embedding_model_name,
            "task_link_scoring_version": args.task_link_scoring_version,
            "task_link_weights": {
                "semantic": args.task_link_semantic_weight,
                "lexical": args.task_link_lexical_weight,
                "topic": args.task_link_topic_weight,
                "owner": args.task_link_owner_weight,
                "recency": args.task_link_recency_weight,
            },
            "task_link_strong_threshold": args.task_link_strong_threshold,
            "task_link_min_margin": args.task_link_min_margin,
            "task_link_ai_threshold": args.task_link_ai_threshold,
            "task_link_recency_horizon_clauses": args.task_link_recency_horizon,
            "task_link_top_k": args.task_link_top_k,
            "context_retrieval_mode": args.context_retrieval_mode,
            "context_retrieval_version": args.context_retrieval_version,
            "ai_mutation_router_mode": args.ai_mutation_router_mode,
            "ai_mutation_prompt_version": args.ai_mutation_prompt_version,
            "ai_mutation_min_confidence": args.ai_mutation_min_confidence,
            "note_dual_view_mode": args.note_dual_view_mode,
            "note_dual_view_version": args.note_dual_view_version,
            "temporal_semantics_mode": args.temporal_semantics_mode,
            "temporal_parser_version": args.temporal_parser_version,
            "temporal_working_day_policy": args.temporal_working_day_policy,
            "temporal_min_confidence": args.temporal_min_confidence,
            "context_limits": {
                "max_clauses": args.context_max_clauses,
                "max_characters": args.context_max_characters,
                "max_tasks": args.context_max_tasks,
                "local_before": args.context_local_before,
                "local_after": args.context_local_after,
                "max_topic_clauses": args.context_max_topic_clauses,
                "max_topics": args.context_max_topics,
                "max_history_events": args.context_max_history_events,
            },
            "context_topic_boundary_threshold": (
                args.context_topic_boundary_threshold
            ),
            "context_topic_smoothing_window": args.context_topic_smoothing_window,
            "action_clear_threshold": args.action_clear_threshold,
            "action_ai_threshold": args.action_ai_threshold,
            "candidate_threshold_version": args.candidate_threshold_version,
            "meeting_notes": "off" if args.without_meeting_notes else "sidecar_if_present",
            "prompt_version": PROMPT_VERSION,
            "model": MODEL_VERSION,
            "reasoning_effort": REASONING_EFFORT_VERSION,
            "mode": (
                "job_api"
                if args.job_endpoint
                else (
                    "api"
                    if args.endpoint
                    else ("local_openai" if args.local_openai else "local")
                )
            ),
            "endpoint": args.job_endpoint or args.endpoint or "",
            "completed_case_count": len(comparisons),
            "expected_case_count": expected_case_count,
            "metrics_are_partial": bool(execution_errors) or len(comparisons) != expected_case_count,
            "dataset_audit": {
                "reviewed_ground_truth_count": len(reviewed_comparisons),
                "unreviewed_ground_truth_count": len(unreviewed_ground_truth),
                "unreviewed_case_ids": unreviewed_ground_truth,
                "suspect_empty_ground_truth_count": len(suspect_empty_ground_truth),
                "suspect_empty_ground_truth_case_ids": suspect_empty_ground_truth,
            },
            "metrics": metrics,
            "reviewed_metrics": reviewed_metrics,
            "exploratory_metrics": exploratory_metrics,
            "pipeline_diagnostics": pipeline_diagnostics,
            "fallback_metrics": fallback_metrics,
            "job_execution": job_execution,
            "execution_errors": execution_errors,
            "case_execution": case_execution,
            "cases": [asdict(item) for item in comparisons],
            "case_diagnostics": execution_details,
        }
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Report: {args.report}")

    exit_metrics = reviewed_metrics or metrics
    if args.pipeline_version == "v2" and (
        exit_metrics["task_precision"] < args.min_task_precision
        or exit_metrics["task_recall"] < args.min_task_recall
    ):
        raise SystemExit(
            "V2 quality gate not met: "
            f"precision >= {args.min_task_precision:.2f}, "
            f"recall >= {args.min_task_recall:.2f}"
        )
    if exit_metrics["failed_case_count"] or execution_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
