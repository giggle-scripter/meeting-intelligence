from __future__ import annotations

from scripts.experimental_distillation.audit_v225_upstream_candidates import (
    RuntimeRow,
    family_keys,
    merge_rows,
    norm,
)


def row(source: str, name: str, owner: str, ordinal: int, **fields: str) -> RuntimeRow:
    task = {"task_name": name, "assignee": owner, "status": "Proposed", **fields}
    return RuntimeRow("CASE", task, source, ordinal, (0.5, 0, -ordinal, name), (source,))


def test_v225_normalization_is_accent_and_case_insensitive() -> None:
    assert norm("  Đăng ký   API  ") == "dang ky api"
    assert norm("đăng ký api") == norm("ĐĂNG KÝ API")


def test_v225_dedupe_is_priority_ordered_and_fills_missing_fields() -> None:
    merged = merge_rows([
        row("action_candidates", "Đăng ký API", "Lan", 0, due_date=""),
        row("accepted_final", "dang ky api", "Lan", 9, due_date="2026-02-20"),
    ])
    assert len(merged) == 1
    assert merged[0].source == "accepted_final"
    assert merged[0].task["due_date"] == "2026-02-20"


def test_v225_partition_keys_match_corpus_convention() -> None:
    keys = family_keys("W3-MED-C4-N2-PROD-INT-019")
    assert keys == {"template": "INT", "family": "PROD-INT"}
