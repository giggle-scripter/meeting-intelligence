"""Build an authorized, non-destructive V1 company-shell snapshot.

This utility copies only the allowlisted runtime, contract, documentation,
and focused test surface.  It never removes source files or Git history and
does not make any decision about intellectual-property ownership.  Use it
only after the appropriate owner has authorized the snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"
EXCLUDED_ROOTS = {
    "meeting_v2_adaptive",
    "evaluation/runtime",
    "evaluation/artifacts",
    "evaluation/datasets",
    "data",
    "runtime",
    "artifacts",
    "datasets",
    "scripts/experimental_distillation",
}
EXCLUDED_FILES = {
    "backend/app/core/v2_adaptive.py",
}
V2_TEST_MARKERS = ("v2", "v227", "core_api", "core_feedback_ledger")
ALLOWED_FILES = {
    "README.md",
    "pyproject.toml",
    "backend/__init__.py",
    "backend/requirements.txt",
    "scripts/core_preflight.py",
    "scripts/local_v227_preflight.py",
    "scripts/run_local_meeting_core.ps1",
    "scripts/build_company_shell_snapshot.py",
    "docs/operator-handoff-v1-v2-vi.md",
    "docs/api.md",
    "docs/security.md",
    "power-automate/README.md",
    "sp365/integration-contract.md",
    "clients/csharp/README.md",
    "clients/csharp/MeetingTaskPipeline.Client/Program.cs",
    "clients/csharp/MeetingTaskPipeline.Client/MeetingTaskPipeline.Client.csproj",
}
ALLOWED_DIRS = ("backend/app",)
ALLOWED_TESTS = {
    "backend/tests/unit/test_jobs.py",
    "backend/tests/integration/test_pipeline.py",
}


def _normal(path: Path) -> str:
    return path.as_posix().lstrip("./")


def _excluded(relative: Path) -> bool:
    value = _normal(relative)
    if value in EXCLUDED_FILES:
        return True
    if value.startswith("backend/tests/") and any(
        marker in relative.name.casefold() for marker in V2_TEST_MARKERS
    ):
        return True
    return any(value == root or value.startswith(root + "/") for root in EXCLUDED_ROOTS)


def _allowlisted(relative: Path) -> bool:
    value = _normal(relative)
    if _excluded(relative):
        return False
    if value in ALLOWED_FILES or value in ALLOWED_TESTS:
        return True
    return any(value == root or value.startswith(root + "/") for root in ALLOWED_DIRS)


def iter_allowlisted_files(source_root: Path) -> Iterable[Path]:
    """Yield regular files in the explicit snapshot surface."""

    for path in source_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source_root)
        if path.suffix == ".pyc" or "__pycache__" in relative.parts:
            continue
        if _allowlisted(relative):
            yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _isolated_v1_smoke(output_root: Path, python_executable: str) -> None:
    """Import and health-check from the snapshot's path in a clean process.

    The isolated interpreter gets the snapshot path explicitly because ``-I``
    ignores ``PYTHONPATH``.  A deny finder makes an accidental import or
    resolution of the optional/private V2 implementation fail closed, even
    when the caller's interpreter has an editable checkout installed.
    """

    code = """
import importlib.abc
import sys
from pathlib import Path
import tempfile

root = Path.cwd().resolve()
sys.path.insert(0, str(root))


class _DenyPrivateV2(importlib.abc.MetaPathFinder):
    _prefixes = (
        "meeting_v2_adaptive",
        "scripts.experimental_distillation",
        "backend.app.core.v2_adaptive",
    )

    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in self._prefixes):
            raise AssertionError("private V2 implementation was imported or resolved")
        return None


sys.meta_path.insert(0, _DenyPrivateV2())


def _assert_local_module(name):
    module = sys.modules.get(name)
    if module is None:
        return
    locations = []
    module_file = getattr(module, "__file__", None)
    if module_file:
        locations.append(Path(module_file).resolve())
    locations.extend(Path(item).resolve() for item in getattr(module, "__path__", ()))
    assert all(item.is_relative_to(root) for item in locations), (name, locations, root)

