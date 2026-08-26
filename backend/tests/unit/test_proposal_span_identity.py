from backend.app.candidate import (
    EvidenceSeed,
    ProposalCluster,
    ProposalKind,
    SeedRole,
    build_proposal_span_identities,
)
from backend.app.models import Clause, ClauseAnnotation


def test_span_identity_extracts_exact_action_and_is_stable() -> None:
    clause = Clause("C-1", "S-1", "P-1", "Lan", None, None, "Lan, em sẽ viết báo cáo trước thứ Sáu.", "", [], 1)
    cluster = ProposalCluster(
        cluster_id="CLUSTER-C-1", nucleus_seed_id="SEED-C-1", nucleus_clause_id="C-1", proposal_kind=ProposalKind.CREATE,
    )
    seed = EvidenceSeed(seed_id="SEED-C-1", clause_id="C-1", turn_id="S-1", speaker_name="Lan", order_index=1, roles=(SeedRole.ACTION,))
    records = build_proposal_span_identities([cluster], [seed], [clause], {"C-1": ClauseAnnotation("C-1", {"ACTION_VERB"})})

    assert records[0].state == "PROPOSED"
    assert records[0].action_span is not None
    assert records[0].action_span.text == "viết báo cáo"
    assert records[0].identity_key.startswith("PID-")


def test_span_identity_downgrades_unextractable_create_to_reference() -> None:
    clause = Clause("C-1", "S-1", "P-1", "Lan", None, None, "Cấu hình monitoring.", "", [], 1)
    cluster = ProposalCluster(cluster_id="CLUSTER-C-1", nucleus_seed_id="SEED-C-1", nucleus_clause_id="C-1", proposal_kind=ProposalKind.CREATE)
    seed = EvidenceSeed(seed_id="SEED-C-1", clause_id="C-1", turn_id="S-1", speaker_name="Lan", order_index=1, roles=(SeedRole.ACTION,))
    record = build_proposal_span_identities([cluster], [seed], [clause], {"C-1": ClauseAnnotation("C-1", {"ACTION_VERB"})})[0]

    assert record.state == "REFERENCE"
    assert record.action_span is None
