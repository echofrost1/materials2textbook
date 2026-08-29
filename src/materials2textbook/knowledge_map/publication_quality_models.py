"""Read-only data contracts for publication quality assurance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PublicationSeverity:
    BLOCKER = "BLOCKER"
    HIGH = "HIGH"
    WARNING = "WARNING"


class PublicationQualityStatus:
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class PublicationContentFragment:
    target: str  # markdown | digital_book
    location: str
    component: str
    text: str
    occurrence_id: str = ""
    section_id: str = ""
    task_id: str = ""


@dataclass(frozen=True)
class PublicationQualityIssue:
    issue_id: str
    code: str
    severity: str
    location: str
    message: str
    source_span: str
    affected_outputs: tuple[str, ...]
    component: str = ""
    occurrence_id: str = ""
    classification: str = ""  # upstream_source_bug | renderer_bug | writer_quality_bug
    rationale: str = ""
    supporting_evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RenderedClaimVerification:
    occurrence_id: str
    target: str
    claim_text: str
    claim_type: str
    source_span: str
    supporting_evidence_ids: tuple[str, ...]
    support_status: str  # SUPPORTED | UNSUPPORTED | UNCERTAIN
    confidence: float


@dataclass(frozen=True)
class PedagogicalSufficiencyRecord:
    occurrence_id: str
    role: str
    body_character_count: int
    sentence_count: int
    teach_explanation_coverage: bool
    procedure_step_count: int
    example_present: bool
    exercise_support_coverage: bool
    role_specific_density: float
    status: str  # ADEQUATE | CONTENT_TOO_THIN


@dataclass(frozen=True)
class PublicationProvenanceRecord:
    occurrence_id: str
    plan_status: str  # UNCHANGED_PLAN | CONTRACTED_PLAN | DROPPED_GOAL
    render_status: str  # VERIFIED_AS_GENERATED | VERIFIED_REPAIRED | ROLLED_BACK | NOT_RENDERED


@dataclass(frozen=True)
class RepairHistoryEntry:
    occurrence_id: str
    repair_type: str
    reason: str
    before: str
    candidate: str
    diff: str
    post_check_result: Any
    final_disposition: str  # ACCEPTED | ROLLED_BACK | SKIPPED | REJECTED


@dataclass
class PublicationQualityReport:
    issues: list[PublicationQualityIssue] = field(default_factory=list)
    rendered_claims: list[RenderedClaimVerification] = field(default_factory=list)
    pedagogical_sufficiency: list[PedagogicalSufficiencyRecord] = field(default_factory=list)
    provenance: list[PublicationProvenanceRecord] = field(default_factory=list)
    repair_history: list[RepairHistoryEntry] = field(default_factory=list)
    rendered_claim_semantic_audit: dict[str, Any] = field(default_factory=dict)
    semantic_closed_loop_status: str = PublicationQualityStatus.PASS
    publication_quality_status: str = PublicationQualityStatus.PASS
    final_publication_status: str = PublicationQualityStatus.PASS

    @property
    def blockers(self) -> list[PublicationQualityIssue]:
        return [item for item in self.issues if item.severity == PublicationSeverity.BLOCKER]

    @property
    def warnings(self) -> list[PublicationQualityIssue]:
        return [item for item in self.issues if item.severity == PublicationSeverity.WARNING]

    @property
    def high_severity(self) -> list[PublicationQualityIssue]:
        return [item for item in self.issues if item.severity == PublicationSeverity.HIGH]

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_closed_loop_status": self.semantic_closed_loop_status,
            "publication_quality_status": self.publication_quality_status,
            "final_publication_status": self.final_publication_status,
            "issue_counts": {
                "blocker": len(self.blockers),
                "high": len(self.high_severity),
                "warning": len(self.warnings),
            },
            "issues": [item.to_dict() for item in self.issues],
            "rendered_claims": [asdict(item) for item in self.rendered_claims],
            "pedagogical_sufficiency": [asdict(item) for item in self.pedagogical_sufficiency],
            "provenance": [asdict(item) for item in self.provenance],
            "repair_history": [asdict(item) for item in self.repair_history],
            "rendered_claim_semantic_audit": self.rendered_claim_semantic_audit,
        }
