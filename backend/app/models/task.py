"""Reduced task state and public output models."""

from dataclasses import dataclass, field


@dataclass
class TaskState:
    task_id: str
    task_name: str
    assignee: str
    status: str = "PROPOSED"
    deadline_mention_id: str = ""
    deadline_event_ids: list[str] = field(default_factory=list)
    deadline_mention_history: list[str] = field(default_factory=list)
    source_clause_ids: list[str] = field(default_factory=list)
    last_event_time_ms: int | None = None
    last_order_index: int = 0
    confidence: float = 0.0
    extraction_sources: set[str] = field(default_factory=set)


@dataclass
class FinalTask:
    task_name: str
    assignee: str
    start_date: str
    due_date: str
    due_date_text: str
    evidence: str
    status: str = "Proposed"


@dataclass
class PipelineDiagnostics:
    caption_count: int = 0
    deduplicated_caption_count: int = 0
    turn_count: int = 0
    sentence_count: int = 0
    clause_count: int = 0
    candidate_window_count: int = 0
    rule_event_count: int = 0
    ai_event_count: int = 0
    ai_window_count: int = 0
    ai_provider_enabled: bool = False
    ai_provider_call_count: int = 0
    ai_context_clause_count: int = 0
    ai_fallback_error_count: int = 0
    unresolved_window_count: int = 0
    ai_call_rate: float = 0.0
    ai_clause_coverage: float = 0.0
    ai_batch_count: int = 0
    event_count_before_deduplication: int = 0
    event_count_after_deduplication: int = 0
    terminal_replay_blocked_count: int = 0
    ledger_task_created_count: int = 0
    ledger_task_updated_count: int = 0
    exact_id_link_count: int = 0
    exact_alias_link_count: int = 0
    semantic_link_count: int = 0
    unresolved_mutation_count: int = 0
    duplicate_task_merge_count: int = 0
    provisional_task_created_count: int = 0
    provisional_task_promoted_count: int = 0
    provisional_promotion_blocked_count: int = 0
    ambiguous_identity_mutation_blocked_count: int = 0
    sibling_identity_split_count: int = 0
    ai_contract_rejection_count: int = 0
    ai_structural_contract_rejection_count: int = 0
    ai_semantic_rejection_count: int = 0
    ai_unknown_task_id_rejection_count: int = 0
    ai_invalid_source_clause_rejection_count: int = 0
    ai_invalid_anchor_clause_rejection_count: int = 0
    ai_non_concrete_action_rejection_count: int = 0
    ai_invalid_assignee_rejection_count: int = 0
    unauthorized_creation_blocked_count: int = 0
    ledger_unknown_task_id_rejection_count: int = 0
    recap_reconciliation_mode: str = "off"
    recap_fragment_shadow_count: int = 0
    owner_grounding_mode: str = "off"
    owner_evidence_count: int = 0
    owner_ungrounded_event_count: int = 0
    owner_evidence_type_counts: dict[str, int] = field(default_factory=dict)
    deadline_grounding_mode: str = "off"
    deadline_attachment_count: int = 0
    deadline_unresolved_attachment_count: int = 0
    deadline_attachment_type_counts: dict[str, int] = field(default_factory=dict)
    recap_scope: str = "NONE"
    meeting_date_source: str = "REQUEST"
    effective_meeting_date: str = ""
    explicit_task_start_date_count: int = 0
    action_classifier_mode: str = "off"
    action_classifier_version: str = "disabled"
    embedding_model_version: str = "disabled"
    action_classifier_clause_count: int = 0
    action_classifier_prediction_counts: dict[str, int] = field(default_factory=dict)
    action_classifier_would_create_count: int = 0
    action_classifier_would_review_count: int = 0
    action_classifier_would_update_count: int = 0
    action_classifier_rule_action_clause_count: int = 0
    action_classifier_rule_agreement_count: int = 0
    action_classifier_rule_disagreement_count: int = 0
    action_classifier_error_count: int = 0
    action_candidate_builder_mode: str = "off"
    action_candidate_builder_version: str = "disabled"
    action_candidate_count: int = 0
    action_candidate_action_span_count: int = 0
    action_candidate_kind_counts: dict[str, int] = field(default_factory=dict)
    action_candidate_state_counts: dict[str, int] = field(default_factory=dict)
    action_candidate_builder_error_count: int = 0
    commitment_router_mode: str = "off"
    commitment_router_version: str = "disabled"
    commitment_router_decision_count: int = 0
    commitment_router_route_counts: dict[str, int] = field(default_factory=dict)
    commitment_router_authority_counts: dict[str, int] = field(default_factory=dict)
    commitment_router_suppressed_event_count: int = 0
    commitment_router_error_count: int = 0
    action_canonicalization_mode: str = "off"
    action_canonicalization_version: str = "disabled"
    action_canonicalization_frame_count: int = 0
    action_canonicalization_changed_count: int = 0
    action_canonicalization_rejected_count: int = 0
    action_canonicalization_error_count: int = 0
    candidate_router_mode: str = "off"
    candidate_router_version: str = "disabled"
    candidate_threshold_version: str = "disabled"
    candidate_evidence_count: int = 0
    candidate_decision_count: int = 0
    candidate_route_counts: dict[str, int] = field(default_factory=dict)
    candidate_ai_create_check_suppressed_count: int = 0
    candidate_router_error_count: int = 0
    task_create_proposal_call_count: int = 0
    task_create_proposal_accepted_count: int = 0
    task_create_proposal_no_action_count: int = 0
    task_create_proposal_unresolved_count: int = 0
    task_create_proposal_rejected_count: int = 0
    task_create_proposal_rejection_reasons: dict[str, int] = field(
        default_factory=dict
    )
    task_semantic_linker_mode: str = "off"
    task_semantic_linker_version: str = "disabled"
    task_semantic_index_version: str = "disabled"
    task_semantic_scoring_version: str = "disabled"
    task_semantic_embedding_model_version: str = "disabled"
    task_semantic_query_count: int = 0
    task_semantic_scored_query_count: int = 0
    task_semantic_route_counts: dict[str, int] = field(default_factory=dict)
    task_semantic_reason_counts: dict[str, int] = field(default_factory=dict)
    task_semantic_production_agreement_count: int = 0
    task_semantic_production_disagreement_count: int = 0
    task_semantic_ambiguous_sibling_count: int = 0
    task_semantic_mean_top1_score: float = 0.0
    task_semantic_mean_margin: float = 0.0
    task_semantic_linker_error_count: int = 0
    context_retrieval_mode: str = "off"
    context_retrieval_version: str = "disabled"
    context_topic_index_version: str = "disabled"
    context_embedding_model_version: str = "disabled"
    context_bundle_count: int = 0
    context_total_clause_count: int = 0
    context_total_character_count: int = 0
    context_total_task_count: int = 0
    context_total_history_event_count: int = 0
    context_total_note_cue_count: int = 0
    context_max_clause_count_observed: int = 0
    context_max_character_count_observed: int = 0
    context_clause_cap_hit_count: int = 0
    context_character_cap_hit_count: int = 0
    context_tier_clause_counts: dict[str, int] = field(default_factory=dict)
    context_retrieval_error_count: int = 0
    ai_mutation_router_mode: str = "off"
    ai_mutation_router_version: str = "disabled"
    ai_mutation_prompt_version: str = "disabled"
    ai_mutation_candidate_count: int = 0
    ai_mutation_payload_count: int = 0
    ai_mutation_call_count: int = 0
    ai_mutation_event_count: int = 0
    ai_mutation_unresolved_count: int = 0
    ai_mutation_rejected_count: int = 0
    ai_mutation_error_count: int = 0
    ai_mutation_rejection_reasons: dict[str, int] = field(default_factory=dict)
    ai_mutation_candidate_task_count: int = 0
    ai_mutation_context_clause_count: int = 0
    ai_mutation_context_character_count: int = 0
    ai_mutation_unknown_task_id_count: int = 0
    ai_mutation_invalid_source_count: int = 0
    ai_mutation_invalid_anchor_count: int = 0
    ai_mutation_invalid_owner_span_count: int = 0
    ai_mutation_invalid_deadline_count: int = 0
    note_dual_view_mode: str = "off"
    note_dual_view_version: str = "disabled"
    note_claim_count: int = 0
    note_full_grounded_count: int = 0
    note_partial_grounded_count: int = 0
    note_only_count: int = 0
    note_contradicted_count: int = 0
    note_claim_retrieval_clause_count: int = 0
    note_claim_mean_top1_score: float = 0.0
    note_claim_mean_margin: float = 0.0
    note_human_proposal_candidate_count: int = 0
    note_auto_overview_context_only_count: int = 0
    note_direct_event_suppressed_count: int = 0
    note_dual_view_error_count: int = 0
    note_grounding_reason_counts: dict[str, int] = field(default_factory=dict)
    temporal_semantics_mode: str = "off"
    temporal_parser_version: str = "disabled"
    temporal_working_day_policy: str = "disabled"
    temporal_expression_count: int = 0
    temporal_type_counts: dict[str, int] = field(default_factory=dict)
    temporal_existing_resolved_count: int = 0
    temporal_ast_resolved_count: int = 0
    temporal_ast_unresolved_count: int = 0
    temporal_unresolved_anchor_count: int = 0
    temporal_agreement_count: int = 0
    temporal_disagreement_count: int = 0
    temporal_improve_count: int = 0
    temporal_regress_count: int = 0
    temporal_parser_error_count: int = 0
    temporal_resolution_status_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class PipelineResult:
    meeting_title: str
    summary: str
    tasks: list[FinalTask]
    diagnostics: PipelineDiagnostics
    unresolved_window_ids: list[str] = field(default_factory=list)
