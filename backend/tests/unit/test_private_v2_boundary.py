from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import audit_private_v2_boundary as audit
from scripts import prepare_private_v2_extraction as extraction
from scripts.private_v2_boundary import BOUNDARY_MANIFEST, load_boundary_manifest


ROOT = Path(__file__).resolve().parents[3]


def test_boundary_audit_passes_without_private_source_or_data_output() -> None:
    report = audit.audit_boundary(root=ROOT)
    assert report["status"] == "PASS"
    assert report["counts"]["private_source_files"] == 14
    assert report["counts"]["capability_groups"] == 4
    assert report["counts"]["import_closure_failures"] == 0
    assert report["counts"]["tracked_runtime_artifact_data_files"] == 0
    assert all("path" not in code.casefold() for code in report["codes"])


def test_boundary_audit_rejects_tampered_canonical_manifest(tmp_path: Path) -> None:
    value = load_boundary_manifest(BOUNDARY_MANIFEST)
    value["canonical_v228_manifest_sha256"] = "0" * 64
    manifest = tmp_path / "boundary.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    report = audit.audit_boundary(root=ROOT, manifest_path=manifest)
    assert report["status"] == "FAIL"
    assert report["codes"] == ["STOP_INVALID_PRIVATE_V2_BOUNDARY_MANIFEST"]
    assert "meeting_v2_adaptive" not in json.dumps(report)


def test_boundary_audit_rejects_manifest_missing_feedback_trainer(tmp_path: Path) -> None:
    value = load_boundary_manifest(BOUNDARY_MANIFEST)
    value["private_v2"]["source_files"].remove(
        "scripts/experimental_distillation/train_v227_feedback.py"
    )
    manifest = tmp_path / "boundary.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    report = audit.audit_boundary(root=ROOT, manifest_path=manifest)
    assert report["status"] == "FAIL"
    assert report["codes"] == ["STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS"]


def test_boundary_audit_rejects_missing_private_import_closure(tmp_path: Path) -> None:
    value = load_boundary_manifest(BOUNDARY_MANIFEST)
    missing = "experiments/distilled_proposal_ranker/v2/protocol_bridge_v218.py"
    value["private_v2"]["source_files"].remove(missing)
    for group in value["private_v2"]["required_capability_groups"].values():
        if missing in group:
            group.remove(missing)
    manifest = tmp_path / "boundary.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    report = audit.audit_boundary(root=ROOT, manifest_path=manifest)
    assert report["status"] == "FAIL"
    assert report["codes"] == ["STOP_PRIVATE_V2_IMPORT_CLOSURE"]
    assert report["counts"]["import_closure_failures"] == 1


def test_extraction_requires_authorization(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="STOP_AUTHORIZATION_REQUIRED"):
        extraction.prepare_extraction(tmp_path / "out")


def test_extraction_rejects_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="STOP_EXTRACTION_OUTPUT_MUST_BE_NEW_OR_EMPTY"):
        extraction.prepare_extraction(output, authorized=True)


def test_extraction_rejects_tampered_boundary_before_writing(tmp_path: Path) -> None:
    value = load_boundary_manifest(BOUNDARY_MANIFEST)
    value["canonical_v228_manifest_sha256"] = "f" * 64
    manifest = tmp_path / "boundary.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="STOP_CANONICAL_V228_MANIFEST_HASH_MISMATCH"):
        extraction.prepare_extraction(output, manifest_path=manifest, authorized=True)
    assert not output.exists()


def test_extraction_rejects_secret_like_declared_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    secret = source / "private_api_key.py"
    secret.write_text("placeholder", encoding="utf-8")
    value = load_boundary_manifest(BOUNDARY_MANIFEST)
    value["private_v2"]["source_roots"] = ["private_api_key.py"]
    value["private_v2"]["source_files"] = ["private_api_key.py"]
    manifest = tmp_path / "boundary.json"
    # Keep the canonical hash unchanged: this tests file-level exclusion.
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="STOP_PRIVATE_V2_CAPABILITY_COMPLETENESS"):
        extraction.prepare_extraction(tmp_path / "out", source_root=source, manifest_path=manifest, authorized=True)


def test_extraction_writes_hashes_and_source_only_label(tmp_path: Path) -> None:
    output = tmp_path / "out"
    result = extraction.prepare_extraction(output, authorized=True)
    assert result["label"] == "migration_source_only"
    assert result["standalone_installable"] is False
    assert result["ownership_decision"] is False
    assert result["counts"] == {
        "private_source_files": 14,
        "capability_groups": 4,
        "capability_files": 14,
    }
    assert (output / "manifest.json").is_file()
    assert (output / "manifest.sha256").is_file()
    copied = {item["path"] for item in result["files"]}
    assert all(not item.startswith(("evaluation/", "data/", "artifacts/", "runtime/")) for item in copied)
    assert all(not item.endswith((".env", ".pem", ".key")) for item in copied)
