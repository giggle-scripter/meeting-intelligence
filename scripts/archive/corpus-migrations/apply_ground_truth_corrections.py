"""Apply transcript-reviewed corrections to generated expected outputs."""

from __future__ import annotations

import json
from pathlib import Path


DATASET = Path("data/validation")


def task(
    name: str,
    assignee: str,
    start: str,
    due: str = "",
    due_text: str = "",
) -> dict[str, str]:
    return {
        "task_name": name,
        "assignee": assignee,
        "start_date": start,
        "due_date": due,
        "due_date_text": due_text,
        "status": "Proposed",
    }


REPLACEMENTS: dict[str, list[dict[str, str]]] = {
    "W3-MED-C3-N1-IT-INT-002": [
        task("Fix connection error API", "Đức", "2026-02-06", "2026-02-11", "trước thứ Năm"),
        task("Hoàn thiện dữ liệu mẫu", "Lan", "2026-02-06", "2026-02-06", "cuối ngày hôm nay"),
        task("Review dữ liệu mẫu", "Tuấn", "2026-02-06", "", "sau khi Lan bàn giao"),
        task("Xử lý adapter token", "Tuấn", "2026-02-06"),
        task("Phân quyền truy cập", "Hoa", "2026-02-06", "", "cuối tuần này"),
    ],
    "W3-MED-C3-N1-IT-INT-006": [
        task("Hoàn thiện tài liệu API", "Mai", "2026-02-10", "2026-02-20", "deadline 20/02"),
        task("Viết script dữ liệu mẫu", "Tuấn", "2026-02-10"),
        task("Kiểm tra dữ liệu mẫu", "Linh", "2026-02-10", "", "trong 2 ngày sau khi có script"),
        task("Kiểm tra quyền truy cập", "Linh", "2026-02-10", "2026-02-18", "trước 18/02"),
        task("Setup môi trường staging", "Hùng", "2026-02-10", "2026-02-24", "deadline mới là 24/02"),
    ],
    "W3-MED-C3-N1-OPS-INT-012": [
        task("Review feature A", "Bình", "2026-02-16", "2026-02-19", "deadline thứ Năm"),
        task("Fix bug login", "Bình", "2026-02-16", "2026-02-17", "cuối ngày mai"),
        task("Cập nhật monitoring", "Dũng", "2026-02-16", "2026-02-20", "thứ Sáu"),
        task("Backup config", "Dũng", "2026-02-16"),
        task("Chạy regression test", "Chi", "2026-02-16", "2026-02-20", "deadline thứ Sáu"),
        task("Cập nhật tài liệu release", "Chi", "2026-02-16"),
        task("Viết tài liệu kỹ thuật", "Chi", "2026-02-16"),
        task("Review tài liệu và tổ chức buổi review", "An", "2026-02-16", "2026-02-20", "thứ Sáu"),
    ],
    "W3-MED-C3-N1-OPS-INT-016": [
        task("Fix bug #234", "Minh", "2026-02-20", "2026-02-22", "22/02"),
        task("Cập nhật dashboard", "Huy", "2026-02-20", "2026-02-24", "24/02"),
        task("Viết release notes", "Trang", "2026-02-20"),
        task("Viết hướng dẫn sử dụng", "Anh", "2026-02-20", "2026-02-26", "26/02"),
        task("Chuẩn bị staging", "Trang", "2026-02-20", "2026-02-23", "23/02"),
        task("Test load", "Huy", "2026-02-20", "2026-02-25", "25/02"),
        task("Nghiên cứu API cũ", "Anh", "2026-02-20"),
    ],
    "W3-MED-C3-N1-OPS-INT-020": [
        task("Cập nhật version các service trừ logging", "Minh", "2026-02-24", "2026-02-27", "hạn thứ Sáu"),
        task("Fix bug #4521", "Minh", "2026-02-24", "2026-02-26", "hạn chót là thứ Năm tuần này"),
        task("Viết tài liệu kỹ thuật", "Minh", "2026-02-24", "", "trước release"),
        task("Giám sát release từ thứ Năm", "Minh", "2026-02-24"),
        task("Giám sát release đến hết thứ Tư", "Hùng", "2026-02-24", "2026-02-25", "đến hết thứ Tư"),
        task("Setup metric giám sát", "Hùng", "2026-02-24", "2026-03-03", "hạn thứ Ba"),
        task("Gửi log và chuẩn bị test case", "Lan", "2026-02-24"),
        task("Review báo cáo giám sát", "PL", "2026-02-24"),
    ],
    "W3-MED-C4-N2-PROD-INT-003": [
        task("Hoàn thiện meeting summary flow", "Lan", "2026-02-07", "2026-02-13", "deadline 13/02"),
        task("Xây dựng parser transcript", "Tuấn", "2026-02-07", "2026-02-18", "deadline 18/02"),
        task("Hoàn thiện SP365 display cơ bản", "Hoa", "2026-02-07", "", "sau khi parser xong"),
    ],
    "W3-MED-C4-N2-PROD-INT-007": [
        task("Fix API connection", "Lan", "2026-02-11", "2026-02-11", "trước 16h hôm nay"),
        task("Xử lý parser transcript", "Hoa", "2026-02-11", "2026-02-18", "deadline mới"),
        task("Hoàn thiện spec SP365", "Lan", "2026-02-11", "2026-02-14", "deadline 14/02"),
        task("Cập nhật UI flow summary", "Lan", "2026-02-11", "2026-02-11", "deadline hôm nay"),
        task("Phân tích lỗi timestamp", "Hoa; Tuấn", "2026-02-11", "2026-02-12", "trong ngày mai"),
    ],
    "W3-MED-C4-N2-SW-INT-009": [
        task("Fix môi trường test", "Huy", "2026-02-13", "2026-02-13", "18h hôm nay"),
        task("Fix lỗi đăng nhập", "Huy", "2026-02-13", "2026-02-20", "deadline 20/2"),
        task("Viết hướng dẫn triển khai", "Huy", "2026-02-13", "2026-02-17", "deadline 17/2"),
        task("Cập nhật tài liệu troubleshooting", "Hà", "2026-02-13", "2026-02-17", "deadline 17/2"),
        task("Viết test case interaction", "Huy", "2026-02-13", "2026-02-17", "deadline 17/2"),
    ],
    "W3-MED-C4-N2-SW-INT-017": [
        task("Fix môi trường test", "Anh", "2026-02-21", "2026-02-28", "deadline 28/02"),
        task("Fix lỗi đăng nhập", "Minh", "2026-02-21", "2026-02-25", "deadline 25/02"),
        task("Viết tài liệu nội bộ", "Anh", "2026-02-21", "2026-02-26", "deadline 26/02"),
        task("Viết tài liệu hướng dẫn triển khai cho khách hàng", "Minh", "2026-02-21", "2026-02-26", "deadline 26/02"),
    ],
    "W4-LONG-C4-N1-IT-STATE-006": [
        task("Thiết lập môi trường staging", "Bình", "2026-02-20", "2026-03-05", "deadline 05/03"),
        task("Soạn tài liệu hướng dẫn triển khai", "Cường", "2026-02-20", "2026-03-10", "deadline 10/03"),
        task("Xử lý lỗi LDAP", "Bình", "2026-02-20", "2026-02-27", "deadline 27/02"),
        task("Soạn hướng dẫn cấu hình LDAP", "Dung", "2026-02-20", "2026-02-24", "deadline 24/02"),
        task("Ẩn danh dữ liệu test", "Dung", "2026-02-20", "2026-02-28", "deadline 28/02"),
        task("Cấu hình monitoring", "Bình", "2026-02-20", "", "trước release dự kiến cuối tháng 3"),
    ],
    "W4-LONG-C4-N1-OPS-STATE-012": [
        task("Chuẩn bị môi trường test cấu hình", "Minh", "2026-02-26", "2026-03-02", "deadline 02/03"),
        task("Thiết lập môi trường test hướng dẫn", "Huy", "2026-02-26", "2026-03-05", "deadline 05/03"),
        task("Review checklist triển khai với anh An", "Huy", "2026-02-26"),
        task("Viết và hoàn thiện tài liệu triển khai", "Trang", "2026-02-26", "2026-03-08", "deadline 08/03"),
        task("Xử lý lỗi đăng nhập", "Minh", "2026-02-26", "2026-03-03", "deadline 03/03"),
        task("Chuẩn bị dữ liệu test cho login", "Huy", "2026-02-26", "2026-03-04", "deadline 04/03"),
    ],
    "W4-LONG-C4-N2-IT-STATE-010": [
        task("Chuẩn bị môi trường test", "Tuấn", "2026-02-24", "2026-02-27", "deadline thứ Sáu"),
        task("Viết tài liệu môi trường test", "Hà", "2026-02-24", "2026-02-26", "deadline mới là thứ Năm"),
        task("Fix lỗi đăng nhập", "Khoa", "2026-02-24", "2026-02-27", "deadline thứ Sáu"),
        task("Viết test case đăng nhập", "Lan", "2026-02-24", "2026-02-26", "deadline thứ Năm"),
        task("Viết tài liệu triển khai tổng thể", "Hà", "2026-02-24", "2026-03-06", "thứ Sáu tuần sau"),
        task("Cấu hình monitoring", "Tuấn", "2026-02-24", "2026-03-06", "thứ Sáu tuần sau"),
    ],
    "W4-LONG-C4-N3-OPS-STATE-008": [
        task("Hoàn thiện logic khóa đăng nhập", "Châu", "2026-02-22", "2026-02-22", "19h hôm nay"),
        task("Viết tài liệu production", "Dũng", "2026-02-22", "2026-03-01", "deadline 01/03"),
        task("Setup monitoring", "Dũng", "2026-02-22", "2026-02-28", "deadline 28/02"),
        task("Lập release plan", "Bình; Dũng", "2026-02-22", "2026-02-26", "end of day thứ 5 tuần sau"),
        task("Viết release note", "Em", "2026-02-22"),
    ],
    "W4-LONG-C5-N1-SW-STATE-009": [
        task("Hoàn thiện môi trường test", "Hà; Tuấn", "2026-02-23", "2026-03-05", "deadline 05/03"),
        task("Viết tài liệu triển khai chi tiết", "Tuấn; Hà", "2026-02-23", "2026-03-14", "deadline 14/03"),
        task("Cấu hình monitoring cho staging", "Tuấn", "2026-02-23", "2026-03-03", "deadline 03/03"),
        task("Hoàn thiện release plan chi tiết", "Hà", "2026-02-23", "2026-03-21", "deadline cho release plan chi tiết là 21/03"),
    ],
    "W4-LONG-C5-N2-PROD-STATE-007": [
        task("Triển khai môi trường test module payment", "Minh", "2026-02-21", "2026-02-23", "deadline đã điều chỉnh thành 23/02"),
        task("Tạo template tài liệu triển khai", "Duy", "2026-02-21", "2026-02-26", "deadline 26/02"),
        task("Xử lý lỗi đăng nhập staging", "Hà", "2026-02-21", "2026-02-25", "deadline đã đặt lại thành 25/02"),
        task("Seed dữ liệu test payment", "Minh", "2026-02-21", "", "sau khi môi trường sẵn sàng"),
        task("Thiết lập monitoring dashboard cho payment", "Minh", "2026-02-21", "2026-02-27", "deadline 27/02"),
    ],
    "W4-LONG-C5-N2-SW-STATE-001": [
        task("Cập nhật tài liệu triển khai", "Minh", "2026-02-15", "2026-02-26", "deadline 26/02"),
        task("Cập nhật kịch bản kiểm thử triển khai", "Hoa", "2026-02-15", "2026-02-20", "deadline 20/02"),
        task("Tạo dữ liệu test", "Tuấn", "2026-02-15", "2026-02-19", "deadline 19/02"),
        task("Lập kế hoạch monitoring", "Hùng", "2026-02-15"),
    ],
    "W4-LONG-C5-N3-SW-STATE-005": [
        task("Hoàn thiện môi trường test staging", "Lan", "2026-02-19", "2026-02-26", "deadline 26/2"),
        task("Hoàn thiện tài liệu hướng dẫn triển khai staging", "Hà", "2026-02-19", "2026-03-05", "deadline 5/3"),
        task("Xử lý lỗi lock tài khoản staging và cấu hình monitoring", "Tuấn", "2026-02-19", "2026-02-22", "monitoring deadline 22/2"),
        task("Viết rollback plan cho release", "Lan", "2026-02-19", "2026-02-22", "deadline 22/2"),
    ],
    "W5-XL-C5-N2-IT-STRESS-002": [
        task("Phân tích log thanh toán", "Lan", "2026-02-26", "", "deadline 29/02"),
        task("Xuất log và metric thanh toán", "Tuấn", "2026-02-26", "2026-02-27", "sáng 27/02"),
        task("Deploy hotfix giá và cache tuning", "Tuấn; Lan", "2026-02-26", "2026-02-26", "hôm nay 15h"),
        task("Review design product placement", "Lan", "2026-02-26", "2026-03-04", "deadline 04/03"),
        task("Code review payment", "Đức", "2026-02-26", "2026-03-04", "deadline 04/03"),
        task("Review API FastShip", "Hùng; Đức", "2026-02-26", "2026-03-07", "deadline 07/03"),
        task("Tối ưu image mobile", "Hùng", "2026-02-26", "2026-03-04", "deadline 04/03"),
        task("Đánh giá capacity payment", "Đức", "2026-02-26", "2026-03-06", "deadline 06/03"),
    ],
    "W5-XL-C5-N2-OPS-STRESS-004": [
        task("Thiết lập CI/CD pipeline cho payment", "An", "2026-02-28", "2026-03-13", "deadline dời lên 13/3"),
        task("Cấu hình alerting", "An", "2026-02-28", "2026-03-12", "12/3"),
        task("Định nghĩa metric và threshold", "Tuấn", "2026-02-28", "2026-03-10", "10/3"),
        task("Cập nhật log spec error codes", "Tuấn", "2026-02-28", "2026-03-13", "13/3"),
        task("Viết E2E test scenario", "Tuấn", "2026-02-28", "2026-03-09", "9/3"),
        task("Thiết lập support process", "Lan", "2026-02-28", "2026-03-13", "13/3"),
        task("Viết API draft cho CRM", "Tuấn", "2026-02-28", "2026-03-13", "13/3"),
        task("Viết migration script", "An", "2026-02-28", "2026-03-18", "18/3"),
        task("Deploy payment", "", "2026-02-28", "2026-03-20", "20/3"),
        task("Liên hệ product về webhook spec", "Minh", "2026-02-28", "2026-03-15", "trước 15/3"),
    ],
    "W5-XL-C5-N3-PROD-STRESS-003": [
        task("Hoàn thành UI prototype", "Linh", "2026-02-27", "2026-03-04", "deadline 04/03"),
        task("Hoàn thành integration test script", "Hùng", "2026-02-27", "2026-03-04", "deadline 04/03"),
        task("Xử lý container memory", "Linh", "2026-02-27", "2026-03-03", "deadline 03/03"),
    ],
    "W5-XL-C5-N3-SW-STRESS-001": [
        task("Hoàn thành spec integration", "Thắng", "2026-02-25", "2026-02-26", "deadline thứ Năm tuần này"),
        task("Gửi multi-currency spec", "Dũng", "2026-02-25", "2026-02-26", "deadline thứ Năm"),
        task("Hoàn thành test case integration", "Trang", "2026-02-25", "2026-03-03", "deadline thứ Ba tuần sau"),
        task("Phân tích performance", "Dũng", "2026-02-25", "2026-03-02", "deadline thứ Hai tuần sau"),
        task("Khảo sát crypto", "Dũng", "2026-02-25", "2026-03-02", "2h chiều thứ Hai"),
        task("Chuẩn bị test data multi-currency", "An", "2026-02-25", "2026-03-02", "deadline thứ Hai tuần sau"),
        task("Tạo mock server cho third-party API", "Hòa", "2026-02-25", "2026-03-02", "deadline thứ Hai tuần sau"),
        task("Monitoring integration", "Hòa", "2026-02-25", "", "trước release"),
        task("Setup canary", "Hòa", "2026-02-25", "2026-03-15", "deadline 15/3"),
        task("Hoàn thành wireframe admin UI", "Huyền", "2026-02-25", "2026-03-05", "deadline thứ Năm tuần sau"),
    ],
}


