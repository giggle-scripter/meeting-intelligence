from backend.app.candidate import build_action_frame


def test_frame_removes_modality_deadline_and_followup_method() -> None:
    frame = build_action_frame(
        "Vâng, em sẽ viết tài liệu API trước thứ Sáu và báo lại nhé.",
        ("C-1",),
    )

    assert frame.valid is True
    assert frame.canonical_action == "Viết tài liệu API"
    assert frame.verb == "Viết"
    assert frame.deliverable == "tài liệu API"
    assert frame.method_steps == ("báo lại nhé",)
    assert frame.grounded_clause_ids == ("C-1",)


def test_frame_preserves_identity_qualifier_and_sibling_object() -> None:
    frame = build_action_frame("Cập nhật tài liệu triển khai staging", ("C-2",))

    assert frame.valid is True
    assert frame.canonical_action == "Cập nhật tài liệu triển khai staging"
    assert frame.object == "tài liệu triển khai staging"


def test_frame_rejects_generic_action_without_object() -> None:
    frame = build_action_frame("Làm và báo lại", ("C-3",))

    assert frame.valid is False
    assert frame.canonical_action == ""
    assert frame.rejection_reason in {"NO_OBJECT", "VAGUE_ADMIN"}
