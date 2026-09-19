"""Offline readiness checks for the single-worker V2.27 ASGI app.

The preflight intentionally does not import or start the API and does not make
provider, tunnel, or other network calls.  It only checks local configuration
and the immutable private artifact bundle.  Output is safe to paste into an
operator ticket: secrets, transcript data, and full environment values are
never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_SHA256 = "7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3"
DEFAULT_ARTIFACT_DIRECTORY = ROOT / "evaluation/runtime/experimental-distillation-v2/v228-frozen-promotion"
DEFAULT_FEEDBACK_DIRECTORY = ROOT / "evaluation/runtime/v227-feedback"
TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
REQUIRED_POLICY_FIELDS = {
    "adaptive_budget", "adaptive_threshold", "base_budget", "base_threshold",
    "bridge_weight", "field_completeness_min", "intermediate_share_min",
    "intermediate_weight", "score_mean_cap", "volume_cutoff",
}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _result(name: str, passed: bool, code: str, message: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "code": code,
        "message": message,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _safe_artifact_name(name: object) -> bool:
    """Return whether a manifest artifact key is a plain local filename."""
    return (
        isinstance(name, str)
        and bool(name)
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and ":" not in name
        and all(ord(character) >= 32 for character in name)
        and Path(name).name == name
    )


def _safe_private_directory(path: Path) -> tuple[bool, str]:
    """Return whether *path* exists or its nearest parent can be created."""
    candidate = path.expanduser()
    probe = candidate
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists() or not probe.is_dir():
        return False, "parent directory does not exist"
    if not os.access(probe, os.W_OK | os.X_OK):
        return False, "parent directory is not writable"
    # On POSIX, world-writable storage is not a private feedback/job location.
    # Windows ACLs do not map reliably to these mode bits, so access() is the
    # useful check there.
    if os.name != "nt":
        try:
            if probe.stat().st_mode & 0o002:
                return False, "parent directory is world-writable"
        except OSError:
            return False, "parent directory could not be inspected"
    if candidate.exists() and not candidate.is_dir():
        return False, "configured path is not a directory"
    if candidate.exists() and not os.access(candidate, os.W_OK | os.X_OK):
        return False, "configured directory is not writable"
    return True, "local private directory is writable or creatable"


def _feedback_check(directory: Path, tenant: str) -> tuple[bool, str]:
    if not TENANT_RE.fullmatch(tenant):
        return False, "STOP_INVALID_V227_FEEDBACK_TENANT_ID"
    root = directory.expanduser().resolve() / tenant
    ok, _ = _safe_private_directory(root)
    return ok, "PASS_V227_FEEDBACK_DIRECTORY" if ok else "STOP_V227_FEEDBACK_DIRECTORY_UNWRITABLE"


def _git_ignored(path: Path, root: Path) -> bool:
    """Ask Git whether a path (or its parent) is ignored, without touching it."""
    if not path.is_relative_to(root):
        return False
    for target in (path, path.parent):
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "-q", "--", str(target.relative_to(root))],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return False
        if completed.returncode == 0:
            return True
    return False


def _inside_tracked_source(path: Path, root: Path) -> bool:
    """Reject SQLite files below a tracked source directory.

    Ignored runtime directories are allowed.  For a non-ignored path, a
    tracked file directly in any ancestor directory is enough to identify the
    directory as source.  This keeps evaluation/runtime (which is ignored)
    usable while rejecting paths such as backend/app/jobs.sqlite3.
    """
    candidate = path.expanduser().resolve()
    root = root.resolve()
    if not candidate.is_relative_to(root) or _git_ignored(candidate, root):
        return False
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
            text=False,
        ).stdout
    except OSError:
        return False
    tracked = [Path(item.decode("utf-8")) for item in output.split(b"\0") if item]
    for ancestor in (candidate.parent, *candidate.parents):
        if not ancestor.is_relative_to(root):
            break
        relative = ancestor.relative_to(root)
        if any(item.parent == relative for item in tracked):
            return True
    return False


def _sqlite_check(value: str, root: Path) -> tuple[bool, str]:
    if not value.strip() or value.strip() == ":memory:":
        return False, "STOP_MEETING_JOB_SQLITE_PATH_REQUIRED"
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if _inside_tracked_source(candidate, root):
        return False, "STOP_MEETING_JOB_SQLITE_PATH_INSIDE_TRACKED_SOURCE"
    if candidate.exists() and not candidate.is_file():
        return False, "STOP_MEETING_JOB_SQLITE_PATH_NOT_FILE"
    if candidate.exists() and not os.access(candidate, os.W_OK):
        return False, "STOP_MEETING_JOB_SQLITE_PATH_UNWRITABLE"
    ok, _ = _safe_private_directory(candidate.parent)
    return ok, "PASS_MEETING_JOB_SQLITE_PATH" if ok else "STOP_MEETING_JOB_SQLITE_PARENT_UNWRITABLE"


def _artifact_check(directory: Path) -> tuple[bool, str]:
    directory = directory.expanduser().resolve()
    manifest_path = directory / "manifest.json"
    if not directory.is_dir() or not manifest_path.is_file():
        return False, "STOP_MISSING_PRIVATE_ARTIFACT"
    try:
        if _sha256(manifest_path) != FROZEN_MANIFEST_SHA256:
            return False, "STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH"
        manifest = _json(manifest_path)
        if manifest.get("schema_version") != "v228-manifest-v1" or manifest.get("immutable") is not True:
            return False, "STOP_INVALID_V228_MANIFEST"
        hashes = manifest.get("artifact_hashes")
        required_files = {"model.json", "frozen-policy.json"}
        if not isinstance(hashes, dict) or not required_files.issubset(hashes):
            return False, "STOP_INVALID_RUNTIME_ARTIFACT_SET"
        for name, expected_hash in hashes.items():
            if not _safe_artifact_name(name):
                return False, "STOP_INVALID_RUNTIME_ARTIFACT_ENTRY"
            if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
                return False, "STOP_INVALID_RUNTIME_ARTIFACT_HASH"
            path = (directory / name).resolve()
            if not path.is_relative_to(directory):
                return False, "STOP_INVALID_RUNTIME_ARTIFACT_ENTRY"
            if not path.is_file():
                return False, "STOP_MISSING_RUNTIME_ARTIFACT"
            if expected_hash.casefold() != _sha256(path):
                return False, "STOP_ARTIFACT_BUNDLE_HASH_MISMATCH"
        model = _json(directory / "model.json")
        weights = model.get("weights")
        if (model.get("schema_version") != "v228-frozen-model-v1"
                or model.get("feature_dimensions") != 768
                or not isinstance(weights, list) or len(weights) != 768
                or not all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in weights)
                or not isinstance(model.get("bias"), (int, float))
                or not math.isfinite(float(model["bias"]))):
            return False, "STOP_INVALID_RUNTIME_MODEL"
        policy = _json(directory / "frozen-policy.json")
        if (policy.get("schema_version") != "v228-frozen-policy-v1"
                or not isinstance(policy.get("policy"), dict)
                or set(policy["policy"]) != REQUIRED_POLICY_FIELDS):
            return False, "STOP_INVALID_RUNTIME_POLICY"
    except (AttributeError, OSError, UnicodeError, ValueError, TypeError, KeyError):
        return False, "STOP_INVALID_PRIVATE_ARTIFACT_BUNDLE"
    return True, "PASS_FROZEN_ARTIFACT_BUNDLE"


def validate_preflight(*, env: Mapping[str, str] | None = None, root: Path = ROOT) -> dict[str, Any]:
    """Validate local V2.27 settings and return a safe, machine-readable report."""
    values = os.environ if env is None else env
    root = Path(root).resolve()
    checks: list[dict[str, str]] = []

    key_present = bool(values.get("POWER_AUTOMATE_API_KEY", "").strip())
    checks.append(_result("power_automate_api_key", key_present,
                          "PASS_POWER_AUTOMATE_API_KEY" if key_present else "STOP_POWER_AUTOMATE_API_KEY_REQUIRED",
                          "configured" if key_present else "set a nonempty backend API key"))

    tenant = values.get("V227_FEEDBACK_TENANT_ID", "").strip()
    tenant_present = bool(TENANT_RE.fullmatch(tenant))
    checks.append(_result("feedback_tenant", tenant_present,
                          "PASS_V227_FEEDBACK_TENANT_ID" if tenant_present else "STOP_INVALID_V227_FEEDBACK_TENANT_ID",
                          "configured" if tenant_present else "set 1-64 letters, digits, dot, dash, or underscore"))

    feedback = Path(values.get("V227_FEEDBACK_DIRECTORY", "") or DEFAULT_FEEDBACK_DIRECTORY)
    feedback_ok, feedback_code = _feedback_check(feedback, tenant) if tenant_present else (False, "STOP_INVALID_V227_FEEDBACK_TENANT_ID")
    checks.append(_result("feedback_directory", feedback_ok, feedback_code,
                          "writable or creatable private tenant directory" if feedback_ok else "choose a writable private feedback directory"))

    sqlite_value = values.get("MEETING_JOB_SQLITE_PATH", "")
    sqlite_ok, sqlite_code = _sqlite_check(sqlite_value, root)
    checks.append(_result("sqlite_job_store", sqlite_ok, sqlite_code,
                          "writable durable path outside tracked source" if sqlite_ok else "set a writable SQLite path outside tracked source"))

    artifact = Path(values.get("V227_ARTIFACT_DIRECTORY", "") or DEFAULT_ARTIFACT_DIRECTORY)
    artifact_ok, artifact_code = _artifact_check(artifact)
    checks.append(_result("frozen_artifacts", artifact_ok, artifact_code,
                          "manifest and model bundle validated" if artifact_ok else "provide the private frozen V2.28 artifact bundle"))

    audio_enabled = any(values.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}
                        for name in ("V227_AUDIO_TO_TEXT_ENABLED", "V227_AUDIO_TRANSCRIPTION_ENABLED", "V227_AUDIO_ENABLED"))
    audio_key = bool(values.get("OPENAI_API_KEY", "").strip())
    audio_ok = not audio_enabled or audio_key
    checks.append(_result("audio_provider_key", audio_ok,
                          "PASS_AUDIO_CONFIGURATION" if audio_ok else "STOP_OPENAI_API_KEY_REQUIRED_FOR_AUDIO",
                          "audio disabled or provider key configured" if audio_ok else "set OPENAI_API_KEY or disable audio"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {"status": "FAIL" if failed else "PASS", "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline V2.27 local readiness check")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--text", action="store_true", help="print one concise line per check")
    args = parser.parse_args(argv)
    report = validate_preflight(root=args.root)
    if args.text:
        print(f"V227_PREFLIGHT {report['status']}")
        for check in report["checks"]:
            print(f"{check['status']} {check['code']}: {check['message']}")
    else:
        print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
