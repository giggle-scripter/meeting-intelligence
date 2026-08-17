from backend.app.models import Clause
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.v2.context import compact_context_clause_ids
from backend.app.v2.models import ClauseRelevance, RelevanceClass


def _clause(index: int) -> Clause:
    text = f"Clause {index}"
    return Clause(
        f"C-{index}", "S-1", "P-1", "Lan", None, None,
        text, normalize_for_match(text), order_index=index,
    )


def test_compaction_keeps_focus_and_mandatory_and_drops_noise_first() -> None:
    clauses = [_clause(index) for index in range(10)]
    by_id = {item.clause_id: item for item in clauses}
    relevance = {
        item.clause_id: ClauseRelevance(
            item.clause_id,
            RelevanceClass.MANDATORY if item.clause_id == "C-8" else (
                RelevanceClass.NOISE if item.clause_id in {"C-0", "C-1", "C-9"}
                else RelevanceClass.CONTEXT
            ),
            0,
            (),
        )
        for item in clauses
    }

    selected = compact_context_clause_ids(
        list(by_id), ["C-5"], by_id, relevance, max_clauses=5, neighbor_radius=1
    )

    assert "C-5" in selected
    assert "C-8" in selected
    assert not {"C-0", "C-1", "C-9"}.intersection(selected)
    assert list(selected) == sorted(selected, key=lambda value: int(value.split("-")[1]))
