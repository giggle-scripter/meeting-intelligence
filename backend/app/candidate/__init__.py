"""Candidate scoring and context windows."""

from .ai_batcher import AiWindowBatch, batch_ai_windows
from .action_candidates import ActionCandidate, CandidateState, GroundedSpan, build_action_candidates, build_action_proposals_v3, summarize_action_candidates
from .action_canonicalization import ActionFrame, build_action_frame
from .deadline_grounding import DeadlineAttachmentEvidence, DeadlineAttachmentType, build_deadline_attachment_evidence
from .owner_grounding import OwnerEvidence, OwnerEvidenceType, build_owner_evidence
from .commitment_router import AuthorityKind, CommitmentDecision, CommitmentRoute, route_commitments, summarize_commitment_decisions
from .evidence import CandidateEvidence, build_candidate_evidence
from .router import (
    CandidateDecision,
    CandidateRoute,
    CandidateRouter,
    CandidateRouterConfig,
    CandidateRouterShadowSummary,
    summarize_candidate_decisions,
)
from .proposal import TaskCreateProposal, TaskCreateProposalResponse
from .evidence_seeds import EvidenceSeed, SeedRole, build_evidence_seeds
from .proposal_relations import ProposalRelation, RelationScope, RelationType, build_proposal_relations
from .proposal_clusters import ProposalCluster, ProposalKind, build_proposal_clusters
from .scorer import choose_extraction_strategy, is_candidate
from .window_builder import build_candidate_windows
from .window_merger import merge_windows

__all__ = [
    "ActionCandidate", "ActionFrame", "AiWindowBatch", "AuthorityKind", "CandidateDecision", "CandidateEvidence", "CandidateRoute",
    "CandidateState", "CommitmentDecision", "CommitmentRoute", "DeadlineAttachmentEvidence", "DeadlineAttachmentType", "GroundedSpan", "OwnerEvidence", "OwnerEvidenceType",
    "CandidateRouter", "CandidateRouterConfig", "CandidateRouterShadowSummary",
    "TaskCreateProposal", "TaskCreateProposalResponse",
    "EvidenceSeed", "SeedRole", "build_evidence_seeds",
    "ProposalRelation", "RelationScope", "RelationType", "build_proposal_relations",
    "ProposalCluster", "ProposalKind", "build_proposal_clusters",
    "batch_ai_windows",
    "build_action_candidates", "build_action_proposals_v3", "build_action_frame", "build_candidate_evidence", "build_candidate_windows", "build_deadline_attachment_evidence", "build_owner_evidence",
    "choose_extraction_strategy", "is_candidate", "merge_windows", "route_commitments",
    "summarize_action_candidates", "summarize_candidate_decisions", "summarize_commitment_decisions",
]
