"""Audit the checked-in private V2 extraction boundary without exposing content."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import build_company_shell_snapshot as company_shell
from scripts.private_v2_boundary import (
    BOUNDARY_MANIFEST,
    ROOT,
    git_tracked_paths,
    is_regular_non_symlink,
    is_secret_like,
    is_under,
    load_boundary_manifest,
    normal,
    sha256_file,
)


def _v1_import_audit(root: Path, python_executable: str) -> bool:
    code = """
import sys
from pathlib import Path
root = Path(%r).resolve()
sys.path.insert(0, str(root))
from backend.app.core.registry import get_core
assert get_core("v1-frozen").core_id == "v1-frozen"
private = ("meeting_v2_adaptive", "scripts.experimental_distillation", "backend.app.core.v2_adaptive")
assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules for prefix in private)
""" % str(root)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("MEETING_CORE", None)
    completed = subprocess.run(
        [python_executable, "-I", "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _private_module_files(root: Path, private_roots: list[str]) -> dict[str, str]:
    """Map private import names to existing private Python files.

    The map is built from the declared roots, rather than importing anything,
    so an undeclared file that is imported by a declared file remains visible
    to the audit.
    """

    modules: dict[str, str] = {}
    for relative_root in private_roots:
        root_path = root / relative_root
        if root_path.is_file() and root_path.suffix == ".py" and is_regular_non_symlink(root_path):
            modules[_module_name(root_path, root)] = normal(root_path.relative_to(root))
            continue
        if not root_path.is_dir():
            continue
        for path in root_path.rglob("*.py"):
            if is_regular_non_symlink(path):
                modules[_module_name(path, root)] = normal(path.relative_to(root))
    return modules


def _resolve_import_name(source: Path, root: Path, *, module: str | None, level: int) -> str | None:
    source_module = _module_name(source, root)
    if not source_module:
        return None
    package = source_module if source.name == "__init__.py" else source_module.rpartition(".")[0]
    if level == 0:
        base = []
    else:
        package_parts = package.split(".") if package else []
        if level > len(package_parts) + 1:
            return None
        base = package_parts[: len(package_parts) - level + 1]
    if module:
        base.extend(module.split("."))
    return ".".join(part for part in base if part)


def _private_import_closure_failures(
    *, root: Path, private_files: list[str], private_roots: list[str]
) -> set[tuple[str, str]]:
    """Return undeclared private Python imports found by AST inspection."""

    private_modules = _private_module_files(root, private_roots)
    declared = {normal(item) for item in private_files}
    failures: set[tuple[str, str]] = set()
    for relative in declared:
        source = root / relative
        if not is_regular_non_symlink(source):
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, UnicodeError, SyntaxError):
            failures.add((relative, "<parse>"))
            continue
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_import_name(
                    source,
                    root,
                    module=node.module,
                    level=node.level,
                )
                if resolved:
                    candidates.append(resolved)
                    if node.module is None or resolved not in private_modules:
                        candidates.extend(
                            f"{resolved}.{alias.name}" for alias in node.names if alias.name != "*"
                        )
            for candidate in candidates:
                target = private_modules.get(candidate)
                if target and target not in declared:
                    failures.add((relative, target))
    return failures


def audit_private_import_closure(
    *, root: Path = ROOT, private_files: list[str], private_roots: list[str]
) -> int:
    """Count undeclared existing private files referenced by declared source."""

    return len(
        _private_import_closure_failures(
            root=Path(root).resolve(),
            private_files=private_files,
            private_roots=private_roots,
        )
    )


def _report(status: str, codes: list[str], counts: dict[str, int], hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "status": status,
        "code": "PASS_PRIVATE_V2_BOUNDARY_AUDIT" if status == "PASS" else "STOP_PRIVATE_V2_BOUNDARY_AUDIT",
        "codes": codes,
        "counts": counts,
        "hashes": hashes,
    }


def audit_boundary(
    *,
    root: Path = ROOT,
    manifest_path: Path = BOUNDARY_MANIFEST,
    python_executable: str = sys.executable,
) -> dict[str, Any]:
    """Return a secret-free audit report; no source or data values are emitted."""

    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()
    codes: list[str] = []
    failures: list[str] = []
    counts = {
        "private_source_files": 0,
        "private_source_failures": 0,
        "capability_groups": 0,
        "capability_files": 0,
        "capability_failures": 0,
        "import_closure_failures": 0,
        "tracked_runtime_artifact_data_files": 0,
        "allowlist_failures": 0,
    }
    hashes: dict[str, str] = {}
    try:
        manifest = load_boundary_manifest(manifest_path)
        hashes["boundary_manifest_sha256"] = sha256_file(manifest_path)
        hashes["canonical_v228_manifest_sha256"] = manifest["canonical_v228_manifest_sha256"]
        codes.append("PASS_MANIFEST_VALID")
    except ValueError as exc:
        failures.append(
            "STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS"
            if str(exc) == "STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS"
            else "STOP_INVALID_PRIVATE_V2_BOUNDARY_MANIFEST"
        )
        return _report("FAIL", failures, counts, hashes)
    except (OSError, TypeError, json.JSONDecodeError):
        failures.append("STOP_INVALID_PRIVATE_V2_BOUNDARY_MANIFEST")
        return _report("FAIL", failures, counts, hashes)

    private = manifest["private_v2"]
    private_files = [normal(item) for item in private["source_files"]]
    private_roots = [normal(item) for item in private["source_roots"]]
    counts["private_source_files"] = len(private_files)
    for relative in private_files:
        path = root / relative
        if (
            not any(is_under(relative, source_root) or normal(relative) == source_root for source_root in private_roots)
            or any(is_under(relative, forbidden) for forbidden in manifest["forbidden_artifact_data_roots"])
            or is_secret_like(path)
            or not is_regular_non_symlink(path)
        ):
            counts["private_source_failures"] += 1
    if counts["private_source_failures"]:
        failures.append("STOP_PRIVATE_V2_SOURCE_DECLARATION")
    else:
        codes.append("PASS_PRIVATE_V2_SOURCE_DECLARATION")

    capability_groups = private["required_capability_groups"]
    counts["capability_groups"] = len(capability_groups)
    capability_files = {
        normal(item)
        for group in capability_groups.values()
        for item in group
    }
    counts["capability_files"] = len(capability_files)
    forbidden_roots = [normal(item) for item in manifest["forbidden_artifact_data_roots"]]
    for relative in capability_files:
        path = root / relative
        if (
            relative not in private_files
            or not any(is_under(relative, source_root) for source_root in private_roots)
            or any(is_under(relative, forbidden) for forbidden in forbidden_roots)
            or is_secret_like(path)
            or not is_regular_non_symlink(path)
            or company_shell._allowlisted(Path(relative))
        ):
            counts["capability_failures"] += 1
    if counts["capability_failures"]:
        failures.append("STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS")
    else:
        codes.append("PASS_PRIVATE_V2_CAPABILITY_COMPLETENESS")

    closure_failures = _private_import_closure_failures(
        root=root,
        private_files=private_files,
        private_roots=private_roots,
    )
    counts["import_closure_failures"] = len(closure_failures)
    if counts["import_closure_failures"]:
        failures.append("STOP_PRIVATE_V2_IMPORT_CLOSURE")
    else:
        codes.append("PASS_PRIVATE_V2_IMPORT_CLOSURE")

    snapshot_excluded_roots = {normal(item) for item in company_shell.EXCLUDED_ROOTS}
    snapshot_excluded_files = {normal(item) for item in company_shell.EXCLUDED_FILES}
    required_roots = set(manifest["company_shell_snapshot"]["excluded_roots_must_include"])
    required_files = set(manifest["company_shell_snapshot"]["excluded_files_must_include"])
    if not required_roots.issubset(snapshot_excluded_roots) or not required_files.issubset(snapshot_excluded_files):
        failures.append("STOP_COMPANY_SHELL_EXCLUSION_DECLARATION")
    for relative in private_files:
        if company_shell._allowlisted(Path(relative)):
            counts["allowlist_failures"] += 1
    for relative in manifest["forbidden_artifact_data_roots"]:
        if any(company_shell._allowlisted(Path(relative) / "placeholder") for _ in (0,)):
            counts["allowlist_failures"] += 1
    if counts["allowlist_failures"]:
        failures.append("STOP_COMPANY_SHELL_ALLOWLIST_LEAK")
    else:
        codes.append("PASS_COMPANY_SHELL_ALLOWLIST")

    try:
        tracked = git_tracked_paths(root)
    except (OSError, subprocess.SubprocessError):
        failures.append("STOP_GIT_TRACKED_PATH_AUDIT")
    else:
        runtime_roots = manifest["runtime_artifact_data_roots"]
        counts["tracked_runtime_artifact_data_files"] = sum(
            any(is_under(item, runtime_root) for runtime_root in runtime_roots) for item in tracked
        )
        if counts["tracked_runtime_artifact_data_files"]:
            failures.append("STOP_TRACKED_RUNTIME_ARTIFACT_DATA")
        else:
            codes.append("PASS_RUNTIME_ARTIFACT_DATA_UNTRACKED")

    if _v1_import_audit(root, python_executable):
        codes.append("PASS_V1_IMPORT_PRIVATE_V2_UNLOADED")
    else:
        failures.append("STOP_V1_IMPORT_PRIVATE_V2_LOADED")
    return _report("FAIL" if failures else "PASS", failures or codes, counts, hashes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the private V2 extraction boundary")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, default=BOUNDARY_MANIFEST, help=argparse.SUPPRESS)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    args = parser.parse_args(argv)
    report = audit_boundary(root=args.root, manifest_path=args.manifest, python_executable=args.python_executable)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