import backend.app
from backend.app.config import Settings
from backend.app.core_api import create_app
from backend.app.jobs import MeetingJobStore

package_path = Path(backend.app.__file__).resolve()
assert package_path.is_relative_to(root), (package_path, root)
with tempfile.TemporaryDirectory(prefix="meeting-core-smoke-") as temp:
    settings = Settings(
        power_automate_api_key="smoke-only",
        meeting_feedback_tenant_id="smoke",
        meeting_feedback_directory=str(Path(temp) / "feedback"),
    )
    app = create_app(
        core_id="v1-frozen",
        settings=settings,
        job_store=MeetingJobStore(sqlite_path=str(Path(temp) / "jobs.sqlite3")),
    )
    health = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/health")()
    assert health.status == "ok" and health.core_id == "v1-frozen"
for module_name in tuple(sys.modules):
    if module_name == "meeting_v2_adaptive" or module_name.startswith("meeting_v2_adaptive."):
        raise AssertionError("private V2 implementation was imported or resolved")
    if module_name.startswith(("backend", "scripts")):
        _assert_local_module(module_name)
print("V1_SNAPSHOT_SMOKE PASS")
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("MEETING_CORE", None)
    completed = subprocess.run(
        [python_executable, "-I", "-c", code],
        cwd=output_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "isolated V1 snapshot smoke failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def build_snapshot(
    output_directory: Path,
    *,
    source_root: Path = ROOT,
    python_executable: str = sys.executable,
    authorized: bool = False,
) -> dict[str, object]:
    """Create one new snapshot and return its manifest metadata."""

    if not authorized:
        raise PermissionError(
            "Snapshot creation requires explicit authorization; this tool does not decide IP ownership."
        )
    source_root = Path(source_root).resolve()
    output_directory = Path(output_directory).expanduser().resolve()
    if (
        output_directory == source_root
        or output_directory.is_relative_to(source_root)
        or source_root.is_relative_to(output_directory)
    ):
        raise ValueError("snapshot output must be outside the source checkout")
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"snapshot output must be new or empty: {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, object]] = []
    for source in sorted(iter_allowlisted_files(source_root), key=lambda item: _normal(item.relative_to(source_root))):
        relative = source.relative_to(source_root)
        destination = output_directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append({
            "path": _normal(relative),
            "bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
        })

    manifest = {
        "schema_version": "company-shell-snapshot-v1",
        "purpose": "authorized V1 frozen company-shell snapshot",
        "ip_ownership_decision": False,
        "authorized_use_required": True,
        "source_history_copied": False,
        "excluded": sorted(
            EXCLUDED_ROOTS
            | EXCLUDED_FILES
            | {"backend/tests/*v2*", "backend/tests/*v227*", "backend/tests/*core_api*"}
        ),
        "files": copied,
    }
    manifest_path = output_directory / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_hash = _sha256(manifest_path)
    (output_directory / MANIFEST_HASH_NAME).write_text(
        f"{manifest_hash}  {MANIFEST_NAME}\n", encoding="ascii"
    )
    _isolated_v1_smoke(output_directory, python_executable)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an authorized, non-destructive company-shell snapshot"
    )
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--output-directory", type=Path, dest="output_directory")
    parser.add_argument("--authorized", action="store_true", help="confirm the snapshot is authorized")
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--source-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output_directory or args.output
    if output is None:
        parser.error("provide an output directory")
    try:
        manifest = build_snapshot(
            output,
            source_root=args.source_root,
            python_executable=args.python_executable,
            authorized=args.authorized,
        )
    except (FileExistsError, PermissionError, RuntimeError, ValueError, OSError) as exc:
        print(f"SNAPSHOT_FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"SNAPSHOT_READY files={len(manifest['files'])} output={Path(output).resolve()}")
    print("This snapshot does not decide IP ownership; use it only when authorized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
