import importlib.util
import json
from pathlib import Path


def _queue_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "build_blind_task_evidence_review_queue.py"
    spec = importlib.util.spec_from_file_location("blind_task_evidence_queue", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blind_queue_uses_only_reviewed_task_and_transcript_context(tmp_path: Path) -> None:
    case = tmp_path / "BLIND-001"
    case.mkdir()
    (case / "metadata.json").write_text(json.dumps({"case_id": "BLIND-001"}), encoding="utf-8")
    (case / "expected_output.json").write_text(json.dumps({"tasks": [{"task_name": "Gửi báo cáo"}]}), encoding="utf-8")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "BLIND-001-v1-trace.json").write_text(json.dumps({
        "clauses": [{"clause_id": "C1", "text_raw": "Lan sẽ gửi báo cáo hôm nay."}],
        "date_mentions": {},
    }), encoding="utf-8")

    rows, errors = _queue_module().build_queue(tmp_path, traces)

    assert errors == []
    assert rows[0]["review_status"] == "SUGGESTED"
    assert rows[0]["suggested_clause_ids"] == ["C1"]
    assert rows[0]["action_evidence"] is None
