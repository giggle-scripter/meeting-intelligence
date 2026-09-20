from pathlib import Path

from scripts.audit_power_automate_uploads import audit_upload_set


def test_checked_in_power_automate_uploads_match_validation_sources() -> None:
    report = audit_upload_set(
        Path("data/validation"), Path("data/power_automate_uploads")
    )

    assert report["passed"], report["errors"]
    assert report["source_case_count"] == 86
    assert report["upload_variant_count"] == 172
