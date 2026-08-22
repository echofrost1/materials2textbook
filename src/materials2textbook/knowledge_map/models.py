from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class KnowledgeKind:
    CONCEPT = "CONCEPT"
    PRINCIPLE = "PRINCIPLE"
    METHOD = "METHOD"
    SKILL = "SKILL"
    PROCEDURE = "PROCEDURE"


class LearningRole:
    INTRO = "INTRO"
    TEACH = "TEACH"
    RECALL = "RECALL"
    APPLY = "APPLY"
    EXTEND = "EXTEND"


class MasteryFacet:
    """Instructional facets, not claims about an individual learner."""

    ORIENTED = "ORIENTED"
    EXPLAIN = "EXPLAIN"
    PERFORM = "PERFORM"
    ANALYZE = "ANALYZE"


@dataclass(frozen=True, order=True)
class BookPosition:
    chapter_ordinal: int
    task_ordinal: int
    occurrence_ordinal: int
    section_ordinal: int = 0
    source_point_ordinal: int = 0


@dataclass
class SourceKnowledgePoint:
    source_knowledge_point_id: str
    title: str
    chapter_id: str
    section_id: str
    chapter_ordinal: int
    section_ordinal: int
    task_ordinal: int
    source_point_ordinal: int
    context_title: str
    source_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class KnowledgePoint:
    knowledge_id: str
    title: str
    aliases: list[str]
    kind: str
    scope: str = ""
    canonical_summary: str = ""
    source_chunk_ids: list[str] = field(default_factory=list)
    extraction_confidence: float = 0.0


@dataclass
class KnowledgeMapping:
    source_knowledge_point_id: str
    canonical_knowledge_ids: list[str]
    mapping_type: str  # EXACT | ALIAS | DECOMPOSED | UNCERTAIN
    confidence: float
    rationale: str
    evidence_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class Prerequisite:
    edge_id: str
    source_knowledge_id: str
    target_knowledge_id: str
    required_facets: list[str]
    required_extension_keys: list[str] = field(default_factory=list)
    relation: str = "HARD"  # HARD | SUPPORTING
    use_type: str = "DIRECT"  # DIRECT | BACKGROUND
    rationale: str = ""
    confidence: float = 0.0
    evidence_chunk_ids: list[str] = field(default_factory=list)
    provenance: str = ""
    supporting_basis: str = ""
    trusted_for_runtime: bool = False


@dataclass
class PrerequisiteUse:
    knowledge_id: str
    required_facets: list[str]
    required_extension_keys: list[str] = field(default_factory=list)
    relation: str = "HARD"
    use_type: str = "DIRECT"
    edge_id: str = ""
    rationale: str = ""
    evidence_chunk_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    provenance: str = ""
    supporting_basis: str = ""
    trusted_for_runtime: bool = False


@dataclass
class SemanticDelta:
    """LLM-proposed semantic facts for one occurrence; it intentionally has no role."""

    occurrence_id: str
    repeats_prior_explanation: bool
    uses_prior_knowledge: bool
    recall_needed: bool
    required_self_facets: list[str]
    required_self_extension_keys: list[str]
    cross_prerequisite_uses: list[PrerequisiteUse]
    new_facets: list[str]
    new_extension_keys: list[str]
    new_context: str
    repeated_aspects: list[str]
    contribution_summary: str
    confidence: float
    rationale: str
    evidence_chunk_ids: list[str]
    orientation_only: bool = False
    restores_prior_context: bool = False
    repeats_complete_teaching: bool = False


@dataclass
class PlannedOccurrence:
    occurrence_id: str
    knowledge_id: str
    source_knowledge_point_id: str
    position: BookPosition
    chapter_id: str
    section_id: str
    context_title: str
    source_chunk_ids: list[str]
    role: str
    required_self_facets: list[str] = field(default_factory=list)
    required_self_extension_keys: list[str] = field(default_factory=list)
    required_prerequisites: list[PrerequisiteUse] = field(default_factory=list)
    intended_grants: list[str] = field(default_factory=list)
    intended_extension_keys: list[str] = field(default_factory=list)
    repeats_prior_explanation: bool = False
    uses_prior_knowledge: bool = False
    recall_needed: bool = False
    intended_contribution: str = ""
    new_context: str = ""
    repeated_aspects: list[str] = field(default_factory=list)
    contribution_confidence: float = 0.0
    contribution_rationale: str = ""
    contribution_evidence_chunk_ids: list[str] = field(default_factory=list)
    mapping_confidence: float = 0.0
    planning_confidence: float = 0.0
    planning_rationale: str = ""
    planning_evidence_chunk_ids: list[str] = field(default_factory=list)
    trusted_for_state: bool = False


