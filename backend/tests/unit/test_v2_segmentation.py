from backend.app.models import Clause
from backend.app.v2.segmentation import create_segments


def test_each_clause_is_primary_once_and_overlap_is_context_only() -> None:
    clauses = [
        Clause(f"C-{index}", "S", "speaker", "Lan", None, None, "text", "text", order_index=index)
        for index in range(9)
    ]
    segments = create_segments(clauses, target_size=4, overlap=2)

    assert [item for segment in segments for item in segment.primary_clause_ids] == [f"C-{index}" for index in range(9)]
    assert segments[1].context_clause_ids[:2] == ("C-2", "C-3")