FIELD_UPDATES: dict[str, dict[int, dict[str, str]]] = {
    "W1-SHORT-C1-N0-OPS-FPC-NDL-NEG-004": {
        0: {"due_date_text": "Chiều thứ Sáu"},
        1: {"due_date_text": "Chiều thứ Sáu"},
        2: {"due_date_text": "Chiều thứ Sáu"},
    },
    "W1-SHORT-C2-N1-PROD-NEG-QUNC-007": {0: {"due_date": "2026-01-22"}},
    "W1-SHORT-C2-N1-SW-DASG-WNEXT-NEG-013": {0: {"due_date": "2026-02-03"}},
    "W1-SHORT-C2-N1-SW-FPC-WDAY-NEG-009": {
        2: {"due_date": "2026-01-28", "due_date_text": "xong trước thứ Năm"}
    },
    "W2-SHORT-C2-N1-IT-NOOWN-022": {1: {"due_date": "2026-02-19"}},
    "W2-SHORT-C3-N2-PROD-HOFF-011": {1: {"due_date": "2026-02-11"}},
    "W2-SHORT-C3-N2-PROD-NDL-023": {1: {"due_date": "2026-02-19"}},
    "W2-SHORT-C3-N2-SW-RECF-017": {0: {"due_date": "2026-02-12"}},
    "W3-MED-C3-N1-IT-INT-010": {5: {"due_date": "2026-02-23"}},
    "W3-MED-C3-N1-IT-INT-006": {
        3: {"due_date": "2026-02-18", "due_date_text": "deadline 18/02"}
    },
    "W3-MED-C3-N1-OPS-INT-008": {
        2: {"due_date": "2026-02-18"},
        3: {"due_date": "2026-02-12"},
    },
    "W3-MED-C4-N2-PROD-INT-011": {0: {"due_date": "2026-02-19"}},
    "W4-LONG-C4-N2-OPS-STATE-004": {
        2: {"due_date": "2026-02-20", "due_date_text": "Deadline 20/02"}
    },
    "W4-LONG-C4-N2-IT-STATE-010": {
        1: {"due_date_text": "deadline mới cho tài liệu môi trường test là thứ Năm"}
    },
    "W5-XL-C5-N2-OPS-STRESS-004": {9: {"due_date": "2026-03-14"}},
}


def main() -> None:
    changed: list[str] = []
    for case_id, tasks in REPLACEMENTS.items():
        path = DATASET / case_id / "expected_output.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["tasks"] = tasks
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed.append(case_id)

    for case_id, updates in FIELD_UPDATES.items():
        if not updates:
            continue
        path = DATASET / case_id / "expected_output.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for index, fields in updates.items():
            payload["tasks"][index].update(fields)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed.append(case_id)

    print(f"Updated {len(set(changed))} reviewed expected-output files.")


if __name__ == "__main__":
    main()