@dataclass
class KnowledgeAvailabilityRecord:
    """What verified student-visible teaching has made available so far.

    This record deliberately says nothing about an individual learner's actual
    mastery.  In the runtime path it is populated only after the corresponding
    occurrence has rendered content and passed its local conformance and
    evidence checks.  The source maps make every available item traceable to
    the occurrence which established it.
    """

    available_facets: list[str] = field(default_factory=list)
    available_extension_keys: list[str] = field(default_factory=list)
    first_available_position: BookPosition | None = None
    last_taught_task_ordinal: int | None = None
    last_activated_task_ordinal: int | None = None
    facet_source_occurrence_ids: dict[str, str] = field(default_factory=dict)
    extension_source_occurrence_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class InstructionalAvailabilityState:
    position: BookPosition | None = None
    availability_by_knowledge: dict[str, KnowledgeAvailabilityRecord] = field(default_factory=dict)


@dataclass
class AvailabilitySnapshot:
    occurrence_id: str
    position: BookPosition
    before: InstructionalAvailabilityState
    after: InstructionalAvailabilityState
    self_requirements_available: bool
    cross_requirements_available: bool
    transition_applied: bool
    blocked_reasons: list[str] = field(default_factory=list)
    availability_kind: str = "PLANNED"  # PLANNED | VERIFIED_RUNTIME


@dataclass(frozen=True)
class OccurrenceExecutionResult:
    """Local execution outcome required before a planned grant becomes usable.

    ``rendered_span_id`` and a non-empty ``rendered_body`` are both required
    for a grant: an anchor, successful plan or accepted brief alone never
    establishes instructional availability.  Facet and extension grants are
    also explicit verifier outputs; runtime code must never infer that every
    intended grant was actually rendered merely from an occurrence-level PASS.
    """

    occurrence_id: str
    rendered_span_id: str | None
    rendered_body: str
    conformance_status: str
    evidence_status: str
    conformance_verified_facets: tuple[str, ...] = ()
    conformance_verified_extension_keys: tuple[str, ...] = ()
    evidence_supported_facets: tuple[str, ...] = ()
    evidence_supported_extension_keys: tuple[str, ...] = ()
    generation_provenance: str = "unknown"
    semantic_claim_ids: tuple[str, ...] = ()
    semantic_claim_statuses: tuple[str, ...] = ()
    semantic_audit_records: tuple[dict[str, Any], ...] = ()


@dataclass
class VerifiedAvailabilityTransition:
    """Auditable runtime state transition for one rendered occurrence."""

    occurrence_id: str
    position: BookPosition
    before: InstructionalAvailabilityState
    after: InstructionalAvailabilityState
    execution: OccurrenceExecutionResult
    self_requirements_available: bool
    cross_requirements_available: bool
    grant_applied: bool
    granted_facets: tuple[str, ...] = ()
    granted_extension_keys: tuple[str, ...] = ()
    blocked_reasons: list[str] = field(default_factory=list)


@dataclass
class RuntimeOccurrenceCompilation:
    """Deterministic pre-write compilation against verified runtime state."""

    occurrence_id: str
    before: InstructionalAvailabilityState
    compiled_occurrence: PlannedOccurrence | None
    effective_delta: SemanticDelta | None
    self_requirements_available: bool
    cross_requirements_available: bool
    executable: bool
    issue_code: str = ""
    issue_details: str = ""
    audit: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RelationJudgement:
    relation: str  # EQUIVALENT | APPLICATION | EXTENSION | DISTINCT | UNCERTAIN
    confidence: float
    rationale: str
    evidence_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    issue_id: str
    type: str
    severity: str
    knowledge_id: str
    occurrence_id: str
    position: BookPosition
    diagnosis: str
    deterministic_evidence: dict[str, Any] = field(default_factory=dict)
    semantic_judgement: str = ""
    suggested_future_repair: str = "MANUAL_REVIEW"


@dataclass
class LearningTrajectory:
    knowledge_id: str
    occurrence_ids: list[str]
    planned_conflict_ids: list[str] = field(default_factory=list)


@dataclass
class KnowledgeMap:
    title: str
    outline_signature: str
    knowledge_points: list[KnowledgePoint]
    source_knowledge_points: list[SourceKnowledgePoint]
    mappings: list[KnowledgeMapping]
    prerequisites: list[Prerequisite]
    planned_occurrences: list[PlannedOccurrence]
    trajectories: list[LearningTrajectory]
    availability_snapshots: list[AvailabilitySnapshot]
    validation_issues: list[ValidationIssue]
    analysis_version: str = "knowledge-map.phase-1.v1"
