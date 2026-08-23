from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import re
from typing import Any, Callable

from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.knowledge_map.outline import book_plan_fingerprint
from materials2textbook.schemas import BookChapterPlan, BookPlan, BookSectionPlan, EvidenceChunk


REPLANNABLE_PLAN_GAP = "REPLANNABLE_PLAN_GAP"
EVIDENCE_BINDING_GAP = "EVIDENCE_BINDING_GAP"
GENUINE_SOURCE_GAP = "GENUINE_SOURCE_GAP"
UNRESOLVED_SOURCE_COVERAGE = "UNRESOLVED_SOURCE_COVERAGE"
MANUAL_REVIEW = "MANUAL_REVIEW"
REPLAN_DUPLICATE_SECTION = "REPLAN_DUPLICATE_SECTION"
PARTIAL_COVERAGE_AUDIT = "PARTIAL_COVERAGE_AUDIT"
SECTION_EVIDENCE_SUFFICIENT = "SECTION_EVIDENCE_SUFFICIENT"
SECTION_EVIDENCE_PARTIAL = "SECTION_EVIDENCE_PARTIAL"
SECTION_EVIDENCE_UNRESOLVED = "SECTION_EVIDENCE_UNRESOLVED"

MAX_REPLAN_ATTEMPTS = 1
MAX_PRIMARY_MATERIALS = 6
MAX_REFERENCE_MATERIALS = 12
MAX_SECTION_EVIDENCE_CANDIDATES = 12

_SECTION_FIELDS = (
    "section_purpose",
    "expected_learning_outcome",
    "current_task_action",
    "knowledge_scope",
    "case_purpose",
    "exercise_purpose",
    "assessment_purpose",
    "activity_purpose",
)
_TEXT_FIELDS = (
    "section_purpose",
    "expected_learning_outcome",
    "current_task_action",
    "case_purpose",
    "exercise_purpose",
    "assessment_purpose",
    "activity_purpose",
)
_PLACEHOLDER_MARKERS = (
    "to be planned",
    "core concepts and learning tasks",
    "key knowledge points",
    "placeholder",
    "complete practical tasks in",
    "using video and document evidence",
    "using video and document",
    "understand the core concepts and learning tasks",
    "待规划",
    "待补充",
    "核心概念、材料来源和学习任务",
    "关键知识点",
    "学习目标",
)


@dataclass
class CompletenessIssue:
    issue_type: str
    outline_node_id: str
    severity: str
    expected_requirement: str
    current_state: str
    supporting_evidence_ids: list[str] = field(default_factory=list)
    missing_support: list[str] = field(default_factory=list)
    recommended_action: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletenessReport:
    status: str
    book_id: str
    plan_fingerprint: str
    issues: list[CompletenessIssue] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    replan_attempts: int = 0
    deterministic_repairs: list[str] = field(default_factory=list)
    curriculum_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    section_evidence_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    safe_to_freeze: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompletenessOptimizationResult:
    book_plan: BookPlan
    initial_report: CompletenessReport
    final_report: CompletenessReport
    replan_attempts: int = 0
    replan_history: list[dict[str, Any]] = field(default_factory=list)
    resolved_issue_keys: list[str] = field(default_factory=list)
    book_plan_changed: bool = False
    replan_issues: list[CompletenessIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_report": self.initial_report.to_dict(),
            "final_report": self.final_report.to_dict(),
            "replan_attempts": self.replan_attempts,
            "replan_history": list(self.replan_history),
            "resolved_issue_keys": list(self.resolved_issue_keys),
            "book_plan_changed": self.book_plan_changed,
            "replan_issues": [asdict(issue) for issue in self.replan_issues],
            "book_plan_fingerprint": book_plan_fingerprint(self.book_plan),
        }


@dataclass
class BookPlanReplanPatch:
    """A bounded patch extracted from a planner proposal."""

    existing_node_updates: dict[str, dict[str, Any]] = field(default_factory=dict)
    new_node_proposals: list[dict[str, Any]] = field(default_factory=list)
    evidence_binding_updates: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    curriculum_gap_resolutions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TargetedSectionPatch:
    """The only patch shape accepted for a section-level completeness probe."""

    chapter_id: str
    section_id: str
    identity: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    primary_material_ids: list[str] = field(default_factory=list)
    reference_material_ids: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class CurriculumResolutionPatch:
    expected_requirement: str
    coverage: str
    supporting_evidence_ids: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class SectionEvidenceCoverageDecision:
    """A bounded evidence decision for one existing section.

    The decision is an overlay: it can select authorized chunks and describe
    obligation coverage, but it cannot change section identity or structure.
    """

    chapter_id: str
    section_id: str
    status: str
    primary_material_ids: list[str] = field(default_factory=list)
    reference_material_ids: list[str] = field(default_factory=list)
    obligation_coverage: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0


def compile_targeted_section_patch(
    book_plan: BookPlan,
    proposal: dict[str, Any],
    chunks: list[EvidenceChunk],
    *,
    authorized_evidence_ids: set[str] | None = None,
) -> tuple[BookPlanReplanPatch | None, list[CompletenessIssue]]:
    """Validate one small section patch without constructing a new BookPlan.

    The validator is deliberately stricter than the legacy full-plan adapter:
    chapter/section IDs and identity context are mandatory, and evidence must
    come from the supplied section whitelist.  There is no ordinal fallback.
    """

    diagnostics: list[CompletenessIssue] = []
    chapter_id = str(proposal.get("chapter_id") or "")
    section_id = str(proposal.get("section_id") or "")
    chapters = {chapter.chapter_id: chapter for chapter in book_plan.chapters}
    sections = {section.section_id: (chapter, section) for chapter in book_plan.chapters for section in chapter.sections}
    found = sections.get(section_id)
    if not chapter_id or not section_id or found is None or chapter_id not in chapters or found[0].chapter_id != chapter_id:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id or "unknown",
                "high",
                "targeted patch names an existing chapter_id and section_id",
                f"chapter_id={chapter_id}; section_id={section_id}",
                recommended_action="reject the patch; do not use ordinal fallback",
                provenance={"source": "targeted section patch compiler", "rule": "stable node identity"},
            )
        )
        return None, diagnostics

    chapter, existing = found
    identity = proposal.get("identity") if isinstance(proposal.get("identity"), dict) else {}
    identity_title = str(identity.get("section_title") or identity.get("title") or "")
    identity_points = {str(value) for value in identity.get("knowledge_point_ids", []) if value}
    identity_scope = {str(value) for value in identity.get("knowledge_scope", []) if value}
    if not (
        identity_title and _normalize_text(identity_title) == _normalize_text(existing.title)
        or identity_points and identity_points.intersection(existing.knowledge_point_ids)
        or identity_scope and identity_scope.intersection(existing.knowledge_scope)
    ):
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "high",
                "targeted patch identity context matches the existing section",
                f"title={identity_title}; points={sorted(identity_points)}; scope={sorted(identity_scope)}",
                recommended_action="reject the patch and request identity-aligned output",
                provenance={"source": "targeted section patch compiler", "rule": "title/knowledge/scope proof"},
            )
        )
        return None, diagnostics

    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    whitelist = authorized_evidence_ids
    if whitelist is None:
        whitelist = {chunk.chunk_id for chunk in _section_candidates(chapter, existing, chunks)}
    evidence_values = [
        str(value)
        for value in [
            *(proposal.get("primary_material_ids") or []),
            *(proposal.get("reference_material_ids") or []),
        ]
        if value
    ]
    unauthorized = [value for value in evidence_values if value not in chunk_map or value not in whitelist]
    if unauthorized:
        diagnostics.append(
            CompletenessIssue(
                EVIDENCE_BINDING_GAP,
                section_id,
                "high",
                "targeted patch evidence IDs are in the section whitelist",
                f"unauthorized evidence IDs: {unauthorized}",
                supporting_evidence_ids=sorted(whitelist),
                missing_support=unauthorized,
                recommended_action="reject the patch; retrieve a bounded section packet",
                provenance={"source": "targeted section patch compiler", "rule": "authorized evidence ID whitelist"},
            )
        )
        return None, diagnostics

    raw_fields = proposal.get("fields") if isinstance(proposal.get("fields"), dict) else {}
    fields: dict[str, Any] = {}
    unknown_fields = sorted(set(raw_fields) - set(_TEXT_FIELDS) - {"knowledge_scope"})
    if unknown_fields:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "high",
                "targeted patch contains only completeness metadata fields",
                f"unknown fields: {unknown_fields}",
                recommended_action="reject unknown fields",
                provenance={"source": "targeted section patch compiler", "rule": "field whitelist"},
            )
        )
        return None, diagnostics
    for field_name in _TEXT_FIELDS:
        value = raw_fields.get(field_name)
        if not isinstance(value, str) or not value.strip() or _is_placeholder(value):
            continue
        if field_name == "expected_learning_outcome" and not _is_observable_learning_outcome(value):
            continue
        if _is_placeholder(getattr(existing, field_name)):
            fields[field_name] = value.strip()
    if not existing.knowledge_scope and isinstance(raw_fields.get("knowledge_scope"), list):
        values = [str(value).strip() for value in raw_fields["knowledge_scope"] if str(value).strip()]
        if values:
            fields["knowledge_scope"] = values

    patch = BookPlanReplanPatch()
    if fields:
        patch.existing_node_updates[section_id] = {
            "chapter_id": chapter_id,
            "fields": fields,
            "identity": identity,
            "rationale": str(proposal.get("rationale") or ""),
        }
    primary = _dedupe([str(value) for value in proposal.get("primary_material_ids", []) if value])
    references = _dedupe([str(value) for value in proposal.get("reference_material_ids", []) if value and str(value) not in primary])
    if primary or references:
        patch.evidence_binding_updates[section_id] = {
            "chapter_id": chapter_id,
            "identity": identity,
            "primary_material_ids": primary,
            "reference_material_ids": references,
        }
    if not fields and not primary and not references:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "medium",
                "targeted patch changes at least one currently missing field or bounded evidence binding",
                "empty or non-actionable patch",
                recommended_action="reject without mutation",
                provenance={"source": "targeted section patch compiler", "rule": "non-empty patch"},
            )
        )
        return None, diagnostics
    return patch, diagnostics


