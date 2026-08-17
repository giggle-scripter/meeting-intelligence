from backend.app.models import FinalTask
from backend.app.output.summary_builder import build_summary


def test_summary_is_concise_and_user_facing() -> None:
    tasks = [
        FinalTask(
            "Fix lỗi",
            "Lan",
            "2026-07-29",
            "",
            "3 ngày làm việc sau khi nhận spec từ CS",
            "",
        ),
        FinalTask(
            "Kiểm tra module liên quan",
            "Hoàng",
            "2026-07-29",
            "2026-07-29",
            "trong sáng nay",
            "",
        ),
    ]

    assert build_summary("Xử lý lỗi tích hợp", tasks) == (
        "Cuộc họp tập trung vào xử lý lỗi tích hợp. "
        "2 công việc được chốt: Lan sẽ fix lỗi "
        "(3 ngày làm việc sau khi nhận spec từ CS); Hoàng sẽ kiểm tra "
        "module liên quan (trong sáng nay)."
    )


def test_summary_for_no_tasks_is_vietnamese() -> None:
    assert build_summary("Trao đổi kế hoạch tích hợp", []) == (
        "Cuộc họp tập trung vào trao đổi kế hoạch tích hợp. "
        "Chưa chốt công việc mới."
    )


def test_summary_explains_cancellation_only_meeting() -> None:
    assert build_summary(
        "Theo dõi lỗi và tài liệu",
        [],
        no_active_reason="cancelled",
    ) == (
        "Cuộc họp tập trung vào theo dõi lỗi và tài liệu. "
        "Các task cũ được đề cập đã bị hủy; không có task active mới."
    )


def test_summary_does_not_repeat_handoff_qualifier() -> None:
    task = FinalTask(
        "Giám sát release đến thứ Tư",
        "Hùng",
        "2026-07-30",
        "2026-08-05",
        "đến thứ Tư",
        "PL: Hùng giám sát đến thứ Tư.",
    )

    assert build_summary("Phối hợp release", [task]) == (
        "Cuộc họp tập trung vào phối hợp release. "
        "1 công việc được chốt: Hùng sẽ giám sát release đến thứ Tư."
    )


def test_summary_does_not_expose_technical_meeting_id() -> None:
    assert build_summary("W2-SHORT-C2-N0-IT-BRST-006", []) == "Chưa chốt công việc mới."


def test_summary_derives_a_topic_from_tasks_when_metadata_is_generic() -> None:
    tasks = [
        FinalTask("Chuẩn bị hồ sơ dự án", "Phương", "2026-07-29", "", "", ""),
        FinalTask("Cập nhật access matrix", "Linh", "2026-07-29", "", "", ""),
    ]

    assert build_summary("transcript", tasks) == (
        "Cuộc họp tập trung vào chuẩn bị hồ sơ dự án và cập nhật access matrix. "
        "2 công việc được chốt: Phương sẽ chuẩn bị hồ sơ dự án; "
        "Linh sẽ cập nhật access matrix."
    )

    assert build_summary("smoke_001", tasks) == (
        "Cuộc họp tập trung vào chuẩn bị hồ sơ dự án và cập nhật access matrix. "
        "2 công việc được chốt: Phương sẽ chuẩn bị hồ sơ dự án; "
        "Linh sẽ cập nhật access matrix."
    )


def test_summary_uses_a_natural_review_sentence() -> None:
    assert build_summary("Rà soát công việc phát hành", []) == (
        "Cuộc họp rà soát công việc phát hành. Chưa chốt công việc mới."
    )


def test_summary_preserves_topic_acronyms() -> None:
    assert build_summary("API Review", []) == (
        "Cuộc họp tập trung vào API Review. Chưa chốt công việc mới."
    )


def test_summary_preserves_english_title_case() -> None:
    assert build_summary("Access Review", []) == (
        "Cuộc họp tập trung vào Access Review. Chưa chốt công việc mới."
    )
