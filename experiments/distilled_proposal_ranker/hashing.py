"""Canonical hashes, atomic manifests, and output-boundary enforcement."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


RUNTIME_ROOT = Path("evaluation/runtime/experimental-distillation-v1")
MODEL_ROOT = Path("artifacts/models/experimental-distillation-v1")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def resolve_within(repo_root: Path, path: Path, allowed_root: Path) -> Path:
    resolved_repo = repo_root.resolve()
    resolved = (path if path.is_absolute() else resolved_repo / path).resolve()
    allowed = (resolved_repo / allowed_root).resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"output path is outside {allowed_root.as_posix()}: {path}")
    return resolved


def require_runtime_output(repo_root: Path, path: Path) -> Path:
    return resolve_within(repo_root, path, RUNTIME_ROOT)


def require_model_output(repo_root: Path, path: Path) -> Path:
    return resolve_within(repo_root, path, MODEL_ROOT)


def atomic_write_json(path: Path, value: Any, *, pretty: bool = True) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if pretty
        else canonical_json_bytes(value) + b"\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return sha256_bytes(payload)


def completed_manifest_reusable(path: Path, signature: dict[str, Any]) -> bool:
    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    return value.get("status") == "complete" and value.get("signature") == signature
