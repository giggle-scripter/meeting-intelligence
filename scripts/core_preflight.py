"""Offline preflight for the selectable local meeting core shell.

The report intentionally contains only capability metadata and pass/fail
codes.  It never prints configuration values, secrets, or transcript data and
does not import a provider client or start a service.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

from scripts import local_v227_preflight


ROOT = Path(__file__).resolve().parents[1]
CORE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "v1-frozen": {
        "adaptive": False,
        "pipeline_version": "v1",
        "runtime_model_id": "v1-frozen",
        "supports_meeting_note": True,
    },
    "v2-adaptive": {
        "adaptive": True,
        "pipeline_version": "v227_experimental",
        "runtime_model_id": "v228-frozen-full-fit",
        "supports_meeting_note": False,
    },
}


@contextmanager
def _v2_environment(values: Mapping[str, str], *, artifact: Path, feedback: Path) -> Iterator[None]:
    """Temporarily provide the optional adapter with the preflight settings.

    ``V2AdaptiveCore`` is also used by the application and therefore reads
    its tenant from the process environment.  Preflight accepts an explicit
    mapping for testability and for callers that do not want to mutate their
    process environment, so bridge only the adapter's three selection values
    and restore them immediately afterwards.
    """

    names = {
        "V227_FEEDBACK_TENANT_ID": _env_value(
            values, "V227_FEEDBACK_TENANT_ID", "MEETING_FEEDBACK_TENANT_ID"
        ),
        "V227_ARTIFACT_DIRECTORY": str(artifact),
        "V227_FEEDBACK_DIRECTORY": str(feedback),
    }
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name, value in names.items():
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _load_v2_capabilities(
    values: Mapping[str, str], *, artifact: Path, feedback: Path,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Load the selected optional adapter after artifact checks have passed."""

    try:
        optional_package = importlib.import_module("meeting_v2_adaptive")
        adapter_type = getattr(optional_package, "V2AdaptiveCore")
        with _v2_environment(values, artifact=artifact, feedback=feedback):
            adapter = adapter_type(
                artifact_directory=artifact,
                feedback_directory=feedback,
                expected_manifest_sha256=local_v227_preflight.FROZEN_MANIFEST_SHA256,
            )
        capabilities = adapter.capabilities
        result = {
            "adaptive": bool(capabilities.adaptive),
            "pipeline_version": str(capabilities.pipeline_version),
            "runtime_model_id": str(capabilities.runtime_model_id),
            "supports_meeting_note": bool(capabilities.supports_meeting_note),
        }
    except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
        # Do not expose import paths, exception text, or configuration values
        # in operator output.  The check code is enough to diagnose the gate.
        return None, _check(
            "v2_adapter", False, "STOP_V2_ADAPTER_UNAVAILABLE",
            "optional V2 adaptive package could not be loaded",
        )
    return result, _check(
        "v2_adapter", True, "PASS_V2_ADAPTER_LOADED",
        "optional V2 adaptive adapter loaded",
    )


def _check(name: str, passed: bool, code: str, message: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "code": code,
        "message": message,
    }


