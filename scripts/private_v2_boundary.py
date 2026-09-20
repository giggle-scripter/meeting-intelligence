"""Shared, secret-free helpers for the private V2 extraction boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_MANIFEST = ROOT / "docs" / "private-v2-extraction-boundary.json"
SCHEMA_VERSION = "private-v2-extraction-boundary-v1"
CANONICAL_V228_MANIFEST_SHA256 = "7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_RE = re.compile(
    r"(?:^|[._-])(secret|secrets|credential|credentials|password|passwd|"
    r"token|api[_-]?key|private[_-]?key|access[_-]?key)(?:[._-]|$)",
    re.IGNORECASE,
)
_SECRET_SUFFIXES = {".env", ".pem", ".key", ".p12", ".pfx", ".jks"}


def normal(path: Path | str) -> str:
    return Path(path).as_posix().lstrip("./")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_boundary_manifest(path: Path = BOUNDARY_MANIFEST) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("STOP_INVALID_PRIVATE_V2_BOUNDARY_MANIFEST")
    if value.get("canonical_v228_manifest_sha256") != CANONICAL_V228_MANIFEST_SHA256:
        raise ValueError("STOP_CANONICAL_V228_MANIFEST_HASH_MISMATCH")
    required_lists = (
        "forbidden_artifact_data_roots",
        "runtime_artifact_data_roots",
        "forbidden_file_patterns",
    )
    if any(not isinstance(value.get(name), list) for name in required_lists):
        raise ValueError("STOP_INVALID_PRIVATE_V2_BOUNDARY_MANIFEST")
    fixed_ids = value.get("fixed_ids")
    if (
        not isinstance(fixed_ids, dict)
        or fixed_ids.get("public_package") != "meeting-task-pipeline"
        or fixed_ids.get("private_package") != "meeting_v2_adaptive"
        or fixed_ids.get("public_core_id") != "v1-frozen"
        or fixed_ids.get("private_core_id") != "v2-adaptive"
    ):
        raise ValueError("STOP_INVALID_PRIVATE_V2_FIXED_IDS")
    shell = value.get("company_shell_snapshot")
    if not isinstance(shell, dict) or not isinstance(shell.get("excluded_roots_must_include"), list) or not isinstance(shell.get("excluded_files_must_include"), list):
        raise ValueError("STOP_INVALID_PRIVATE_V2_SHELL_DECLARATION")
    private = value.get("private_v2")
    if not isinstance(private, dict) or not isinstance(private.get("source_roots"), list) or not isinstance(private.get("source_files"), list):
        raise ValueError("STOP_INVALID_PRIVATE_V2_SOURCE_DECLARATION")
    if len(private["source_files"]) != len(set(private["source_files"])):
        raise ValueError("STOP_DUPLICATE_PRIVATE_V2_SOURCE")
    groups = private.get("required_capability_groups")
    required_groups = {"inference", "feedback_training", "activation_rollback", "adapter"}
    if (
        not isinstance(groups, dict)
        or not required_groups.issubset(groups)
        or any(not isinstance(groups.get(name), list) or not groups[name] for name in required_groups)
    ):
        raise ValueError("STOP_INVALID_PRIVATE_V2_CAPABILITY_GROUPS")
    source_files = set(private["source_files"])
    for name in required_groups:
        group = groups[name]
        if len(group) != len(set(group)) or any(not isinstance(item, str) for item in group):
            raise ValueError("STOP_INVALID_PRIVATE_V2_CAPABILITY_GROUPS")
        if not set(group).issubset(source_files):
            raise ValueError("STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS")
    all_paths = private["source_roots"] + private["source_files"] + value["forbidden_artifact_data_roots"] + value["runtime_artifact_data_roots"]
    if any(not isinstance(item, str) for item in value["forbidden_file_patterns"]):
        raise ValueError("STOP_INVALID_PRIVATE_V2_PATH")
    for item in all_paths:
        if not isinstance(item, str) or not item or Path(item).is_absolute() or ".." in Path(item).parts:
            raise ValueError("STOP_INVALID_PRIVATE_V2_PATH")
    forbidden_roots = value["forbidden_artifact_data_roots"]
    from scripts import build_company_shell_snapshot as company_shell

    for name in required_groups:
        for item in groups[name]:
            if (
                is_secret_like(item)
                or any(is_under(item, root) for root in forbidden_roots)
                or company_shell._allowlisted(Path(item))
            ):
                raise ValueError("STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS")
    return value


def is_under(relative: Path | str, root: Path | str) -> bool:
    value = normal(relative)
    prefix = normal(root).rstrip("/")
    return value == prefix or value.startswith(prefix + "/")


def is_secret_like(path: Path | str) -> bool:
    candidate = Path(path)
    name = candidate.name.casefold()
    if candidate.suffix.casefold() in _SECRET_SUFFIXES:
        return True
    return bool(_SECRET_RE.search(name))


def is_regular_non_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def git_tracked_paths(root: Path = ROOT) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]
