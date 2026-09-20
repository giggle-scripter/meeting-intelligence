"""Prepare an authorized, source-only private V2 migration package.

The output is deliberately not a Python package or runtime bundle.  It is a
hashable hand-off of the declared source files for a separately controlled
repository migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import build_company_shell_snapshot as company_shell
from scripts.private_v2_boundary import (
    BOUNDARY_MANIFEST,
    ROOT,
    is_regular_non_symlink,
    is_secret_like,
    is_under,
    load_boundary_manifest,
    normal,
    sha256_file,
)


MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"


def _validate_source_paths(root: Path, manifest: dict[str, Any]) -> list[tuple[str, Path]]:
    private = manifest["private_v2"]
    forbidden = manifest["forbidden_artifact_data_roots"]
    sources: list[tuple[str, Path]] = []
    for item in private["source_files"]:
        relative = normal(item)
        path = root / relative
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(root.resolve())
            or any(is_under(relative, forbidden_root) for forbidden_root in forbidden)
            or is_secret_like(path)
            or company_shell._allowlisted(Path(relative))
            or not is_regular_non_symlink(path)
        ):
            raise ValueError("STOP_PRIVATE_V2_SOURCE_REJECTED")
        sources.append((relative, path))
    return sources


def prepare_extraction(
    output_directory: Path,
    *,
    source_root: Path = ROOT,
    manifest_path: Path = BOUNDARY_MANIFEST,
    authorized: bool = False,
) -> dict[str, Any]:
    """Copy only the declared private source files to a new external output."""

    if not authorized:
        raise PermissionError("STOP_AUTHORIZATION_REQUIRED")
    source_root = Path(source_root).resolve()
    output_directory = Path(output_directory).expanduser().resolve()
    if output_directory == source_root or output_directory.is_relative_to(source_root) or source_root.is_relative_to(output_directory):
        raise ValueError("STOP_EXTRACTION_OUTPUT_MUST_BE_OUTSIDE_CHECKOUT")
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError("STOP_EXTRACTION_OUTPUT_MUST_BE_NEW_OR_EMPTY")

    manifest_path = Path(manifest_path).resolve()
    boundary = load_boundary_manifest(manifest_path)
    sources = _validate_source_paths(source_root, boundary)
    output_directory.mkdir(parents=True, exist_ok=True)
    try:
        copied: list[dict[str, Any]] = []
        for relative, source in sources:
            destination = output_directory / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
            if not is_regular_non_symlink(destination):
                raise ValueError("STOP_EXTRACTION_DESTINATION_REJECTED")
            copied.append({
                "path": relative,
                "bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            })
        output_manifest: dict[str, Any] = {
            "schema_version": "private-v2-migration-source-only-v1",
            "label": "migration_source_only",
            "standalone_installable": False,
            "ownership_decision": False,
            "source_history_copied": False,
            "evaluation_runtime_included": False,
            "datasets_included": False,
            "feedback_included": False,
            "keys_included": False,
            "artifacts_included": False,
            "boundary_manifest_sha256": sha256_file(manifest_path),
            "canonical_v228_manifest_sha256": boundary["canonical_v228_manifest_sha256"],
            "required_capability_groups": boundary["private_v2"]["required_capability_groups"],
            "counts": {
                "private_source_files": len(copied),
                "capability_groups": len(boundary["private_v2"]["required_capability_groups"]),
                "capability_files": len({
                    item
                    for group in boundary["private_v2"]["required_capability_groups"].values()
                    for item in group
                }),
            },
            "files": copied,
        }
        manifest_file = output_directory / MANIFEST_NAME
        manifest_file.write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_directory / MANIFEST_HASH_NAME).write_text(
            f"{sha256_file(manifest_file)}  {MANIFEST_NAME}\n", encoding="ascii"
        )
        return output_manifest
    except Exception:
        shutil.rmtree(output_directory)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare an authorized private V2 migration source hand-off")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--output-directory", type=Path, dest="output_directory")
    parser.add_argument("--authorized", action="store_true", help="confirm this source-only extraction is authorized")
    parser.add_argument("--source-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, default=BOUNDARY_MANIFEST, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output_directory or args.output
    if output is None:
        parser.error("provide an output directory")
    try:
        result = prepare_extraction(output, source_root=args.source_root, manifest_path=args.manifest, authorized=args.authorized)
    except (FileExistsError, PermissionError, ValueError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "PASS", "code": "PRIVATE_V2_EXTRACTION_READY", "files": len(result["files"]), "label": result["label"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