def apply_targeted_section_patch(
    book_plan: BookPlan,
    proposal: dict[str, Any],
    chunks: list[EvidenceChunk],
    *,
    authorized_evidence_ids: set[str] | None = None,
) -> tuple[BookPlan, list[CompletenessIssue]]:
    """Compile and apply one section patch, or return the original plan."""

    patch, diagnostics = compile_targeted_section_patch(
        book_plan,
        proposal,
        chunks,
        authorized_evidence_ids=authorized_evidence_ids,
    )
    if patch is None:
        return book_plan, diagnostics
    merged = _merge_replan_patch(book_plan, patch, chunks)
    return merged or book_plan, diagnostics


def section_evidence_obligations(
    chapter: BookChapterPlan,
    section: BookSectionPlan,
) -> list[dict[str, str]]:
    """Return the small, auditable responsibility set used for evidence binding."""

    obligations: list[dict[str, str]] = []
    values = (
        ("section_purpose", section.section_purpose),
        ("expected_learning_outcome", section.expected_learning_outcome),
        ("current_task_action", section.current_task_action),
        ("case_purpose", section.case_purpose if section.needs_case else ""),
        ("exercise_purpose", section.exercise_purpose if section.needs_exercises else ""),
        ("assessment_purpose", section.assessment_purpose),
        ("activity_purpose", section.activity_purpose),
    )
    for key, value in values:
        if value and not _is_placeholder(value):
            obligations.append({"key": key, "text": str(value).strip()})
    for value in section.knowledge_scope or section.knowledge_point_ids:
        if value and str(value).strip():
            obligations.append({"key": f"knowledge_scope:{value}", "text": str(value).strip()})
    if not obligations:
        obligations.append({"key": "section_identity", "text": " ".join([chapter.title, section.title])})
    return obligations


def _coverage_status_from_obligations(obligations: list[dict[str, Any]], has_selected: bool) -> str:
    if not has_selected:
        return SECTION_EVIDENCE_UNRESOLVED
    statuses = {str(item.get("status") or "UNRESOLVED").upper() for item in obligations}
    if statuses == {"SUPPORTED"}:
        return SECTION_EVIDENCE_SUFFICIENT
    if "SUPPORTED" in statuses or "PARTIAL" in statuses:
        return SECTION_EVIDENCE_PARTIAL
    # A bounded, authorized selection exists but lexical evidence cannot prove
    # every obligation.  Keep this as an explicit partial audit state; only an
    # empty candidate/selection is unresolved.  A semantic coverage judgement
    # may later promote or retain this status, but evidence count alone never
    # promotes it to sufficient.
    return SECTION_EVIDENCE_PARTIAL


