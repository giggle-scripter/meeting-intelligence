import importlib.util
import json
from pathlib import Path

from backend.app.quality.validation_gate import verify_split_manifest


def _freezer_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "freeze_blind_split.py"
    spec = importlib.util.spec_from_file_location("freeze_blind_split", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_freezer_creates_a_verified_manifest_for_reviewed_cases(tmp_path: Path) -> None:
    for index in range(20):
        case_id = f"BLIND-{index:02d}"
        case = tmp_path / case_id
        case.mkdir()
        (case / "metadata.json").write_text(json.dumps({
            "case_id": case_id,
            "ground_truth": {"available": True, "reviewer": "Independent QA"},
        }), encoding="utf-8")
        (case / "transcript.txt").write_text("[09:00:00] Lan: Em sẽ gửi báo cáo.", encoding="utf-8")
        (case / "expected_output.json").write_text('{"tasks": []}', encoding="utf-8")

    manifest = _freezer_module().build_manifest(
        tmp_path, split_id="blind-v1", reviewer="Independent QA", frozen_at="2026-08-26T10:00:00+07:00",
    )

    assert verify_split_manifest(manifest, tmp_path).passed
    assert manifest["independence"]["tuned_against"] is False
