"""Build short candidate-centered clause windows."""

from ..models import CandidateWindow, Clause, ClauseAnnotation
from ..utils.ids import make_id
from .scorer import choose_extraction_strategy, is_candidate


def build_candidate_windows(clauses: list[Clause], annotations: dict[str, ClauseAnnotation], before: int = 3, after: int = 5) -> list[CandidateWindow]:
    windows: list[CandidateWindow] = []
    for index, clause in enumerate(clauses):
        annotation = annotations[clause.clause_id]
        if not is_candidate(annotation):
            continue
        start = max(0, index - before)
        end = min(len(clauses), index + after + 1)
        windows.append(CandidateWindow(make_id("WIN", len(windows) + 1), [clause.clause_id], [item.clause_id for item in clauses[start:end]], annotation.score, choose_extraction_strategy(annotation)))
    return windows