def compile_section_evidence_coverage(
    book_plan: BookPlan,
    proposal: dict[str, Any],
    chunks: list[EvidenceChunk],
    *,
    authorized_evidence_ids: set[str],
) -> tuple[SectionEvidenceCoverageDecision | None, list[CompletenessIssue]]:
    """Validate one model coverage judgement against a bounded candidate pool."""

    diagnostics: list[CompletenessIssue] = []
    chapter_id = str(proposal.get("chapter_id") or "")
    section_id = str(proposal.get("section_id") or "")
    sections = {
        section.section_id: (chapter, section)
        for chapter in book_plan.chapters
        for section in chapter.sections
    }
    found = sections.get(section_id)
    if not chapter_id or found is None or found[0].chapter_id != chapter_id:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id or "unknown",
                "high",
                "evidence coverage names an existing chapter_id and section_id",
                f"chapter_id={chapter_id}; section_id={section_id}",
                recommended_action="reject the evidence decision; do not use ordinal fallback",
                provenance={"source": "section evidence coverage compiler", "rule": "stable node identity"},
            )
        )
        return None, diagnostics
    chapter, section = found
    identity = proposal.get("identity") if isinstance(proposal.get("identity"), dict) else {}
    title = str(identity.get("section_title") or identity.get("title") or "")
    points = {str(value) for value in identity.get("knowledge_point_ids", []) if value}
    if not ((title and _normalize_text(title) == _normalize_text(section.title)) or (points and points.intersection(section.knowledge_point_ids))):
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "high",
                "evidence coverage identity matches the existing section",
                f"title={title}; knowledge_point_ids={sorted(points)}",
                recommended_action="reject the evidence decision and request identity-aligned output",
                provenance={"source": "section evidence coverage compiler", "rule": "title/knowledge proof"},
            )
        )
        return None, diagnostics
    known = {chunk.chunk_id for chunk in chunks if chunk.chunk_id}
    primary = _dedupe([str(value) for value in proposal.get("primary_material_ids", []) if value])
    references = _dedupe([str(value) for value in proposal.get("reference_material_ids", []) if value and str(value) not in primary])
    selected = primary + references
    unauthorized = [value for value in selected if value not in known or value not in authorized_evidence_ids]
    if unauthorized:
        diagnostics.append(
            CompletenessIssue(
                EVIDENCE_BINDING_GAP,
                section_id,
                "high",
                "section evidence IDs stay inside the retrieved candidate whitelist",
                f"unauthorized evidence IDs: {unauthorized}",
                missing_support=unauthorized,
                recommended_action="reject the evidence decision; retrieve only within the bounded packet",
                provenance={"source": "section evidence coverage compiler", "rule": "candidate whitelist"},
            )
        )
        return None, diagnostics
    status = str(proposal.get("status") or "").upper()
    if status not in {SECTION_EVIDENCE_SUFFICIENT, SECTION_EVIDENCE_PARTIAL, SECTION_EVIDENCE_UNRESOLVED}:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "high",
                "section evidence status is one of the three explicit coverage states",
                f"status={status}",
                recommended_action="reject the evidence decision",
                provenance={"source": "section evidence coverage compiler", "rule": "closed status enum"},
            )
        )
        return None, diagnostics
    raw_obligations = proposal.get("obligation_coverage")
    raw_obligations = raw_obligations if isinstance(raw_obligations, list) else []
    allowed_keys = {item["key"] for item in section_evidence_obligations(chapter, section)}
    normalized_obligations: list[dict[str, Any]] = []
    for item in raw_obligations:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("obligation_key") or "")
        if not key or key not in allowed_keys:
            diagnostics.append(
                CompletenessIssue(
                    MANUAL_REVIEW,
                    section_id,
                    "high",
                    "coverage decision references only declared section obligations",
                    f"unknown obligation key={key}",
                    recommended_action="reject unknown coverage obligations",
                    provenance={"source": "section evidence coverage compiler", "rule": "obligation whitelist"},
                )
            )
            return None, diagnostics
        item_status = str(item.get("status") or "UNRESOLVED").upper()
        if item_status not in {"SUPPORTED", "PARTIAL", "UNRESOLVED"}:
            item_status = "UNRESOLVED"
        ids = _dedupe([str(value) for value in item.get("evidence_ids", []) if value])
        if any(value not in selected for value in ids):
            diagnostics.append(
                CompletenessIssue(
                    EVIDENCE_BINDING_GAP,
                    section_id,
                    "high",
                    "obligation evidence IDs are selected by the same bounded section packet",
                    f"obligation={key}; evidence_ids={ids}",
                    recommended_action="reject the decision and keep the obligation unresolved",
                    provenance={"source": "section evidence coverage compiler", "rule": "selected evidence provenance"},
                )
            )
            return None, diagnostics
        normalized_obligations.append({"key": key, "status": item_status, "evidence_ids": ids, "rationale": str(item.get("rationale") or "")})
    missing_keys = sorted(allowed_keys - {item["key"] for item in normalized_obligations})
    if missing_keys:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "high",
                "coverage decision accounts for every current section obligation",
                f"missing obligation keys={missing_keys}",
                missing_support=missing_keys,
                recommended_action="reject incomplete coverage output; request a compact complete judgement",
                provenance={"source": "section evidence coverage compiler", "rule": "obligation completeness"},
            )
        )
        return None, diagnostics
    computed = _coverage_status_from_obligations(normalized_obligations, bool(selected))
    if status != computed:
        diagnostics.append(
            CompletenessIssue(
                MANUAL_REVIEW,
                section_id,
                "high",
                "declared section evidence status agrees with obligation-level coverage",
                f"declared={status}; computed={computed}",
                recommended_action="reject inconsistent coverage judgement; do not override deterministic classification",
                provenance={"source": "section evidence coverage compiler", "rule": "status/obligation consistency"},
            )
        )
        return None, diagnostics
    try:
        confidence = float(proposal.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return SectionEvidenceCoverageDecision(
        chapter_id=chapter_id,
        section_id=section_id,
        status=status,
        primary_material_ids=primary,
        reference_material_ids=references,
        obligation_coverage=normalized_obligations,
        rationale=str(proposal.get("rationale") or ""),
        confidence=confidence,
    ), diagnostics


def compile_curriculum_resolution_patch(
    proposal: dict[str, Any],
    coverage: dict[str, dict[str, Any]],
    chunks: list[EvidenceChunk],
) -> tuple[CurriculumResolutionPatch | None, list[CompletenessIssue]]:
    """Validate a curriculum-only patch separately from section metadata."""

    expected = str(proposal.get("expected_requirement") or "")
    status = str(proposal.get("coverage") or "").upper()
    current = coverage.get(expected, {})
    allowed = set(str(value) for value in current.get("supporting_evidence_ids", []) if value)
    known = {chunk.chunk_id for chunk in chunks if chunk.chunk_id}
    ids = _dedupe([str(value) for value in proposal.get("supporting_evidence_ids", []) if value])
    if not expected or status not in {"COVERED", "PARTIALLY_COVERED", "NO_SUPPORT_FOUND"}:
        return None, [
            CompletenessIssue(
                MANUAL_REVIEW,
                "book",
                "high",
                "curriculum resolution patch has an explicit requirement and coverage status",
                f"expected={expected}; coverage={status}",
                recommended_action="reject the curriculum patch",
                provenance={"source": "curriculum patch compiler", "rule": "strict resolution schema"},
            )
        ]
    unauthorized = [value for value in ids if value not in known or value not in allowed]
    if unauthorized:
        return None, [
            CompletenessIssue(
                EVIDENCE_BINDING_GAP,
                "book",
                "high",
                "curriculum resolution uses only retrieved candidate evidence",
                f"unauthorized evidence IDs: {unauthorized}",
                missing_support=unauthorized,
                recommended_action="reject the curriculum patch",
                provenance={"source": "curriculum patch compiler", "rule": "retrieval whitelist"},
            )
        ]
    if status in {"COVERED", "PARTIALLY_COVERED"} and not ids:
        return None, [
            CompletenessIssue(
                EVIDENCE_BINDING_GAP,
                "book",
                "high",
                "covered curriculum resolution includes supporting evidence IDs",
                f"coverage={status}; supporting_evidence_ids=[]",
                missing_support=[expected],
                recommended_action="reject the resolution and retain the coverage gap",
                provenance={"source": "curriculum patch compiler", "rule": "non-empty support for positive coverage"},
            )
        ]
    return CurriculumResolutionPatch(expected, status, ids, str(proposal.get("rationale") or "")), []


def diagnose_book_plan_completeness(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    domain_config: DomainConfig | None = None,
    curriculum_coverage: dict[str, dict[str, Any]] | None = None,
    section_evidence_coverage: dict[str, dict[str, Any]] | None = None,
) -> CompletenessReport:
    """Inspect a not-yet-frozen BookPlan without changing it."""

    config = domain_config or default_domain_config()
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    issues: list[CompletenessIssue] = []
    coverage = _normalize_curriculum_coverage(book_plan, chunks, config, curriculum_coverage)
    section_coverage = _normalize_section_evidence_coverage(book_plan, section_evidence_coverage)
    issues.extend(_diagnose_curriculum_scope_v2(book_plan, chunks, config, coverage))
    issues.extend(_diagnose_chapter_goals(book_plan, chunks, chunk_map))
    issues.extend(_diagnose_primary_ownership(book_plan, chunk_map))

    for chapter in book_plan.chapters:
        for section in chapter.sections:
            candidates = _section_candidates(chapter, section, chunks)
            issues.extend(_diagnose_section_fields(chapter, section, candidates))
            if section.section_id not in section_coverage:
                candidate_ids = [chunk.chunk_id for chunk in candidates[:MAX_SECTION_EVIDENCE_CANDIDATES]]
                primary = [value for value in section.primary_material_ids if value in chunk_map]
                references = [value for value in section.reference_material_ids if value in chunk_map]
                status, obligations = _deterministic_section_evidence_status(
                    section,
                    candidates,
                    [value for value in [*primary, *references] if value in candidate_ids],
                    chunk_map,
                    section_evidence_obligations(chapter, section),
                )
                section_coverage[section.section_id] = {
                    "status": status,
                    "primary_material_ids": primary,
                    "reference_material_ids": references,
                    "obligation_coverage": obligations,
                    "candidate_ids": candidate_ids,
                    "previous_evidence_ids": _dedupe([*primary, *references]),
                    "retrieved_candidate_ids": candidate_ids,
                    "accepted_evidence_ids": _dedupe([value for value in [*primary, *references] if value in candidate_ids]),
                    "rationale": "deterministic bounded retrieval and obligation coverage",
                    "source": "deterministic section evidence coverage",
                }
            issues.extend(
                _diagnose_section_evidence(
                    section,
                    candidates,
                    chunk_map,
                    section_coverage.get(section.section_id),
                    section_evidence_obligations(chapter, section),
                )
            )

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.issue_type] = counts.get(issue.issue_type, 0) + 1
    status = _report_status(issues)
    return CompletenessReport(
        status=status,
        book_id=book_plan.book_id,
        plan_fingerprint=book_plan_fingerprint(book_plan),
        issues=issues,
        counts=counts,
        curriculum_coverage=coverage,
        section_evidence_coverage=section_coverage,
        safe_to_freeze=status == "PASS",
    )


def optimize_book_plan_completeness(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    domain_config: DomainConfig | None = None,
    replanner: Callable[[BookPlan, list[CompletenessIssue]], BookPlan | None] | None = None,
    curriculum_coverage: dict[str, dict[str, Any]] | None = None,
    section_evidence_coverage: dict[str, dict[str, Any]] | None = None,
    max_replan_attempts: int = MAX_REPLAN_ATTEMPTS,
    allow_replan: bool = True,
) -> CompletenessOptimizationResult:
    """Apply bounded, pre-freeze repairs and optionally ask the original planner once.

    Existing outline identity/order and knowledge ownership are preserved. A
    replanner may append a supported chapter/section, but it cannot rename,
    delete, reorder, or move an existing node.
    """

    initial = diagnose_book_plan_completeness(
        book_plan,
        chunks,
        domain_config=domain_config,
        curriculum_coverage=curriculum_coverage,
        section_evidence_coverage=section_evidence_coverage,
    )
    current = book_plan
    history: list[dict[str, Any]] = []
    deterministic_repairs: list[str] = []
    replan_issues: list[CompletenessIssue] = []
    attempts = 0

    if allow_replan:
        current, deterministic_repairs = _apply_deterministic_repairs(current, chunks)
        if deterministic_repairs:
            history.append(
                {
                    "kind": "DETERMINISTIC_BOUNDED_REPLAN",
                    "attempt": 0,
                    "changed": True,
                    "repaired_fields": list(deterministic_repairs),
                }
            )

    current_report = diagnose_book_plan_completeness(
        current,
        chunks,
        domain_config=domain_config,
        curriculum_coverage=curriculum_coverage,
        section_evidence_coverage=section_evidence_coverage,
    )
    retry_limit = max(0, min(int(max_replan_attempts), MAX_REPLAN_ATTEMPTS))
    while allow_replan and replanner and attempts < retry_limit and _needs_replan(current_report):
        attempts += 1
        candidate = replanner(current, current_report.issues)
        if candidate is None:
            history.append(
                {
                    "kind": "ORIGINAL_PLANNER_REPLAN",
                    "attempt": attempts,
                    "changed": False,
                    "reason": "planner_returned_no_candidate",
                }
            )
            break
        patch, patch_issues = _compile_replan_patch(current, candidate, chunks)
        replan_issues.extend(patch_issues)
        merged = _merge_replan_patch(current, patch, chunks) if patch is not None else None
        if merged is None or book_plan_fingerprint(merged) == book_plan_fingerprint(current):
            history.append(
                {
                    "kind": "ORIGINAL_PLANNER_REPLAN",
                    "attempt": attempts,
                    "changed": False,
                    "reason": "candidate_rejected_or_no_safe_change",
                    "replan_issues": [asdict(issue) for issue in patch_issues],
                }
            )
            break
        current = merged
        current, repairs = _apply_deterministic_repairs(current, chunks)
        deterministic_repairs.extend(repairs)
        history.append(
            {
                "kind": "ORIGINAL_PLANNER_REPLAN",
                "attempt": attempts,
                "changed": True,
                "candidate_fingerprint": book_plan_fingerprint(candidate),
                "merged_fingerprint": book_plan_fingerprint(current),
                "repaired_fields": list(repairs),
                "patch": asdict(patch) if patch is not None else None,
                "replan_issues": [asdict(issue) for issue in patch_issues],
            }
        )
        current_report = diagnose_book_plan_completeness(
            current,
            chunks,
            domain_config=domain_config,
            curriculum_coverage=curriculum_coverage,
            section_evidence_coverage=section_evidence_coverage,
        )

    final_report = diagnose_book_plan_completeness(
        current,
        chunks,
        domain_config=domain_config,
        curriculum_coverage=curriculum_coverage,
        section_evidence_coverage=section_evidence_coverage,
    )
    final_report.replan_attempts = attempts
    final_report.deterministic_repairs = list(deterministic_repairs)
    initial_keys = {_issue_key(issue) for issue in initial.issues}
    final_keys = {_issue_key(issue) for issue in final_report.issues}
    return CompletenessOptimizationResult(
        book_plan=current,
        initial_report=initial,
        final_report=final_report,
        replan_attempts=attempts,
        replan_history=history,
        resolved_issue_keys=sorted(initial_keys - final_keys),
        book_plan_changed=book_plan_fingerprint(current) != book_plan_fingerprint(book_plan),
        replan_issues=replan_issues,
    )


