from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from scripts.pilot_runtime_protection import (
    OperatorError,
    backup_runtime,
    restore_runtime,
    retention_report,
    verify_backup,
)


def _db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("create table jobs (id integer primary key, value text)")
    connection.execute("insert into jobs(value) values ('committed')")
    connection.commit()
    return connection


def _feedback(root: Path, tenant: str = "pilot-a") -> Path:
    tenant_root = root / tenant
    (tenant_root / "feedback").mkdir(parents=True)
    (tenant_root / "sources").mkdir()
    (tenant_root / "challengers" / "digest").mkdir(parents=True)
    (tenant_root / "feedback" / "job-1.json").write_text(
        '{"transcript":"DO NOT PRINT THIS","approved":true}\n', encoding="utf-8"
    )
    (tenant_root / "sources" / "source-1.json").write_text('{"source":1}\n', encoding="utf-8")
    (tenant_root / "challengers" / "digest" / "manifest.json").write_text('{"hash":"abc"}\n', encoding="utf-8")
    (tenant_root / "active-v227-pointer.json").write_text('{"active":"digest"}\n', encoding="utf-8")
    return tenant_root


def test_backup_verify_restore_roundtrip_and_secret_free_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source_db = tmp_path / "source.sqlite3"
    connection = _db(source_db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    backup = tmp_path / "backup"

    result = backup_runtime(sqlite_job_path=source_db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    assert result["files"] == 5
    assert verify_backup(backup=backup, tenant_id="pilot-a")["files"] == 5
    assert "DO NOT PRINT THIS" not in json.dumps(result)

    target_db = tmp_path / "restored.sqlite3"
    target_feedback = tmp_path / "restored-feedback"
    restored = restore_runtime(
        backup=backup, sqlite_job_path=target_db, feedback_root=target_feedback,
        tenant_id="pilot-a", confirm_restore=True, backend_stopped=True,
    )
    assert restored["files"] == 5
    assert json.loads((target_feedback / "pilot-a" / "feedback" / "job-1.json").read_text())[
        "approved"
    ] is True
    with sqlite3.connect(target_db) as target:
        assert target.execute("select value from jobs").fetchone() == ("committed",)
    assert "DO NOT PRINT THIS" not in json.dumps(restored)
    connection.close()


def test_sqlite_backup_is_consistent_with_live_uncommitted_writer(tmp_path: Path) -> None:
    source_db = tmp_path / "source.sqlite3"
    writer = _db(source_db)
    writer.execute("begin")
    writer.execute("insert into jobs(value) values ('uncommitted')")
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    backup = tmp_path / "backup"
    backup_runtime(sqlite_job_path=source_db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    with sqlite3.connect(backup / "job-store.sqlite3") as snapshot:
        assert snapshot.execute("select value from jobs order by id").fetchall() == [("committed",)]
    writer.rollback()
    writer.close()


def test_verify_rejects_tamper_and_extra_file(tmp_path: Path) -> None:
    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    backup = tmp_path / "backup"
    backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    (backup / "tenant" / "pilot-a" / "feedback" / "job-1.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(OperatorError, match="mismatch"):
        verify_backup(backup=backup)
    (backup / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(OperatorError):
        verify_backup(backup=backup)
    connection.close()


def test_backup_rejects_symlink_and_restore_never_overwrites(tmp_path: Path) -> None:
    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    tenant = _feedback(feedback)
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    try:
        os.symlink(external, tenant / "sources" / "unsafe.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OperatorError, match="symlink"):
        backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=tmp_path / "backup")
    (tenant / "sources" / "unsafe.txt").unlink()
    backup = tmp_path / "backup"
    backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    existing_db = tmp_path / "existing.sqlite3"
    existing_db.write_bytes(b"keep")
    with pytest.raises(OperatorError, match="already exists"):
        restore_runtime(backup=backup, sqlite_job_path=existing_db, feedback_root=tmp_path / "out", tenant_id="pilot-a", confirm_restore=True, backend_stopped=True)
    assert existing_db.read_bytes() == b"keep"
    connection.close()


def test_backup_rejects_output_inside_repo_and_retention_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    # The test uses the actual checkout path to exercise the source boundary.
    from scripts import pilot_runtime_protection as module
    with pytest.raises(OperatorError, match="tracked source"):
        backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=module.ROOT / "backup-under-source")
    old = feedback / "pilot-a" / "feedback" / "old.json"
    old.write_text('{"private":"SECRET"}\n', encoding="utf-8")
    old_timestamp = 1_600_000_000
    os.utime(old, (old_timestamp, old_timestamp))
    report = retention_report(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", cutoff="2021-01-01T00:00:00+00:00")
    assert report["deletion_performed"] is False
    assert any(item["path"] == "feedback/old.json" for item in report["candidates"])
    assert old.exists()
    assert "SECRET" not in json.dumps(report)
    connection.close()


def test_restore_requires_two_operator_acknowledgments(tmp_path: Path) -> None:
    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    backup = tmp_path / "backup"
    backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    kwargs = dict(backup=backup, sqlite_job_path=tmp_path / "new.sqlite3", feedback_root=tmp_path / "out", tenant_id="pilot-a")
    with pytest.raises(OperatorError, match="confirm"):
        restore_runtime(**kwargs, confirm_restore=False, backend_stopped=True)
    with pytest.raises(OperatorError, match="backend-stopped"):
        restore_runtime(**kwargs, confirm_restore=True, backend_stopped=False)
    connection.close()


def test_backup_failure_removes_only_new_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import pilot_runtime_protection as module

    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    output = tmp_path / "failed-backup"

    def fail_copy(source: Path, destination: Path) -> None:
        raise RuntimeError("injected backup copy failure")

    monkeypatch.setattr(module, "_copy_file_atomic", fail_copy)
    with pytest.raises(RuntimeError, match="injected backup copy failure"):
        backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=output)
    assert not output.exists()

    existing = tmp_path / "existing-backup"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(OperatorError, match="already exists"):
        backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=existing)
    assert marker.read_text(encoding="utf-8") == "keep"
    connection.close()


def test_restore_copy_failure_leaves_no_destinations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import pilot_runtime_protection as module

    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    backup = tmp_path / "backup"
    backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    target_db = tmp_path / "restored.sqlite3"
    target_feedback = tmp_path / "restored-feedback"

    def fail_copy(source: Path, destination: Path) -> None:
        raise OSError("injected restore copy failure")

    monkeypatch.setattr(module, "_copy_file_atomic", fail_copy)
    with pytest.raises(OperatorError, match="restore failed"):
        restore_runtime(
            backup=backup, sqlite_job_path=target_db, feedback_root=target_feedback,
            tenant_id="pilot-a", confirm_restore=True, backend_stopped=True,
        )
    assert not target_db.exists()
    assert not (target_feedback / "pilot-a").exists()
    connection.close()


def test_restore_promotion_failure_rolls_back_tenant_and_keeps_empty_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import pilot_runtime_protection as module

    db = tmp_path / "source.sqlite3"
    connection = _db(db)
    feedback = tmp_path / "feedback"
    _feedback(feedback)
    backup = tmp_path / "backup"
    backup_runtime(sqlite_job_path=db, feedback_root=feedback, tenant_id="pilot-a", output=backup)
    target_db = tmp_path / "restored.sqlite3"
    target_feedback = tmp_path / "restored-feedback"
    existing_tenant = target_feedback / "pilot-a"
    existing_tenant.mkdir(parents=True)

    real_replace = module.os.replace

    def fail_sqlite_promotion(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        if Path(destination) == target_db:
            raise OSError("injected SQLite promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_sqlite_promotion)
    with pytest.raises(OperatorError, match="restore failed"):
        restore_runtime(
            backup=backup, sqlite_job_path=target_db, feedback_root=target_feedback,
            tenant_id="pilot-a", confirm_restore=True, backend_stopped=True,
        )
    assert not target_db.exists()
    assert existing_tenant.is_dir()
    assert not any(existing_tenant.iterdir())
    connection.close()
