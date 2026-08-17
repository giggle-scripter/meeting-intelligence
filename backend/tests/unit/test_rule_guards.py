from backend.app.ai.rule_guards import evaluate_action


def test_action_guard_rejects_vague_and_pronoun_only_actions() -> None:
    assert evaluate_action("cập nhật lại").reason == "VAGUE_ADMIN"
    assert evaluate_action("handle this").reason == "VAGUE_ADMIN"
    assert evaluate_action("việc đó").reason == "PRONOUN_ONLY"
    assert evaluate_action("gửi").reason == "NO_OBJECT"
    assert evaluate_action("cập nhật lại lên hệ thống").reason == "VAGUE_ADMIN"
    assert evaluate_action("theo dõi tiến độ dự án").reason == "VAGUE_ADMIN"
    assert evaluate_action("gửi lời mời").reason == "VAGUE_ADMIN"
    assert evaluate_action("cấp quyền cho em").reason == "VAGUE_ADMIN"
    assert evaluate_action("sửa theo").reason == "VAGUE_ADMIN"
    assert evaluate_action("update gì không").reason == "VAGUE_ADMIN"


def test_action_guard_accepts_concrete_deliverables() -> None:
    assert evaluate_action("cập nhật dashboard doanh thu").concrete is True
    assert evaluate_action("viết unit test cho module đăng ký").concrete is True
    assert evaluate_action("prepare the release checklist").concrete is True
    assert evaluate_action("cấp quyền truy cập server production").concrete is True