def render_completeness_report_markdown(result: CompletenessOptimizationResult) -> str:
    initial = result.initial_report
    final = result.final_report
    lines = [
        "# BookPlan Completeness Report",
        "",
        f"- initial_status: {initial.status}",
        f"- final_status: {final.status}",
        f"- safe_to_freeze: {final.safe_to_freeze}",
        f"- replan_attempts: {result.replan_attempts}",
        f"- book_plan_changed: {result.book_plan_changed}",
        f"- resolved_issue_count: {len(result.resolved_issue_keys)}",
        "",
        "## Final issue counts",
        "",
    ]
    if final.counts:
        lines.extend(f"- {key}: {value}" for key, value in sorted(final.counts.items()))
    else:
        lines.append("- none")
    lines.extend(["", "## Issues", ""])
    if not final.issues:
        lines.append("No unresolved completeness issues.")
    for issue in final.issues:
        evidence = ", ".join(issue.supporting_evidence_ids) or "none"
        missing = ", ".join(issue.missing_support) or "none"
        lines.extend(
            [
                f"### {issue.issue_type} — {issue.outline_node_id}",
                "",
                f"- severity: {issue.severity}",
                f"- expected_requirement: {issue.expected_requirement}",
                f"- current_state: {issue.current_state}",
                f"- supporting_evidence_ids: {evidence}",
                f"- missing_support: {missing}",
                f"- recommended_action: {issue.recommended_action}",
                f"- provenance: {issue.provenance}",
                "",
            ]
        )
    lines.extend(["", "## Replan diagnostics", ""])
    if not result.replan_issues:
        lines.append("No rejected replan proposals.")
    for issue in result.replan_issues:
        lines.append(
            f"- `{issue.issue_type}` `{issue.outline_node_id}`: "
            f"{issue.current_state}; recommendation={issue.recommended_action}"
        )
    lines.extend(["", "## Section evidence coverage", ""])
    for section_id, item in sorted(final.section_evidence_coverage.items()):
        lines.append(
            f"- `{section_id}`: {item.get('status', SECTION_EVIDENCE_UNRESOLVED)}; "
            f"primary={len(item.get('primary_material_ids', []))}; "
            f"reference={len(item.get('reference_material_ids', []))}; "
            f"source={item.get('source', '')}"
        )
    return "\n".join(lines)


def _diagnose_curriculum_scope(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    config: DomainConfig,
) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    covered_text = [
        value
        for chapter in book_plan.chapters
        for value in [
            chapter.title,
            *chapter.learning_goals,
            *(section.title for section in chapter.sections),
            *(point for section in chapter.sections for point in section.knowledge_point_ids),
        ]
    ]
    for expected in config.chapter_order:
        if not expected.strip() or any(_text_matches(expected, actual) for actual in covered_text):
            continue
        candidates = [chunk.chunk_id for chunk in chunks if _text_matches(expected, _chunk_search_text(chunk))]
        if candidates:
            issue_type = REPLANNABLE_PLAN_GAP
            action = "让原 BookPlan planner 在一次 bounded replan 中补齐有授权素材支持的课程范围。"
            missing = []
        else:
            issue_type = UNRESOLVED_SOURCE_COVERAGE
            action = "补充授权素材或人工确认该课程范围不在本书范围内；不得使用模型常识补齐。"
            missing = [expected]
        issues.append(
            CompletenessIssue(
                issue_type=issue_type,
                outline_node_id="book",
                severity="high",
                expected_requirement=f"curriculum scope covers {expected}",
                current_state="expected scope is not represented by chapter, section, goal, or knowledge point",
                supporting_evidence_ids=_dedupe(candidates),
                missing_support=missing,
                recommended_action=action,
                provenance={
                    "source": "DomainConfig.chapter_order + BookPlan projection",
                    "rule": "semantic text/topic match with authorized EvidenceChunk candidate recall",
                },
            )
        )
    return issues


def _diagnose_curriculum_scope_v2(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    config: DomainConfig,
    coverage: dict[str, dict[str, Any]],
) -> list[CompletenessIssue]:
    """Classify curriculum coverage after bounded candidate retrieval."""

    issues: list[CompletenessIssue] = []
    for expected in config.chapter_order:
        if not expected.strip():
            continue
        item = coverage.get(expected, {})
        status = str(item.get("status") or "NO_SUPPORT_FOUND").upper()
        if status == "COVERED":
            continue
        candidates = [str(value) for value in item.get("supporting_evidence_ids", []) if value]
        if status == "PARTIALLY_COVERED":
            issue_type = REPLANNABLE_PLAN_GAP
            action = "Apply a bounded curriculum patch using only retrieved evidence; do not invent unsupported scope."
            missing = [f"complete curriculum representation for {expected}"]
        else:
            issue_type = GENUINE_SOURCE_GAP
            action = "Provide authorized evidence or explicitly confirm that this scope is outside the book; do not use model knowledge."
            missing = [expected]
        issues.append(
            CompletenessIssue(
                issue_type=issue_type,
                outline_node_id="book",
                severity="high",
                expected_requirement=f"curriculum scope covers {expected}",
                current_state=f"curriculum coverage status={status}; scope is not fully represented by the current BookPlan",
                supporting_evidence_ids=_dedupe(candidates),
                missing_support=missing,
                recommended_action=action,
                provenance={
                    "source": "DomainConfig.chapter_order + bounded curriculum retrieval",
                    "coverage_status": status,
                    "retrieval_rationale": item.get("rationale", ""),
                },
            )
        )
    return issues