def _env_value(values: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


def _common_checks(values: Mapping[str, str], root: Path) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    key_present = bool(_env_value(values, "POWER_AUTOMATE_API_KEY"))
    checks.append(_check(
        "common_api_key", key_present,
        "PASS_COMMON_API_KEY" if key_present else "STOP_COMMON_API_KEY_REQUIRED",
        "configured" if key_present else "set POWER_AUTOMATE_API_KEY",
    ))

    tenant = _env_value(values, "MEETING_FEEDBACK_TENANT_ID", "V227_FEEDBACK_TENANT_ID")
    tenant_present = bool(local_v227_preflight.TENANT_RE.fullmatch(tenant))
    checks.append(_check(
        "feedback_tenant", tenant_present,
        "PASS_FEEDBACK_TENANT" if tenant_present else "STOP_FEEDBACK_TENANT_REQUIRED",
        "configured" if tenant_present else "set a valid tenant id",
    ))

    feedback_value = _env_value(values, "MEETING_FEEDBACK_DIRECTORY", "V227_FEEDBACK_DIRECTORY")
    feedback = Path(feedback_value or local_v227_preflight.DEFAULT_FEEDBACK_DIRECTORY)
    feedback_ok, feedback_code = (
        local_v227_preflight._feedback_check(feedback, tenant)
        if tenant_present else (False, "STOP_FEEDBACK_TENANT_REQUIRED")
    )
    if not feedback_value:
        feedback_ok, feedback_code = False, "STOP_FEEDBACK_DIRECTORY_REQUIRED"
    checks.append(_check(
        "feedback_directory", feedback_ok, feedback_code,
        "writable or creatable private tenant directory"
        if feedback_ok else "choose a writable private feedback directory",
    ))

    sqlite_value = _env_value(values, "MEETING_JOB_SQLITE_PATH")
    sqlite_ok, sqlite_code = local_v227_preflight._sqlite_check(sqlite_value, root)
    checks.append(_check(
        "sqlite_job_store", sqlite_ok, sqlite_code,
        "writable durable path outside tracked source"
        if sqlite_ok else "set a writable SQLite path outside tracked source",
    ))
    return checks


def validate_preflight(
    *, core: str | None = None, env: Mapping[str, str] | None = None,
    root: Path = ROOT, core_id: str | None = None,
) -> dict[str, Any]:
    """Return an offline, secret-free report for one selectable core."""

    values = os.environ if env is None else env
    selected = core or core_id or values.get("MEETING_CORE", "v1-frozen").strip() or "v1-frozen"
    if selected not in CORE_CAPABILITIES:
        return {
            "status": "FAIL",
            "selected_core": selected,
            "capabilities": {},
            "checks": [_check(
                "core_selection", False, "STOP_UNKNOWN_CORE",
                "expected v1-frozen or v2-adaptive",
            )],
        }

    resolved_root = Path(root).resolve()
    checks = _common_checks(values, resolved_root)
    capabilities: dict[str, Any] = CORE_CAPABILITIES[selected]
    if selected == "v2-adaptive":
        # Keep the established immutable-artifact checks as a nested offline
        # report, while accepting the unified shell's common variable names.
        mapped = dict(values)
        mapped.setdefault("V227_FEEDBACK_TENANT_ID", _env_value(values, "MEETING_FEEDBACK_TENANT_ID"))
        mapped.setdefault("V227_FEEDBACK_DIRECTORY", _env_value(values, "MEETING_FEEDBACK_DIRECTORY"))
        artifact_report = local_v227_preflight.validate_preflight(env=mapped, root=resolved_root)
        checks.extend({
            **item,
            "name": f"v2_{item['name']}",
        } for item in artifact_report["checks"] if item["name"] not in {
            "power_automate_api_key", "feedback_tenant", "feedback_directory", "sqlite_job_store",
        })
        # Artifact integrity is the first gate.  Only after it passes do we
        # import or construct the optional adapter, whose capabilities are the
        # source of truth for base-versus-challenger runtime metadata.
        if artifact_report.get("status") == "PASS":
            artifact_directory = Path(
                mapped.get("V227_ARTIFACT_DIRECTORY", "")
                or local_v227_preflight.DEFAULT_ARTIFACT_DIRECTORY
            ).expanduser().resolve()
            feedback_directory = Path(
                mapped.get("V227_FEEDBACK_DIRECTORY", "")
                or local_v227_preflight.DEFAULT_FEEDBACK_DIRECTORY
            ).expanduser().resolve()
            loaded_capabilities, adapter_check = _load_v2_capabilities(
                mapped, artifact=artifact_directory, feedback=feedback_directory,
            )
            checks.append(adapter_check)
            capabilities = loaded_capabilities or {}
        else:
            capabilities = {}

    return {
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "selected_core": selected,
        "capabilities": capabilities,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline meeting core readiness check")
    parser.add_argument("--core", choices=tuple(CORE_CAPABILITIES), default=None)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)
    report = validate_preflight(core=args.core, root=args.root)
    if args.text:
        print(f"MEETING_CORE_PREFLIGHT {report['status']} core={report['selected_core']}")
        capabilities = report["capabilities"]
        if capabilities:
            print("CAPABILITIES " + json.dumps(capabilities, ensure_ascii=True, sort_keys=True))
        for item in report["checks"]:
            print(f"{item['status']} {item['code']}: {item['message']}")
    else:
        print(json.dumps(report, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
