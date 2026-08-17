"""Evidence is assembled only from source clauses."""

from ..models import Clause


def build_evidence(source_clause_ids: list[str], clauses_by_id: dict[str, Clause]) -> str:
    lines: list[str] = []
    for clause_id in source_clause_ids:
        clause = clauses_by_id.get(clause_id)
        if clause is None:
            continue
        line = f"{clause.speaker_name}: {clause.text_raw}"
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)