def _normalize_curriculum_coverage(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    config: DomainConfig,
    supplied: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Build the coverage record consumed by curriculum diagnosis."""

    covered_text = [
        value
        for chapter in book_plan.chapters
        for value in [
            chapter.title,
            *chapter.learning_goals,
            *(section.title for section in chapter.sections),
            *(point for section in chapter.sections for point in section.knowledge_point_ids),
        ]
    ]
    known_chunk_ids = {chunk.chunk_id for chunk in chunks if chunk.chunk_id}
    result: dict[str, dict[str, Any]] = {}
    for expected in config.chapter_order:
        if not expected.strip():
            continue
        raw = supplied.get(expected) if isinstance(supplied, dict) else None
        if isinstance(raw, dict):
            status = str(raw.get("status") or "NO_SUPPORT_FOUND").upper()
            if status not in {"COVERED", "PARTIALLY_COVERED", "NO_SUPPORT_FOUND"}:
                status = "NO_SUPPORT_FOUND"
            result[expected] = {
                "status": status,
                "supporting_evidence_ids": _dedupe(
                    [str(value) for value in raw.get("supporting_evidence_ids", []) if value and str(value) in known_chunk_ids]
                ),
                "candidate_scores": {
                    str(key): value
                    for key, value in dict(raw.get("candidate_scores") or {}).items()
                    if str(key) in known_chunk_ids
                },
                "rationale": str(raw.get("rationale") or "supplied bounded retrieval result"),
                "source": str(raw.get("source") or "bounded curriculum retrieval"),
            }
            continue
        if any(_text_matches(expected, actual) for actual in covered_text):
            result[expected] = {
                "status": "COVERED",
                "supporting_evidence_ids": [],
                "candidate_scores": {},
                "rationale": "expected scope is represented by the current BookPlan",
                "source": "BookPlan projection",
            }
            continue
        ranked = _curriculum_candidates(expected, chunks)
        result[expected] = {
            "status": "PARTIALLY_COVERED" if ranked else "NO_SUPPORT_FOUND",
            "supporting_evidence_ids": [chunk.chunk_id for _, chunk in ranked],
            "candidate_scores": {chunk.chunk_id: round(score, 6) for score, chunk in ranked},
            "rationale": "bounded title/summary/content/keyword/material metadata retrieval",
            "source": "deterministic curriculum candidate retrieval",
        }
    return result


def _curriculum_candidates(expected: str, chunks: list[EvidenceChunk], *, limit: int = 5) -> list[tuple[float, EvidenceChunk]]:
    """Recall evidence candidates without treating recall as curriculum proof."""

    ranked: list[tuple[float, EvidenceChunk]] = []
    for chunk in chunks:
        lexical = _text_match_score(expected, _chunk_search_text(chunk))
        if lexical <= 0:
            continue
        quality = 0.05 * float(chunk.score.teaching_value or 0.0) + 0.03 * float(chunk.score.relevance or 0.0)
        ranked.append((lexical + quality, chunk))
    ranked.sort(key=lambda item: (item[0], item[1].chunk_id), reverse=True)
    return ranked[:limit]


def _diagnose_chapter_goals(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    chunk_map: dict[str, EvidenceChunk],
) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    for chapter in book_plan.chapters:
        if not chapter.learning_goals or any(_is_learning_goal_placeholder(goal) for goal in chapter.learning_goals):
            evidence_ids = _chapter_candidate_evidence_ids(chapter, chunks, chunk_map)
            issue_type = REPLANNABLE_PLAN_GAP if evidence_ids else UNRESOLVED_SOURCE_COVERAGE
            issues.append(
                CompletenessIssue(
                    issue_type=issue_type,
                    outline_node_id=chapter.chapter_id,
                    severity="high",
                    expected_requirement="chapter learning goals are specific and non-placeholder",
                    current_state=str(chapter.learning_goals),
                    supporting_evidence_ids=evidence_ids,
                    missing_support=[] if evidence_ids else ["chapter-scoped evidence"],
                    recommended_action=(
                        "derive bounded chapter goals from the chapter title, section scope, and authorized evidence"
                        if evidence_ids
                        else "provide chapter-scoped evidence before freezing"
                    ),
                    provenance={
                        "source": "BookChapterPlan.learning_goals",
                        "rule": "observable-action, specific-object, and generic-goal detection",
                        "candidate_retrieval": "chapter title + section scope over all supplied EvidenceChunks",
                    },
                )
            )
    return issues


def _diagnose_primary_ownership(book_plan: BookPlan, chunk_map: dict[str, EvidenceChunk]) -> list[CompletenessIssue]:
    # A source chunk may legitimately support more than one section.  The
    # completeness gate constrains each section's authorized pool, but does
    # not invent a global one-owner rule that would turn shared source support
    # into a false planning gap.  Unknown IDs are diagnosed at section scope.
    return []


def _diagnose_section_fields(
    chapter: BookChapterPlan,
    section: BookSectionPlan,
    candidates: list[EvidenceChunk],
) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    evidence_ids = [chunk.chunk_id for chunk in candidates]
    required_fields = list(_TEXT_FIELDS)
    if not section.needs_case:
        required_fields.remove("case_purpose")
    if not section.needs_exercises:
        required_fields.remove("exercise_purpose")
    if not section.knowledge_scope:
        required_fields.append("knowledge_scope")
    for field_name in required_fields:
        value = getattr(section, field_name)
        missing = not value or (isinstance(value, str) and _is_placeholder(value))
        if field_name == "expected_learning_outcome" and isinstance(value, str):
            missing = not _is_observable_learning_outcome(value)
        if not missing:
            continue
        issue_type = REPLANNABLE_PLAN_GAP if evidence_ids else UNRESOLVED_SOURCE_COVERAGE
        expected = {
            "section_purpose": "specific section purpose",
            "expected_learning_outcome": "observable non-placeholder learning outcome",
            "current_task_action": "current task action",
            "knowledge_scope": "knowledge point scope",
            "case_purpose": "case module intent",
            "exercise_purpose": "exercise module intent",
            "assessment_purpose": "assessment module intent",
            "activity_purpose": "activity module intent",
        }[field_name]
        issues.append(
            CompletenessIssue(
                issue_type=issue_type,
                outline_node_id=section.section_id,
                severity="high" if field_name in {"section_purpose", "expected_learning_outcome", "knowledge_scope"} else "medium",
                expected_requirement=expected,
                current_state=str(value or "empty"),
                supporting_evidence_ids=_dedupe(evidence_ids),
                missing_support=[] if evidence_ids else [section.title or "section-scoped evidence"],
                recommended_action=(
                    f"fill {field_name} from section title, knowledge scope, task context, and authorized evidence"
                    if evidence_ids
                    else "provide section-scoped evidence before planning this responsibility"
                ),
                provenance={
                    "source": f"BookSectionPlan.{field_name}",
                    "chapter_id": chapter.chapter_id,
                    "rule": "required section/module intent presence, placeholder, and outcome quality detection",
                },
            )
        )
    return issues


def _diagnose_section_evidence(
    section: BookSectionPlan,
    candidates: list[EvidenceChunk],
    chunk_map: dict[str, EvidenceChunk],
    supplied: dict[str, Any] | None = None,
    obligation_definitions: list[dict[str, str]] | None = None,
) -> list[CompletenessIssue]:
    """Validate a bounded section evidence pool, not the whole retrieval corpus.

    A large candidate recall set is not itself a gap.  The old implementation
    treated every unbound candidate as a required reference, which produced a
    false EVIDENCE_BINDING_GAP for otherwise complete sections.  The section
    status is now based on obligation coverage and provenance of the selected
    bounded pool.
    """

    issues: list[CompletenessIssue] = []
    candidate_ids = [chunk.chunk_id for chunk in candidates[:MAX_SECTION_EVIDENCE_CANDIDATES]]
    primary = [material_id for material_id in section.primary_material_ids if material_id in chunk_map]
    references = [material_id for material_id in section.reference_material_ids if material_id in chunk_map]
    invalid = [
        material_id
        for material_id in [*section.primary_material_ids, *section.reference_material_ids]
        if material_id not in chunk_map
    ]
    if invalid:
        issues.append(
            CompletenessIssue(
                issue_type=EVIDENCE_BINDING_GAP if candidate_ids else UNRESOLVED_SOURCE_COVERAGE,
                outline_node_id=section.section_id,
                severity="high",
                expected_requirement="all section evidence IDs resolve to authorized EvidenceChunk records",
                current_state=f"unresolved evidence IDs: {invalid}",
                supporting_evidence_ids=_dedupe(candidate_ids),
                missing_support=invalid if not candidate_ids else [],
                recommended_action="replace invalid IDs only with bounded candidates from the same section/task evidence scope",
                provenance={"source": "BookSectionPlan primary/reference material IDs", "rule": "canonical chunk ID whitelist"},
            )
        )
    bound_ids = [value for value in [*primary, *references] if value in candidate_ids]
    obligations = (supplied or {}).get("obligation_coverage") if supplied else None
    if isinstance(obligations, list) and obligations:
        status = str((supplied or {}).get("status") or "").upper()
    else:
        status, obligations = _deterministic_section_evidence_status(
            section,
            candidates,
            bound_ids,
            chunk_map,
            obligation_definitions,
        )
    if status == SECTION_EVIDENCE_UNRESOLVED:
        issue_type = EVIDENCE_BINDING_GAP if candidate_ids else UNRESOLVED_SOURCE_COVERAGE
        issues.append(
            CompletenessIssue(
                issue_type=issue_type,
                outline_node_id=section.section_id,
                severity="high",
                expected_requirement="section obligations have authorized, bounded evidence support",
                current_state=f"status={status}; primary={len(primary)}; references={len(references)}; candidates={len(candidate_ids)}",
                supporting_evidence_ids=_dedupe(candidate_ids),
                missing_support=[item.get("key", section.title) for item in obligations if item.get("status") != "SUPPORTED"],
                recommended_action="run section-local bounded evidence retrieval and coverage judgement",
                provenance={"source": "section evidence coverage", "rule": "obligation-level support, not evidence count"},
            )
        )
    return issues


def _deterministic_section_evidence_status(
    section: BookSectionPlan,
    candidates: list[EvidenceChunk],
    bound_ids: list[str],
    chunk_map: dict[str, EvidenceChunk],
    obligation_definitions: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Conservative lexical first pass used when no semantic judgement exists."""

    if not candidates or not bound_ids:
        return SECTION_EVIDENCE_UNRESOLVED, [
            {"key": "section_evidence", "status": "UNRESOLVED", "evidence_ids": []}
        ]
    selected = [chunk_map[value] for value in bound_ids if value in chunk_map]
    obligations: list[dict[str, Any]] = []
    definitions = obligation_definitions or [
        {"key": f"knowledge_scope:{value}", "text": str(value)}
        for value in (section.knowledge_scope or section.knowledge_point_ids or [section.title])
    ]
    for definition in definitions:
        text = str(definition.get("text") or "")
        matches = [chunk.chunk_id for chunk in selected if _text_match_score(text, _chunk_search_text(chunk)) >= 1.0]
        obligations.append({"key": str(definition.get("key") or "section_evidence"), "status": "SUPPORTED" if matches else "UNRESOLVED", "evidence_ids": matches})
    status = _coverage_status_from_obligations(obligations, bool(selected))
    return status, obligations


def _normalize_section_evidence_coverage(
    book_plan: BookPlan,
    supplied: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    known = {
        section.section_id
        for chapter in book_plan.chapters
        for section in chapter.sections
    }
    result: dict[str, dict[str, Any]] = {}
    for section_id, raw in (supplied or {}).items():
        if section_id not in known or not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "").upper()
        if status not in {SECTION_EVIDENCE_SUFFICIENT, SECTION_EVIDENCE_PARTIAL, SECTION_EVIDENCE_UNRESOLVED}:
            continue
        result[section_id] = {
            "status": status,
            "primary_material_ids": _dedupe([str(value) for value in raw.get("primary_material_ids", []) if value]),
            "reference_material_ids": _dedupe([str(value) for value in raw.get("reference_material_ids", []) if value]),
            "obligation_coverage": list(raw.get("obligation_coverage") or []),
            "candidate_ids": _dedupe([str(value) for value in raw.get("candidate_ids", []) if value]),
            "previous_evidence_ids": _dedupe([str(value) for value in raw.get("previous_evidence_ids", []) if value]),
            "retrieved_candidate_ids": _dedupe([str(value) for value in raw.get("retrieved_candidate_ids", []) if value]),
            "accepted_evidence_ids": _dedupe([str(value) for value in raw.get("accepted_evidence_ids", []) if value]),
            "confidence": float(raw.get("confidence") or 0.0),
            "rationale": str(raw.get("rationale") or ""),
            "source": str(raw.get("source") or "bounded section evidence judgement"),
        }
    return result


def _apply_deterministic_repairs(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlan, list[str]]:
    # Keep deterministic repairs limited to facts that are already explicit
    # in the plan or evidence bindings.  Learning goals/module purposes are
    # never synthesized from a title-only fallback.
    return _apply_grounded_repairs(book_plan, chunks)


def _apply_grounded_repairs(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlan, list[str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    changed: list[str] = []
    chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        sections: list[BookSectionPlan] = []
        for section in chapter.sections:
            candidates = _section_candidates(chapter, section, chunks)
            candidate_ids = [chunk.chunk_id for chunk in candidates]
            knowledge_scope = list(section.knowledge_scope)
            if not knowledge_scope and section.knowledge_point_ids:
                knowledge_scope = list(section.knowledge_point_ids)
                changed.append(f"{section.section_id}.knowledge_scope")

            primary = [material_id for material_id in section.primary_material_ids if material_id in chunk_map]
            references = [
                material_id
                for material_id in section.reference_material_ids
                if material_id in chunk_map and material_id not in primary
            ]
            target_primary = min(MAX_PRIMARY_MATERIALS, max(1, min(2, len(candidate_ids)))) if candidate_ids else len(primary)
            for material_id in candidate_ids:
                if len(primary) >= target_primary:
                    break
                if material_id not in primary:
                    primary.append(material_id)
            for material_id in candidate_ids:
                if material_id not in primary and material_id not in references and len(references) < MAX_REFERENCE_MATERIALS:
                    references.append(material_id)
            if primary != section.primary_material_ids or references != section.reference_material_ids:
                changed.append(f"{section.section_id}.evidence_ownership")
            sections.append(
                replace(
                    section,
                    primary_material_ids=primary,
                    reference_material_ids=references,
                    knowledge_scope=knowledge_scope,
                )
            )
        chapter_primary = _dedupe(
            [
                material_id
                for material_id in [
                    *chapter.primary_material_ids,
                    *(material_id for section in sections for material_id in section.primary_material_ids),
                ]
                if material_id in chunk_map
            ]
        )
        chapters.append(replace(chapter, sections=sections, primary_material_ids=chapter_primary))
    return replace(book_plan, chapters=chapters), sorted(set(changed))


def _legacy_unreachable_deterministic_repairs(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlan, list[str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    changed: list[str] = []
    chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        chapter_evidence = _chapter_evidence_ids(chapter, chunk_map)
        goals = list(chapter.learning_goals)
        if (not goals or any(_is_placeholder(goal) for goal in goals)) and chapter_evidence:
            goals = [
                f"围绕{chapter.title}明确本章知识范围与学习任务。",
                f"能够基于授权素材说明{chapter.title}的关键学习要点并完成相关任务。",
            ]
            changed.append(f"{chapter.chapter_id}.learning_goals")
        sections: list[BookSectionPlan] = []
        for section in chapter.sections:
            candidates = _section_candidates(chapter, section, chunks)
            candidate_ids = [chunk.chunk_id for chunk in candidates]
            values = {
                "section_purpose": section.section_purpose,
                "expected_learning_outcome": section.expected_learning_outcome,
                "current_task_action": section.current_task_action,
                "knowledge_scope": list(section.knowledge_scope),
                "case_purpose": section.case_purpose,
                "exercise_purpose": section.exercise_purpose,
                "assessment_purpose": section.assessment_purpose,
                "activity_purpose": section.activity_purpose,
            }
            if candidates:
                values["section_purpose"] = values["section_purpose"] if not _is_placeholder(values["section_purpose"]) else (
                    f"明确“{section.title}”在当前任务中的教学作用。"
                )
                values["expected_learning_outcome"] = values["expected_learning_outcome"] if not _is_placeholder(values["expected_learning_outcome"]) else (
                    f"能够基于授权素材说明“{section.title}”的关键学习要点。"
                )
                values["current_task_action"] = values["current_task_action"] if not _is_placeholder(values["current_task_action"]) else (
                    f"在当前任务中围绕“{section.title}”完成观察、说明或操作任务。"
                )
                values["knowledge_scope"] = values["knowledge_scope"] or list(section.knowledge_point_ids) or [section.title]
                if section.needs_case and _is_placeholder(values["case_purpose"]):
                    values["case_purpose"] = f"用授权素材构造与“{section.title}”直接相关的应用情境。"
                if section.needs_exercises and _is_placeholder(values["exercise_purpose"]):
                    values["exercise_purpose"] = f"通过“{section.title}”相关的识别、说明或操作练习巩固本节要点。"
                if _is_placeholder(values["assessment_purpose"]):
                    values["assessment_purpose"] = f"评价学生能否基于授权素材完成“{section.title}”对应的学习要求。"
                if _is_placeholder(values["activity_purpose"]):
                    values["activity_purpose"] = f"组织围绕“{section.title}”的观察、讨论或操作活动。"
                for field_name in _SECTION_FIELDS:
                    if getattr(section, field_name) != values[field_name]:
                        changed.append(f"{section.section_id}.{field_name}")

            primary = [material_id for material_id in section.primary_material_ids if material_id in chunk_map]
            references = [
                material_id
                for material_id in section.reference_material_ids
                if material_id in chunk_map and material_id not in primary
            ]
            target_primary = min(MAX_PRIMARY_MATERIALS, max(1, min(2, len(candidate_ids)))) if candidate_ids else len(primary)
            for material_id in candidate_ids:
                if len(primary) >= target_primary:
                    break
                if material_id in primary:
                    continue
                primary.append(material_id)
            for material_id in candidate_ids:
                if material_id not in primary and material_id not in references and len(references) < MAX_REFERENCE_MATERIALS:
                    references.append(material_id)
            if primary != section.primary_material_ids or references != section.reference_material_ids:
                changed.append(f"{section.section_id}.evidence_ownership")
            sections.append(
                replace(
                    section,
                    primary_material_ids=primary,
                    reference_material_ids=references,
                    section_purpose=values["section_purpose"],
                    expected_learning_outcome=values["expected_learning_outcome"],
                    current_task_action=values["current_task_action"],
                    knowledge_scope=values["knowledge_scope"],
                    case_purpose=values["case_purpose"],
                    exercise_purpose=values["exercise_purpose"],
                    assessment_purpose=values["assessment_purpose"],
                    activity_purpose=values["activity_purpose"],
                )
            )
        chapter_primary = _dedupe(
            [
                material_id
                for material_id in [
                    *chapter.primary_material_ids,
                    *(material_id for section in sections for material_id in section.primary_material_ids),
                ]
                if material_id in chunk_map
            ]
        )
        chapters.append(replace(chapter, learning_goals=goals, sections=sections, primary_material_ids=chapter_primary))
    return replace(book_plan, chapters=chapters), sorted(set(changed))


def _compile_replan_patch(
    current: BookPlan,
    candidate: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlanReplanPatch | None, list[CompletenessIssue]]:
    """Compile a legacy full-plan proposal into an explicit bounded patch."""

    if candidate.book_id != current.book_id or candidate.title != current.title:
        return None, [
            CompletenessIssue(
                MANUAL_REVIEW,
                "book",
                "high",
                "replan candidate keeps the existing book identity",
                f"candidate book_id/title={candidate.book_id}/{candidate.title}",
                recommended_action="reject the candidate and request a patch for the existing book",
                provenance={"source": "bounded replan patch compiler", "rule": "book identity whitelist"},
            )
        ]

    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    current_chapters = {chapter.chapter_id: chapter for chapter in current.chapters}
    current_sections = {
        section.section_id: section
        for chapter in current.chapters
        for section in chapter.sections
    }
    proposed_sections = list(current_sections.values())
    patch = BookPlanReplanPatch()
    diagnostics: list[CompletenessIssue] = []

    for chapter in candidate.chapters:
        current_chapter = current_chapters.get(chapter.chapter_id)
        if current_chapter is None:
            if _safe_new_chapter(chapter, chunk_map) and not _duplicate_chapter(chapter, current.chapters):
                patch.new_node_proposals.append({"node_type": "chapter", "node": chapter})
            continue

        # Do not delete existing goals merely because the observable-outcome
        # checker rejects them.  An invalid goal is a planning gap that needs a
        # verified replacement, not permission to shrink the curriculum.
        # Replacement is only safe when the source plan has no goals at all;
        # the targeted patch contract can later provide a complete replacement
        # with explicit evidence.
        if not current_chapter.learning_goals:
            proposed_goals = [goal for goal in chapter.learning_goals if not _is_learning_goal_placeholder(goal)]
            goal_evidence = [
                material_id
                for material_id in [
                    *chapter.primary_material_ids,
                    *chapter.reference_material_ids,
                    *(material_id for section in chapter.sections for material_id in [*section.primary_material_ids, *section.reference_material_ids]),
                ]
                if material_id in chunk_map
            ]
            if proposed_goals and goal_evidence:
                patch.existing_node_updates[chapter.chapter_id] = {"learning_goals": proposed_goals}
                patch.curriculum_gap_resolutions.append(
                    {
                        "outline_node_id": chapter.chapter_id,
                        "field": "learning_goals",
                        "source": "bounded planner proposal",
                        "supporting_evidence_ids": _dedupe(goal_evidence),
                    }
                )

        for section in chapter.sections:
            existing = current_sections.get(section.section_id)
            if existing is not None:
                if not _section_identity_matches(existing, section):
                    diagnostics.append(
                        CompletenessIssue(
                            MANUAL_REVIEW,
                            section.section_id,
                            "high",
                            "replan proposal preserves the existing section's semantic identity",
                            (
                                f"existing title/knowledge={existing.title}/{existing.knowledge_point_ids}; "
                                f"candidate title/knowledge={section.title}/{section.knowledge_point_ids}"
                            ),
                            supporting_evidence_ids=[
                                item
                                for item in [*section.primary_material_ids, *section.reference_material_ids]
                                if item in chunk_map
                            ],
                            recommended_action="reject the proposal and request an identity-aligned bounded patch",
                            provenance={
                                "source": "bounded replan patch compiler",
                                "rule": "stable outline node identity must be proven before applying metadata or evidence",
                            },
                        )
                    )
                    continue
                authorized = [
                    material_id
                    for material_id in [*section.primary_material_ids, *section.reference_material_ids]
                    if material_id in chunk_map
                ]
                fields: dict[str, Any] = {}
                if authorized:
                    for field_name in _TEXT_FIELDS:
                        proposed = getattr(section, field_name)
                        current_value = getattr(existing, field_name)
                        if not proposed or _is_placeholder(proposed):
                            continue
                        if field_name == "expected_learning_outcome" and not _is_observable_learning_outcome(proposed):
                            continue
                        if _is_placeholder(current_value) or (
                            field_name == "expected_learning_outcome"
                            and not _is_observable_learning_outcome(str(current_value or ""))
                        ):
                            fields[field_name] = proposed
                    if not existing.knowledge_scope and section.knowledge_scope:
                        fields["knowledge_scope"] = list(section.knowledge_scope)
                if fields or authorized:
                    patch.existing_node_updates.setdefault(section.section_id, {})["fields"] = fields
                patch.evidence_binding_updates[section.section_id] = {
                    "primary_material_ids": [item for item in section.primary_material_ids if item in chunk_map],
                    "reference_material_ids": [item for item in section.reference_material_ids if item in chunk_map],
                }
                continue

            duplicate_reason = _duplicate_section_reason(section, proposed_sections)
            if duplicate_reason:
                diagnostics.append(
                    CompletenessIssue(
                        REPLAN_DUPLICATE_SECTION,
                        section.section_id,
                        "high",
                        "new section has an independent teaching scope",
                        duplicate_reason,
                        supporting_evidence_ids=[
                            item
                            for item in [*section.primary_material_ids, *section.reference_material_ids]
                            if item in chunk_map
                        ],
                        recommended_action="reject the new node and preserve the existing outline node",
                        provenance={
                            "source": "bounded replan patch compiler",
                            "rule": "title + knowledge_scope + knowledge_point_ids + purpose duplicate protection",
                        },
                    )
                )
                continue
            if _safe_new_section(section, chunk_map):
                patch.new_node_proposals.append(
                    {"node_type": "section", "chapter_id": chapter.chapter_id, "node": section}
                )
                proposed_sections.append(section)

    return patch, diagnostics


def _merge_replan_patch(
    current: BookPlan,
    patch: BookPlanReplanPatch | None,
    chunks: list[EvidenceChunk],
) -> BookPlan | None:
    if patch is None:
        return None
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    updates = patch.existing_node_updates
    evidence_updates = patch.evidence_binding_updates
    chapters: list[BookChapterPlan] = []
    for chapter in current.chapters:
        chapter_update = updates.get(chapter.chapter_id, {})
        chapter_goals = chapter_update.get("learning_goals", chapter.learning_goals)
        sections: list[BookSectionPlan] = []
        for section in chapter.sections:
            node_update = updates.get(section.section_id, {})
            fields = node_update.get("fields", {})
            values = {field_name: getattr(section, field_name) for field_name in _SECTION_FIELDS}
            for field_name, value in fields.items():
                if field_name == "expected_learning_outcome" and not _is_observable_learning_outcome(str(value or "")):
                    continue
                if _is_placeholder(values.get(field_name)) or (
                    field_name == "expected_learning_outcome"
                    and not _is_observable_learning_outcome(str(values.get(field_name) or ""))
                ):
                    values[field_name] = value

            binding = evidence_updates.get(section.section_id, {})
            primary = [item for item in section.primary_material_ids if item in chunk_map]
            references = [item for item in section.reference_material_ids if item in chunk_map and item not in primary]
            for item in binding.get("primary_material_ids", []):
                if item in chunk_map and item not in primary:
                    primary.append(item)
            for item in binding.get("reference_material_ids", []):
                if item in chunk_map and item not in primary and item not in references:
                    references.append(item)
            references = references[:MAX_REFERENCE_MATERIALS]
            sections.append(
                replace(
                    section,
                    **values,
                    primary_material_ids=primary,
                    reference_material_ids=references,
                )
            )

        for proposal in patch.new_node_proposals:
            if proposal.get("node_type") == "section" and proposal.get("chapter_id") == chapter.chapter_id:
                node = proposal.get("node")
                if isinstance(node, BookSectionPlan) and not _duplicate_section_reason(node, sections):
                    sections.append(node)
        chapters.append(replace(chapter, learning_goals=chapter_goals, sections=sections))

    for proposal in patch.new_node_proposals:
        if proposal.get("node_type") == "chapter":
            node = proposal.get("node")
            if isinstance(node, BookChapterPlan) and not _duplicate_chapter(node, chapters):
                chapters.append(node)
    return replace(current, chapters=chapters)


def _merge_replan_candidate(
    current: BookPlan,
    candidate: BookPlan,
    chunks: list[EvidenceChunk],
) -> BookPlan | None:
    patch, _issues = _compile_replan_patch(current, candidate, chunks)
    return _merge_replan_patch(current, patch, chunks)


def _safe_new_chapter(chapter: BookChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> bool:
    return bool(
        chapter.title.strip()
        and chapter.sections
        and any(
            material_id in chunk_map
            for section in chapter.sections
            for material_id in [*section.primary_material_ids, *section.reference_material_ids]
        )
    )


def _safe_new_section(section: BookSectionPlan, chunk_map: dict[str, EvidenceChunk]) -> bool:
    return bool(
        section.section_id
        and section.title.strip()
        and section.knowledge_point_ids
        and any(
            material_id in chunk_map
            for material_id in [*section.primary_material_ids, *section.reference_material_ids]
        )
    )


def _duplicate_section_reason(
    proposal: BookSectionPlan,
    existing_sections: list[BookSectionPlan],
) -> str | None:
    proposal_title = _normalize_text(proposal.title)
    proposal_points = {_normalize_text(value) for value in proposal.knowledge_point_ids if value}
    proposal_scope = {_normalize_text(value) for value in proposal.knowledge_scope if value}
    proposal_purpose = _normalize_text(proposal.section_purpose)
    for existing in existing_sections:
        if proposal_title and proposal_title == _normalize_text(existing.title):
            return f"title duplicates {existing.section_id}: {existing.title}"
        existing_points = {_normalize_text(value) for value in existing.knowledge_point_ids if value}
        existing_scope = {_normalize_text(value) for value in existing.knowledge_scope if value}
        if proposal_points and proposal_points == existing_points:
            return f"knowledge_point_ids duplicate {existing.section_id}"
        if proposal_scope and proposal_scope == existing_scope:
            return f"knowledge_scope duplicates {existing.section_id}"
        if proposal_purpose and proposal_purpose == _normalize_text(existing.section_purpose):
            if proposal_points & existing_points or proposal_scope & existing_scope:
                return f"section_purpose and scope duplicate {existing.section_id}"
    return None


def _section_identity_matches(existing: BookSectionPlan, proposal: BookSectionPlan) -> bool:
    """Return whether a proposal can safely target an existing section.

    Section IDs generated by a fresh planner are ordinal and therefore are not
    sufficient identity evidence by themselves.  Metadata and evidence may be
    merged only when the title, knowledge ownership, or explicit scope has a
    deterministic overlap.  Ambiguous proposals fail closed in the compiler.
    """

    if _normalize_text(existing.title) == _normalize_text(proposal.title):
        return True
    existing_points = {_normalize_text(value) for value in existing.knowledge_point_ids if value}
    proposal_points = {_normalize_text(value) for value in proposal.knowledge_point_ids if value}
    if existing_points & proposal_points:
        return True
    existing_scope = {_normalize_text(value) for value in existing.knowledge_scope if value}
    proposal_scope = {_normalize_text(value) for value in proposal.knowledge_scope if value}
    if existing_scope & proposal_scope:
        return True
    return _text_match_score(existing.title, proposal.title) >= 1.0


def _duplicate_chapter(proposal: BookChapterPlan, existing: list[BookChapterPlan]) -> bool:
    title = _normalize_text(proposal.title)
    return bool(title and any(title == _normalize_text(item.title) for item in existing))


def _section_candidates(
    chapter: BookChapterPlan,
    section: BookSectionPlan,
    chunks: list[EvidenceChunk],
) -> list[EvidenceChunk]:
    section_text = " ".join(
        [
            chapter.title,
            section.title,
            *section.knowledge_point_ids,
            *section.knowledge_scope,
            section.section_purpose,
            section.expected_learning_outcome,
            section.current_task_action,
            section.case_purpose if section.needs_case else "",
            section.exercise_purpose if section.needs_exercises else "",
            section.assessment_purpose,
            section.activity_purpose,
        ]
    )
    scored: list[tuple[float, EvidenceChunk]] = []
    current_ids = set(section.primary_material_ids) | set(section.reference_material_ids)
    for chunk in chunks:
        score = _text_match_score(section_text, _chunk_search_text(chunk))
        if chunk.chunk_id in current_ids:
            score = max(score, 3.0)
        if score <= 0:
            continue
        scored.append((score, chunk))
    scored.sort(
        key=lambda item: (
            item[0],
            item[1].score.teaching_value,
            item[1].score.relevance,
            item[1].score.confidence,
            len(item[1].content or "") + len(item[1].summary or ""),
        ),
        reverse=True,
    )
    return [chunk for _, chunk in scored]


def retrieve_section_evidence_candidates(
    chapter: BookChapterPlan,
    section: BookSectionPlan,
    chunks: list[EvidenceChunk],
) -> list[EvidenceChunk]:
    """Return the bounded evidence packet used by a targeted section patch.

    Keeping this small public wrapper prevents production callers from reaching
    into the legacy planner/patch compiler internals while preserving the same
    deterministic retrieval and whitelist rules.
    """

    return _section_candidates(chapter, section, chunks)[:MAX_SECTION_EVIDENCE_CANDIDATES]


def _chunk_search_text(chunk: EvidenceChunk) -> str:
    metadata = " ".join(str(value) for value in chunk.metadata.values() if isinstance(value, (str, int, float)))
    return " ".join(
        [
            chunk.title,
            chunk.summary,
            chunk.content[:1000],
            " ".join(chunk.keywords),
            chunk.subject,
            chunk.material_block,
            chunk.recommended_chapter,
            metadata,
        ]
    )


def _chapter_evidence_ids(chapter: BookChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> list[str]:
    return _dedupe(
        [
            material_id
            for material_id in [
                *chapter.primary_material_ids,
                *chapter.reference_material_ids,
                *(
                    material_id
                    for section in chapter.sections
                    for material_id in [*section.primary_material_ids, *section.reference_material_ids]
                ),
            ]
            if material_id in chunk_map
        ]
    )


def _chapter_candidate_evidence_ids(
    chapter: BookChapterPlan,
    chunks: list[EvidenceChunk],
    chunk_map: dict[str, EvidenceChunk],
) -> list[str]:
    """Recall legal chapter-scoped candidates before calling a gap genuine."""

    ids = list(_chapter_evidence_ids(chapter, chunk_map))
    for section in chapter.sections:
        for chunk in _section_candidates(chapter, section, chunks):
            if chunk.chunk_id and chunk.chunk_id not in ids:
                ids.append(chunk.chunk_id)
    return ids


def _report_status(issues: list[CompletenessIssue]) -> str:
    if not issues:
        return "PASS"
    if any(issue.issue_type in {GENUINE_SOURCE_GAP, UNRESOLVED_SOURCE_COVERAGE, MANUAL_REVIEW} for issue in issues):
        return "BLOCKED"
    return "REPLAN_REQUIRED"


def _needs_replan(report: CompletenessReport) -> bool:
    return any(issue.issue_type in {REPLANNABLE_PLAN_GAP, EVIDENCE_BINDING_GAP} for issue in report.issues)


def _issue_key(issue: CompletenessIssue) -> str:
    return f"{issue.issue_type}|{issue.outline_node_id}|{issue.expected_requirement}"


def _is_placeholder(value: Any) -> bool:
    if isinstance(value, list):
        return not value
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return len(text) < 4 or any(marker.lower() in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_learning_goal_placeholder(value: Any) -> bool:
    return _is_placeholder(value) or not _is_observable_learning_outcome(str(value or ""))


def _is_observable_learning_outcome(value: str) -> bool:
    """Reject generic completion prose while keeping domain-neutral outcomes."""

    text = str(value or "").strip()
    if _is_placeholder(text):
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False
    action_terms = (
        "identify", "describe", "explain", "inspect", "perform", "complete", "select",
        "adjust", "compare", "judge", "evaluate", "analyze", "demonstrate", "recognize",
        "识别", "描述", "说明", "解释", "检查", "完成", "选择", "调整", "比较", "判断", "分析", "操作", "观察",
    )
    if not any(term in lowered for term in action_terms):
        return False
    # A generic verb alone is not an outcome.  Require a non-trivial object
    # and reject the common “using video/document evidence” boilerplate.
    compact = re.sub(r"\s+", "", lowered)
    return len(compact) >= 10


def _text_matches(expected: str, actual: str) -> bool:
    return _text_match_score(expected, actual) >= 1.0


def _text_match_score(expected: str, actual: str) -> float:
    left_raw = str(expected or "").lower().strip()
    right_raw = str(actual or "").lower().strip()
    left = _normalize_text(left_raw)
    right = _normalize_text(right_raw)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    # Tokenize the original text, not the separator-stripped form.  Keeping
    # word boundaries is important for bounded candidate recall (for example,
    # ``basic operation`` should match ``basic operation conditions``).
    left_terms = _terms(left_raw)
    right_terms = _terms(right_raw)
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    if overlap >= 2:
        return overlap / max(1, min(len(left_terms), len(right_terms)))
    return 0.0


def _normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").lower())


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", value.lower()))


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
