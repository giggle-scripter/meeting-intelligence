"""Private pilot runtime backup, verification, restore, and retention reports.

The command is deliberately an offline operator tool.  It never starts the
backend, a tunnel, a provider, or the feedback trainer.  It only accepts
explicit paths and a validated tenant id.  Reports contain metadata (paths,
sizes, and hashes) and never read or print transcript/feedback payloads.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME = "manifest.json"
DB_NAME = "job-store.sqlite3"
TENANT_DIRS = ("feedback", "sources", "challengers")
POINTER_NAMES = ("active-v227-pointer.json",)
MANIFEST_SCHEMA = "pilot-runtime-backup-v1"
SECRET_FILE_MARKERS = ("api-key", "apikey", "provider-key", "provider_key", ".env", "credential")


class OperatorError(RuntimeError):
    """An actionable error whose text is safe for operator output."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_tenant(value: str) -> str:
    if not isinstance(value, str) or not TENANT_RE.fullmatch(value):
        raise OperatorError("tenant id must match [A-Za-z0-9._-]{1,64}")
    return value


def _absolute(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or value.strip() == ":memory:":
        raise OperatorError(f"{label} must be an explicit filesystem path")
    return Path(value).expanduser().absolute()


def _repo_root() -> Path:
    probe = ROOT
    while probe != probe.parent:
        if (probe / ".git").exists():
            return probe.resolve()
        probe = probe.parent
    return ROOT.resolve()


def _reject_inside_repo(path: Path, label: str) -> None:
    repo = _repo_root()
    candidate = path.resolve(strict=False)
    try:
        inside = candidate.is_relative_to(repo)
    except AttributeError:  # pragma: no cover - Python 3.11 has is_relative_to
        inside = str(candidate).lower().startswith(str(repo).lower() + os.sep)
    if inside:
        raise OperatorError(f"{label} must be outside the tracked source checkout")


def _ensure_regular_file(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise OperatorError(f"{label} does not exist") from exc
    except OSError as exc:
        raise OperatorError(f"{label} could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OperatorError(f"{label} must be a regular file")


def _lexists(path: Path) -> bool:
    """Like Path.exists(), but also true for a broken symlink."""
    return os.path.lexists(str(path))


def _ensure_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise OperatorError(f"{label} must be an existing directory") from exc
    except OSError as exc:
        raise OperatorError(f"{label} could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OperatorError(f"{label} must be an existing directory")


def _reject_unsafe_tree(root: Path) -> None:
    """Reject symlinks and non-regular files without following anything."""
    if not _lexists(root):
        return
    if root.is_symlink():
        raise OperatorError("feedback tree contains a symlink")
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in dirs:
            path = current_path / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise OperatorError("feedback tree contains a symlink")
            if not stat.S_ISDIR(info.st_mode):
                raise OperatorError("feedback tree contains a special file")
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = current_path / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise OperatorError("feedback tree contains a symlink")
            if not stat.S_ISREG(info.st_mode):
                raise OperatorError("feedback tree contains an unsafe or special file")
            if any(marker in path.name.lower() for marker in SECRET_FILE_MARKERS):
                raise OperatorError("feedback tree contains a prohibited key or secret file")


def _iter_selected_files(feedback_root: Path, tenant_id: str) -> Iterable[tuple[Path, str]]:
    tenant = feedback_root / tenant_id
    _reject_unsafe_tree(tenant)
    for directory in TENANT_DIRS:
        base = tenant / directory
        if not _lexists(base):
            continue
        if not base.is_dir() or base.is_symlink():
            raise OperatorError(f"tenant {directory} path must be a directory")
        for path in sorted(base.rglob("*")):
            info = path.lstat()
            if info.st_mode and stat.S_ISLNK(info.st_mode):
                raise OperatorError("feedback tree contains a symlink")
            if path.is_dir():
                continue
            if not stat.S_ISREG(info.st_mode):
                raise OperatorError("feedback tree contains an unsafe or special file")
            if any(marker in path.name.lower() for marker in SECRET_FILE_MARKERS):
                raise OperatorError("feedback tree contains a prohibited key or secret file")
            yield path, path.relative_to(tenant).as_posix()
    for name in POINTER_NAMES:
        path = tenant / name
        if not _lexists(path):
            continue
        _ensure_regular_file(path, f"pointer {name}")
        yield path, name


def _manifest_files(backup: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise OperatorError("manifest files must be an array")
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise OperatorError("manifest contains an invalid file entry")
        relative = entry["path"]
        size = entry["size"]
        digest = entry["sha256"]
        if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative or relative.startswith("/"):
            raise OperatorError("manifest contains an unsafe path")
        candidate = Path(relative)
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise OperatorError("manifest contains an unsafe path")
        if any(ord(character) < 32 for character in relative):
            raise OperatorError("manifest contains an unsafe path")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise OperatorError("manifest contains invalid size or SHA-256")
        if relative in seen:
            raise OperatorError("manifest contains duplicate paths")
        seen.add(relative)
        clean.append({"path": relative, "size": size, "sha256": digest})
    if not any(entry["path"] == DB_NAME for entry in clean):
        raise OperatorError("manifest does not contain the SQLite snapshot")
    return clean


def _load_manifest(backup: Path) -> dict[str, Any]:
    _ensure_regular_file(backup / MANIFEST_NAME, "manifest")
    try:
        value = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError("manifest is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA:
        raise OperatorError("unsupported runtime backup manifest")
    tenant = value.get("tenant_id")
    _validate_tenant(tenant)
    if not isinstance(value.get("files"), list):
        raise OperatorError("manifest files must be an array")
    _manifest_files(backup, value)
    return value


def _verify_files(backup: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    entries = _manifest_files(backup, manifest)
    expected = {entry["path"] for entry in entries}
    expected_dirs = {""}
    for relative in expected:
        parent = Path(relative).parent
        while str(parent) not in {"", "."}:
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    actual: set[str] = set()
    for current, dirs, files in os.walk(backup, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            path = current_path / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OperatorError("backup contains a symlink or special file")
            if path.relative_to(backup).as_posix() not in expected_dirs:
                raise OperatorError("backup contains an extra path")
        for name in files:
            path = current_path / name
            relative = path.relative_to(backup).as_posix()
            if relative == MANIFEST_NAME:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise OperatorError("backup contains a symlink or special file")
            actual.add(relative)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = ""
        if missing:
            detail += " missing manifest file"
        if extra:
            detail += " extra file"
        raise OperatorError(f"backup file set mismatch:{detail}")
    for entry in entries:
        path = backup / Path(entry["path"])
        try:
            size = path.stat().st_size
            digest = _sha256(path)
        except OSError as exc:
            raise OperatorError("backup file could not be hashed") from exc
        if size != entry["size"] or digest != entry["sha256"]:
            raise OperatorError("backup hash or size mismatch")
    return {"files": len(entries), "bytes": sum(entry["size"] for entry in entries)}


def _copy_sqlite_snapshot(source: Path, destination: Path) -> None:
    """Create a consistent snapshot using sqlite3.Connection.backup()."""
    _ensure_regular_file(source, "SQLite job store")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
        source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            target_connection = sqlite3.connect(temporary)
            try:
                source_connection.backup(target_connection)
                target_connection.commit()
            finally:
                target_connection.close()
        finally:
            source_connection.close()
        os.replace(temporary, destination)
        temporary = None
    except (OSError, sqlite3.Error) as exc:
        raise OperatorError("SQLite snapshot failed") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, stream, length=1024 * 1024)
            stream.flush()
            os.fsync(stream.fileno())
        if _lexists(destination):
            raise OperatorError(f"restore destination already exists: {destination.name}")
        os.replace(temporary, destination)
        temporary = None
    except OperatorError:
        raise
    except OSError as exc:
        raise OperatorError("restore copy failed") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _remove_created_directory(path: Path) -> None:
    """Remove a directory created by this operation, never a replacement path."""
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def backup_runtime(*, sqlite_job_path: Path, feedback_root: Path, tenant_id: str, output: Path) -> dict[str, Any]:
    tenant_id = _validate_tenant(tenant_id)
    sqlite_job_path = sqlite_job_path.absolute()
    feedback_root = feedback_root.absolute()
    output = output.absolute()
    _reject_inside_repo(output, "backup output")
    if output.exists():
        raise OperatorError("backup output directory already exists")
    _ensure_directory(feedback_root, "feedback root")
    _ensure_regular_file(sqlite_job_path, "SQLite job store")
    # Scan before creating output, so unsafe input never leaves a partial backup.
    selected = list(_iter_selected_files(feedback_root, tenant_id))
    created_output = False
    completed = False
    try:
        output.mkdir(parents=True)
        created_output = True
        tenant_output = output / "tenant" / tenant_id
        for source, relative in selected:
            target = tenant_output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_atomic(source, target)
        _copy_sqlite_snapshot(sqlite_job_path, output / DB_NAME)
        entries: list[dict[str, Any]] = []
        for path in sorted(output.rglob("*")):
            if path.is_file() and path.relative_to(output).as_posix() != MANIFEST_NAME:
                entries.append({
                    "path": path.relative_to(output).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                })
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "tenant_id": tenant_id,
            "files": entries,
        }
        (output / MANIFEST_NAME).write_text(_canonical(manifest) + "\n", encoding="utf-8", newline="\n")
        verified = _verify_files(output, manifest)
        completed = True
        return {"operation": "backup", "tenant_id": tenant_id, "backup": str(output), **verified}
    except OperatorError:
        raise
    except (OSError, UnicodeError) as exc:
        raise OperatorError("backup failed") from exc
    finally:
        if created_output and not completed:
            _remove_created_directory(output)


def verify_backup(*, backup: Path, tenant_id: str | None = None) -> dict[str, Any]:
    backup = backup.absolute()
    if not backup.is_dir():
        raise OperatorError("backup must be an existing directory")
    manifest = _load_manifest(backup)
    if tenant_id is not None and _validate_tenant(tenant_id) != manifest["tenant_id"]:
        raise OperatorError("manifest tenant does not match requested tenant")
    verified = _verify_files(backup, manifest)
    return {"operation": "verify", "tenant_id": manifest["tenant_id"], "backup": str(backup), **verified}


def _empty_or_missing(path: Path) -> bool:
    if not _lexists(path):
        return True
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    return not any(path.iterdir())


def _tenant_tree_matches(root: Path, manifest: dict[str, Any], tenant_id: str) -> bool:
    """Return whether a promoted tenant tree exactly matches its manifest entries."""
    prefix = f"tenant/{tenant_id}/"
    entries = {
        entry["path"][len(prefix):]: entry
        for entry in _manifest_files(root.parent, manifest)
        if entry["path"].startswith(prefix)
    }
    try:
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
        actual: set[str] = set()
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in dirs:
                path = current_path / name
                child = path.lstat()
                if stat.S_ISLNK(child.st_mode) or not stat.S_ISDIR(child.st_mode):
                    return False
            for name in files:
                path = current_path / name
                child = path.lstat()
                if stat.S_ISLNK(child.st_mode) or not stat.S_ISREG(child.st_mode):
                    return False
                actual.add(path.relative_to(root).as_posix())
        if actual != set(entries):
            return False
        for relative, entry in entries.items():
            path = root / relative
            if path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
                return False
    except OSError:
        return False
    return True


def restore_runtime(*, backup: Path, sqlite_job_path: Path, feedback_root: Path, tenant_id: str,
                    confirm_restore: bool, backend_stopped: bool) -> dict[str, Any]:
    tenant_id = _validate_tenant(tenant_id)
    if not confirm_restore:
        raise OperatorError("restore requires --confirm-restore")
    if not backend_stopped:
        raise OperatorError("restore requires --backend-stopped acknowledgment")
    backup = backup.absolute()
    sqlite_job_path = sqlite_job_path.absolute()
    feedback_root = feedback_root.absolute()
    verified = verify_backup(backup=backup, tenant_id=tenant_id)
    manifest = _load_manifest(backup)
    if _lexists(sqlite_job_path):
        raise OperatorError("restore SQLite destination already exists; refusing overwrite")
    destination_tenant = feedback_root / tenant_id
    destination_exists = _lexists(destination_tenant)
    if destination_exists and not _empty_or_missing(destination_tenant):
        raise OperatorError("restore tenant destination must be new or empty")
    if _lexists(feedback_root):
        _ensure_directory(feedback_root, "feedback root")
    else:
        feedback_root.mkdir(parents=True, exist_ok=True)
    sqlite_job_path.parent.mkdir(parents=True, exist_ok=True)
    stage: Path | None = None
    tenant_backup: Path | None = None
    tenant_backup_moved = False
    tenant_promotion_started = False
    tenant_promoted = False
    sqlite_promotion_started = False
    sqlite_promoted = False
    completed = False
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{tenant_id}.restore-", dir=feedback_root))
        for entry in _manifest_files(backup, manifest):
            relative = entry["path"]
            source = backup / Path(relative)
            if relative != DB_NAME and not relative.startswith(f"tenant/{tenant_id}/"):
                raise OperatorError("manifest contains a file outside selected tenant tree")
            target = stage / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_atomic(source, target)
        # Verify every staged tenant file and the staged SQLite snapshot before
        # changing either destination.  The stage mirrors the backup layout.
        _verify_files(stage, manifest)
        staged_tenant = stage / "tenant" / tenant_id
        if not staged_tenant.exists():
            staged_tenant.mkdir(parents=True)
        staged_sqlite = stage / DB_NAME

        # Recheck no-overwrite conditions immediately before promotion.  An
        # empty pre-existing tenant directory is moved aside and restored on
        # failure, so it is never populated one file at a time.
        if _lexists(sqlite_job_path):
            raise OperatorError("restore SQLite destination already exists; refusing overwrite")
        if destination_exists:
            if not _lexists(destination_tenant) or not _empty_or_missing(destination_tenant):
                raise OperatorError("restore tenant destination changed during restore")
            tenant_backup = Path(tempfile.mkdtemp(prefix=f".{tenant_id}.restore-old-", dir=feedback_root))
            tenant_backup.rmdir()
            os.replace(destination_tenant, tenant_backup)
            tenant_backup_moved = True
        elif _lexists(destination_tenant):
            raise OperatorError("restore tenant destination appeared during restore")

        tenant_promotion_started = True
        os.replace(staged_tenant, destination_tenant)
        tenant_promoted = True
        if _lexists(sqlite_job_path):
            raise OperatorError("restore SQLite destination appeared during restore")
        sqlite_promotion_started = True
        os.replace(staged_sqlite, sqlite_job_path)
        sqlite_promoted = True
        completed = True
    except OperatorError:
        raise
    except OSError as exc:
        raise OperatorError("restore failed") from exc
    finally:
        if not completed:
            # A fault injector (or an interrupted rename) may raise after the
            # OS has already completed the rename.  Detect that case from the
            # source disappearing and an exact destination match before doing
            # rollback cleanup.
            if (
                tenant_promotion_started
                and not tenant_promoted
                and not _lexists(stage / "tenant" / tenant_id)
                and _tenant_tree_matches(destination_tenant, manifest, tenant_id)
            ):
                tenant_promoted = True
            if (
                sqlite_promotion_started
                and not sqlite_promoted
                and not _lexists(stage / DB_NAME)
            ):
                db_entry = next(entry for entry in manifest["files"] if entry["path"] == DB_NAME)
                try:
                    sqlite_promoted = (
                        _ensure_regular_file(sqlite_job_path, "SQLite restore destination") is None
                        and sqlite_job_path.stat().st_size == db_entry["size"]
                        and _sha256(sqlite_job_path) == db_entry["sha256"]
                    )
                except OperatorError:
                    sqlite_promoted = False
            if sqlite_promoted:
                try:
                    sqlite_job_path.unlink()
                except OSError:
                    pass
            if tenant_promoted:
                _remove_created_directory(destination_tenant)
            if tenant_backup_moved and tenant_backup is not None:
                try:
                    if not _lexists(destination_tenant):
                        os.replace(tenant_backup, destination_tenant)
                except OSError:
                    pass
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if completed and tenant_backup is not None:
            _remove_created_directory(tenant_backup)
    return {"operation": "restore", "tenant_id": tenant_id, "backup": str(backup),
            "sqlite_job_path": str(sqlite_job_path), "feedback_tenant": str(destination_tenant),
            "files": verified["files"], "bytes": verified["bytes"]}


def _parse_cutoff(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorError("cutoff must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperatorError("cutoff must include a timezone")
    return parsed.astimezone(timezone.utc)


def retention_report(*, sqlite_job_path: Path, feedback_root: Path, tenant_id: str, cutoff: str) -> dict[str, Any]:
    tenant_id = _validate_tenant(tenant_id)
    cutoff_time = _parse_cutoff(cutoff)
    sqlite_job_path = sqlite_job_path.absolute()
    feedback_root = feedback_root.absolute()
    _ensure_directory(feedback_root, "feedback root")
    _reject_unsafe_tree(feedback_root / tenant_id)
    candidates: list[dict[str, Any]] = []
    scanned: list[Path] = []
    now = datetime.now(timezone.utc)
    if _lexists(sqlite_job_path):
        _ensure_regular_file(sqlite_job_path, "SQLite job store")
        scanned.append(sqlite_job_path)
    scanned.extend(source for source, _ in _iter_selected_files(feedback_root, tenant_id))
    for path in scanned:
        info = path.stat()
        modified = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
        if modified < cutoff_time:
            relative = path.name if path == sqlite_job_path else path.relative_to(feedback_root / tenant_id).as_posix()
            candidates.append({
                "path": relative,
                "size": info.st_size,
                "modified": modified.isoformat(),
                "age_seconds": max(0, int((now - modified).total_seconds())),
            })
    candidates.sort(key=lambda item: item["path"])
    total_bytes = sum(path.stat().st_size for path in scanned)
    return {
        "operation": "retention",
        "tenant_id": tenant_id,
        "cutoff": cutoff_time.isoformat(),
        "scanned_files": len(scanned),
        "scanned_bytes": total_bytes,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(item["size"] for item in candidates),
        "candidates": candidates,
        "deletion_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private pilot runtime protection CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="create a consistent private backup")
    backup.add_argument("--sqlite-job-path", required=True)
    backup.add_argument("--feedback-root", required=True)
    backup.add_argument("--tenant-id", required=True)
    backup.add_argument("--output", required=True)
    verify = sub.add_parser("verify", help="verify a backup manifest and all files")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--tenant-id", required=True)
    restore = sub.add_parser("restore", help="restore into new destinations")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--sqlite-job-path", required=True)
    restore.add_argument("--feedback-root", required=True)
    restore.add_argument("--tenant-id", required=True)
    restore.add_argument("--confirm-restore", action="store_true")
    restore.add_argument("--backend-stopped", action="store_true")
    retention = sub.add_parser("retention", help="report old files without deleting")
    retention.add_argument("--sqlite-job-path", required=True)
    retention.add_argument("--feedback-root", required=True)
    retention.add_argument("--tenant-id", required=True)
    retention.add_argument("--cutoff", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = backup_runtime(sqlite_job_path=_absolute(args.sqlite_job_path, "--sqlite-job-path"), feedback_root=_absolute(args.feedback_root, "--feedback-root"), tenant_id=args.tenant_id, output=_absolute(args.output, "--output"))
        elif args.command == "verify":
            result = verify_backup(backup=_absolute(args.backup, "--backup"), tenant_id=args.tenant_id)
        elif args.command == "restore":
            result = restore_runtime(backup=_absolute(args.backup, "--backup"), sqlite_job_path=_absolute(args.sqlite_job_path, "--sqlite-job-path"), feedback_root=_absolute(args.feedback_root, "--feedback-root"), tenant_id=args.tenant_id, confirm_restore=args.confirm_restore, backend_stopped=args.backend_stopped)
        else:
            result = retention_report(sqlite_job_path=_absolute(args.sqlite_job_path, "--sqlite-job-path"), feedback_root=_absolute(args.feedback_root, "--feedback-root"), tenant_id=args.tenant_id, cutoff=args.cutoff)
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(_canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
