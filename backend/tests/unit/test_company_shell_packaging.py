from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import build_company_shell_snapshot as snapshot
from scripts import core_preflight


ROOT = Path(__file__).resolve().parents[3]


def _valid_env(tmp_path: Path) -> dict[str, str]:
    return {
        "POWER_AUTOMATE_API_KEY": "secret-that-must-not-be-printed",
        "MEETING_FEEDBACK_TENANT_ID": "tenant-a",
        "MEETING_FEEDBACK_DIRECTORY": str(tmp_path / "feedback"),
        "MEETING_JOB_SQLITE_PATH": str(tmp_path / "runtime" / "jobs.sqlite3"),
    }


def test_launcher_keeps_fixed_local_uvicorn_contract() -> None:
    text = (ROOT / "scripts" / "run_local_meeting_core.ps1").read_text(encoding="utf-8")
    assert "backend.app.core_api:app" in text
    assert "--host 127.0.0.1" in text
    assert "--port 8011" in text
    assert "--workers 1" in text
    assert "scripts.core_preflight" in text
    assert "cloudflare" not in text.casefold()
    assert "trainer" not in text.casefold()


def test_common_preflight_reports_capabilities_without_secret_or_transcript(tmp_path: Path) -> None:
    report = core_preflight.validate_preflight(env=_valid_env(tmp_path), root=ROOT)
    encoded = json.dumps(report, ensure_ascii=False)
    assert report["status"] == "PASS"
    assert report["selected_core"] == "v1-frozen"
    assert report["capabilities"]["adaptive"] is False
    assert "secret-that-must-not-be-printed" not in encoded
    assert "transcript" not in encoded.casefold()


def test_v2_preflight_reports_loaded_challenger_capabilities_after_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _valid_env(tmp_path)
    env.update({
        "V227_ARTIFACT_DIRECTORY": str(tmp_path / "artifacts"),
        "V227_FEEDBACK_DIRECTORY": str(tmp_path / "feedback"),
    })

    monkeypatch.setattr(
        core_preflight.local_v227_preflight,
        "validate_preflight",
        lambda **_: {
            "status": "PASS",
            "checks": [{
                "name": "frozen_artifacts", "status": "PASS",
                "code": "PASS_FROZEN_ARTIFACT_BUNDLE", "message": "validated",
            }],
        },
    )
    real_import = core_preflight.importlib.import_module

    class FakeAdaptiveCore:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["artifact_directory"] == (tmp_path / "artifacts")
            assert kwargs["feedback_directory"] == (tmp_path / "feedback")
            self.capabilities = SimpleNamespace(
                adaptive=True,
                pipeline_version="v227_experimental",
                runtime_model_id="v227-tenant-challenger",
                supports_meeting_note=False,
            )

    def fake_import(name: str, package: str | None = None):
        if name == "meeting_v2_adaptive":
            return SimpleNamespace(V2AdaptiveCore=FakeAdaptiveCore)
        return real_import(name, package)

    monkeypatch.setattr(core_preflight.importlib, "import_module", fake_import)
    report = core_preflight.validate_preflight(core="v2-adaptive", env=env, root=ROOT)

    assert report["status"] == "PASS"
    assert report["capabilities"]["runtime_model_id"] == "v227-tenant-challenger"
    assert report["capabilities"]["pipeline_version"] == "v227_experimental"
    assert report["checks"][-1]["code"] == "PASS_V2_ADAPTER_LOADED"


def test_v2_preflight_fails_secret_free_when_optional_package_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _valid_env(tmp_path)
    monkeypatch.setattr(
        core_preflight.local_v227_preflight,
        "validate_preflight",
        lambda **_: {"status": "PASS", "checks": []},
    )
    real_import = core_preflight.importlib.import_module

    def fake_import(name: str, package: str | None = None):
        if name == "meeting_v2_adaptive":
            raise ModuleNotFoundError("optional package omitted", name=name)
        return real_import(name, package)

    monkeypatch.setattr(core_preflight.importlib, "import_module", fake_import)
    report = core_preflight.validate_preflight(core="v2-adaptive", env=env, root=ROOT)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["status"] == "FAIL"
    assert report["capabilities"] == {}
    assert report["checks"][-1]["code"] == "STOP_V2_ADAPTER_UNAVAILABLE"
    assert "optional package omitted" not in encoded
    assert "secret-that-must-not-be-printed" not in encoded


def test_v1_preflight_does_not_load_optional_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_import(name: str, package: str | None = None):
        if name == "meeting_v2_adaptive":
            raise AssertionError("V2 package should not be loaded for V1")
        return __import__(name, fromlist=["*"])

    monkeypatch.setattr(core_preflight.importlib, "import_module", fail_import)
    report = core_preflight.validate_preflight(env=_valid_env(tmp_path), root=ROOT)

    assert report["status"] == "PASS"
    assert report["selected_core"] == "v1-frozen"


def test_snapshot_excludes_v2_private_surface_and_hashes_files(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    manifest = snapshot.build_snapshot(output, authorized=True, python_executable=sys.executable)
    assert not (output / "meeting_v2_adaptive").exists()
    assert not (output / "scripts" / "experimental_distillation").exists()
    assert not (output / "backend" / "app" / "core" / "v2_adaptive.py").exists()
    assert (output / "manifest.json").is_file()
    assert (output / "manifest.sha256").read_text(encoding="ascii").startswith(
        hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    )
    listed = {item["path"]: item["sha256"] for item in manifest["files"]}
    assert listed["backend/app/core_api.py"] == hashlib.sha256(
        (output / "backend" / "app" / "core_api.py").read_bytes()
    ).hexdigest()


def test_snapshot_refuses_nonempty_output_and_requires_authorization(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(PermissionError):
        snapshot.build_snapshot(tmp_path / "other")
    with pytest.raises(FileExistsError):
        snapshot.build_snapshot(output, authorized=True)


def test_snapshot_builder_runs_from_output_path_only(tmp_path: Path) -> None:
    output = tmp_path / "isolated"
    snapshot.build_snapshot(output, authorized=True, python_executable=sys.executable)
    completed = subprocess.run(
        [sys.executable, "-c", "import backend.app; print(backend.app.__file__)"],
        cwd=output,
        env={"PATH": str(Path(sys.executable).parent), "PYTHONPATH": str(output)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(completed.stdout.strip()).resolve().is_relative_to(output.resolve())
