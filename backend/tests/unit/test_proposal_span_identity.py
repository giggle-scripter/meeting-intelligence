from backend.app.candidate import (
    EvidenceSeed,
    GroundedSpan,
    ProposalCluster,
    ProposalKind,
    SeedRole,
    build_action_span_lattice,
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
    clause = Clause("C-1", "S-1", "P-1", "Lan", None, None, "Hệ thống monitoring đang lỗi.", "", [], 1)
    cluster = ProposalCluster(cluster_id="CLUSTER-C-1", nucleus_seed_id="SEED-C-1", nucleus_clause_id="C-1", proposal_kind=ProposalKind.CREATE)
    seed = EvidenceSeed(seed_id="SEED-C-1", clause_id="C-1", turn_id="S-1", speaker_name="Lan", order_index=1, roles=(SeedRole.ACTION,))
    record = build_proposal_span_identities([cluster], [seed], [clause], {"C-1": ClauseAnnotation("C-1", {"ACTION_VERB"})})[0]

    assert record.state == "REFERENCE"
    assert record.action_span is None


def test_span_identity_keeps_multiple_independent_actions_in_one_clause() -> None:
    clause = Clause("C-1", "S-1", "P-1", "Lan", None, None, "Lan gửi bản nháp vào 15/3 và hoàn thành final vào 20/3.", "", [], 1)
    cluster = ProposalCluster(cluster_id="CLUSTER-C-1", nucleus_seed_id="SEED-C-1", nucleus_clause_id="C-1", proposal_kind=ProposalKind.CREATE)
    seed = EvidenceSeed(seed_id="SEED-C-1", clause_id="C-1", turn_id="S-1", speaker_name="Lan", order_index=1, roles=(SeedRole.ACTION,))
    records = build_proposal_span_identities([cluster], [seed], [clause], {"C-1": ClauseAnnotation("C-1", {"ACTION_VERB"})})

    assert {item.action_span.text for item in records if item.action_span} >= {"gửi bản nháp", "hoàn thành final"}


def test_span_lattice_includes_bounded_prefixes_from_an_exact_source_span() -> None:
    lattice = build_action_span_lattice((
        GroundedSpan(clause_id="C1", start=4, end=36, text="gửi bản nháp hướng dẫn sử dụng"),
    ))
    assert any(item.text == "gửi bản nháp" and variant == "TOKEN_PREFIX" for item, variant in lattice)
