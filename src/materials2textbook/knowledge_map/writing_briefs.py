from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from materials2textbook.knowledge_map.models import (
    InstructionalAvailabilityState,
    KnowledgePoint,
    LearningRole,
    PlannedOccurrence,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.semantic_evaluation import SemanticPlanningEvaluation


ROLE_WRITING_CONTRACTS = {
    LearningRole.INTRO: (
        "Establish only a usable intuition and boundary. Do not provide the complete definition, procedure, "
        "or later constraints unless they are explicitly listed in must_teach_facets."
    ),
    LearningRole.TEACH: (
        "Teach the listed must_teach_facets explicitly with evidence. Do not re-explain must_not_reteach_facets; "
        "mention them only as the minimum prerequisite context when needed."
    ),
    LearningRole.RECALL: (
        "Restore only the minimum already-taught context required by this task. Do not re-teach a full definition, "
        "method, or procedure."
    ),
    LearningRole.APPLY: (
        "Assume already-available facets. Directly apply them to the current task or case; do not repeat their teaching explanation."
    ),
    LearningRole.EXTEND: (
        "State what is already available, then teach only the listed new condition, constraint, variant, or higher-order facet."
    ),
}


def _text_behavior_constraints(
    *,
    role: str,
    available_facets: list[str],
    must_teach_facets: list[str],
    extension_keys: list[str],
    repeated_aspects: list[str],
) -> tuple[list[str], list[str], int, list[str], list[str]]:
    """Create executable rendering constraints without another semantic decision.

    The strings in ``must_avoid_patterns`` are stable checker keys, not LLM
    suggestions.  They intentionally describe text behaviour rather than a
    new interpretation of the planned occurrence.
    """
    allowed = ["evidence-grounded statements from the source chunks"]
    forbidden = list(repeated_aspects)
    avoid = list(repeated_aspects)
    required = [f"facet:{item}" for item in must_teach_facets]
    recap_limit = 0

    if role == LearningRole.INTRO:
        allowed.extend(["initial intuition", "scope boundary for later teaching"])
        forbidden.extend(["complete procedure", "later conditions or variants"])
        avoid.extend(["complete procedure", "parameter/method rule"])
    elif role == LearningRole.TEACH:
        if must_teach_facets or extension_keys:
            allowed.extend(["explicit teaching of the new planned facet", "minimal prerequisite context"])
            recap_limit = 1 if available_facets else 0
        else:
            # A duplicate-TEACH risk has no instructional increment.  Its
            # render is deliberately narrowed instead of silently falling
            # back to the usual full TEACH template.
            allowed.extend(["necessary transition", "current task context", "minimal recap only"])
            forbidden.extend([
                "definition", "principle explanation", "complete procedure", "parameter/method rule",
            ])
            avoid.extend([
                "definition", "principle explanation", "complete procedure", "parameter/method rule",
            ])
            recap_limit = 2
    elif role == LearningRole.RECALL:
        allowed.extend(["minimal recovery of prerequisite context", "current task transition"])
        forbidden.extend(["complete definition", "complete procedure", "full method teaching"])
        avoid.extend(["definition", "principle explanation", "complete procedure", "parameter/method rule"])
        recap_limit = 2
    elif role == LearningRole.APPLY:
        allowed.extend(["current task action", "observable application and evaluation"])
        forbidden.extend(["definition", "principle explanation", "complete procedure", "parameter/method rule"])
        avoid.extend(["definition", "principle explanation", "complete procedure", "parameter/method rule"])
        recap_limit = 1
    elif role == LearningRole.EXTEND:
        allowed.extend(["one-sentence known-method bridge", "new condition, constraint, or variant"])
        forbidden.extend(["complete standard definition", "complete known method"])
        avoid.extend(["definition", "complete procedure", "parameter/method rule"])
        recap_limit = 1

    required.extend(f"extension:{item}" for item in extension_keys)
    # Preserve order for a predictable prompt and report diff.
    return (
        list(dict.fromkeys(allowed)),
        list(dict.fromkeys(forbidden)),
        recap_limit,
        required,
        list(dict.fromkeys(avoid)),
    )


@dataclass(frozen=True)
class OccurrenceWritingBrief:
    """Immutable writing constraint derived from an accepted instructional trajectory."""

    occurrence_id: str
    source_knowledge_point_id: str
    canonical_knowledge_id: str
    source_title: str
    canonical_title: str
    chapter_id: str
    section_id: str
    role: str
    already_available_facets: list[str]
    required_facets: list[str]
    must_teach_facets: list[str]
    must_not_reteach_facets: list[str]
    extension_keys: list[str]
    repeated_aspects_to_avoid: list[str]
    prerequisite_context: list[str]
    contribution_goal: str
    source_chunk_ids: list[str]
    writing_contract: str
    semantic_delta_evidence_ids: list[str] = field(default_factory=list)
    task_ordinal: int = 0
    occurrence_ordinal: int = 0
    allowed_content: list[str] = field(default_factory=list)
    forbidden_content: list[str] = field(default_factory=list)
    max_recap_sentences: int = 0
    must_include_points: list[str] = field(default_factory=list)
    must_avoid_patterns: list[str] = field(default_factory=list)
    availability_source_occurrence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FallbackOccurrence:
    """An explicit non-semantic rendering unit; never silently routed as a brief."""

    occurrence_id: str
    source_knowledge_point_id: str
    canonical_knowledge_id: str
    source_title: str
    chapter_id: str
    section_id: str
    task_ordinal: int
    occurrence_ordinal: int
    source_chunk_ids: list[str]
    reason: str
    planning_confidence: float = 0.0


@dataclass(frozen=True)
class RejectedPlanOccurrence:
    """A semantic plan rejected before writing; it must never become fallback prose."""

    occurrence_id: str
    source_knowledge_point_id: str
    canonical_knowledge_id: str
    chapter_id: str
    section_id: str
    task_ordinal: int
    occurrence_ordinal: int
    reason: str
    evidence_status: str
    allowed_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DroppedOccurrenceGoal:
    """An audited no-op occurrence; it must not be rendered via fallback prose."""

    occurrence_id: str
    canonical_knowledge_id: str
    chapter_id: str
    section_id: str
    reason: str


class RenderDecision:
    RENDER = "RENDER"
    ZERO_RENDER = "ZERO_RENDER"


@dataclass(frozen=True)
class ZeroRenderOccurrence:
    """An explicit, audited decision to render no student-visible body.

    This is an execution decision, not a missing-anchor fallback and not a
    dropped BookPlan knowledge point.  It preserves the semantic occurrence and
    its frozen outline association while granting no new availability.
    """

    occurrence_id: str
    source_knowledge_point_id: str
    canonical_knowledge_id: str
    chapter_id: str
    section_id: str
    outline_node_id: str
    task_ordinal: int
    occurrence_ordinal: int
    role: str
    non_render_reason: str
    prior_verified_support: tuple[str, ...] = ()
    audit_trace: tuple[str, ...] = ()
    render_decision: str = RenderDecision.ZERO_RENDER


def decide_zero_render_occurrence(
    *,
    occurrence: PlannedOccurrence,
    delta: SemanticDelta,
    prior_verified_support: list[str] | None = None,
) -> ZeroRenderOccurrence | None:
    """Return a zero-render record only for the narrow safe eligibility case.

    The semantic facts must explicitly establish that this location has no new
    teaching increment, does not use prior knowledge in its current task, and
    does not require a recall.  Ambiguous cases remain renderable rather than
    being silently compressed away.
    """
    blockers: list[str] = []
    if occurrence.role in {LearningRole.TEACH, LearningRole.EXTEND}:
        blockers.append("current_role_has_teaching_responsibility")
    if occurrence.intended_grants or occurrence.intended_extension_keys or delta.new_facets or delta.new_extension_keys:
        blockers.append("current_occurrence_has_new_instructional_increment")
    if occurrence.uses_prior_knowledge or delta.uses_prior_knowledge:
        blockers.append("current_task_uses_prior_knowledge")
    if occurrence.recall_needed or delta.recall_needed or delta.restores_prior_context:
        blockers.append("current_occurrence_requires_recall")
    if occurrence.intended_contribution.strip() or delta.contribution_summary.strip() or delta.new_context.strip():
        blockers.append("current_occurrence_has_unresolved_contribution_or_context")
    if blockers:
        return None
    return ZeroRenderOccurrence(
        occurrence_id=occurrence.occurrence_id,
        source_knowledge_point_id=occurrence.source_knowledge_point_id,
        canonical_knowledge_id=occurrence.knowledge_id,
        chapter_id=occurrence.chapter_id,
        section_id=occurrence.section_id,
        outline_node_id=occurrence.section_id,
        task_ordinal=occurrence.position.task_ordinal,
        occurrence_ordinal=occurrence.position.occurrence_ordinal,
        role=occurrence.role,
        non_render_reason="NO_CURRENT_TEACHING_OR_TASK_USE_VALUE",
        prior_verified_support=tuple(prior_verified_support or ()),
        audit_trace=(
            "no_new_facet_or_extension",
            "current_task_does_not_use_knowledge",
            "recall_not_required",
            "no_teach_or_extend_responsibility",
        ),
    )


@dataclass
class WritingBriefCoverage:
    """Full planned-occurrence coverage, split into accepted briefs and explicit fallback."""

    briefs: list[OccurrenceWritingBrief] = field(default_factory=list)
    fallback_occurrences: list[FallbackOccurrence] = field(default_factory=list)
    rejected_plan_occurrences: list[RejectedPlanOccurrence] = field(default_factory=list)
    dropped_occurrence_goals: list[DroppedOccurrenceGoal] = field(default_factory=list)
    zero_render_occurrences: list[ZeroRenderOccurrence] = field(default_factory=list)
    execution_blocked_occurrences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_occurrences(self) -> int:
        ids = {
            item.occurrence_id for item in self.briefs
        }
        ids.update(item.occurrence_id for item in self.fallback_occurrences)
        ids.update(item.occurrence_id for item in self.rejected_plan_occurrences)
        ids.update(item.occurrence_id for item in self.dropped_occurrence_goals)
        ids.update(item.occurrence_id for item in self.zero_render_occurrences)
        ids.update(str(item.get("occurrence_id")) for item in self.execution_blocked_occurrences if item.get("occurrence_id"))
        return len(ids)


@dataclass(frozen=True)
class WritingBriefConsistencyIssue:
    """A deterministic contradiction which must fail before the writer runs."""

    occurrence_id: str
    rule: str
    details: str


def check_writing_brief_consistency(briefs: list[OccurrenceWritingBrief]) -> list[WritingBriefConsistencyIssue]:
    """Validate that a writer can satisfy each immutable brief.

    This is deliberately a narrow structural check.  It does not reinterpret a
    role or semantic delta; a failure is returned to the compilation layer
    rather than asking the writer to resolve contradictory instructions.
    """
    issues: list[WritingBriefConsistencyIssue] = []
    for brief in briefs:
        overlap = sorted(set(brief.must_teach_facets) & set(brief.must_not_reteach_facets))
        if overlap:
            issues.append(WritingBriefConsistencyIssue(
                brief.occurrence_id,
                "MUST_TEACH_AND_MUST_NOT_RETEACH_OVERLAP",
                f"facets: {', '.join(overlap)}",
            ))
        if brief.role == LearningRole.INTRO and (brief.required_facets or brief.extension_keys):
            issues.append(WritingBriefConsistencyIssue(
                brief.occurrence_id,
                "INTRO_HAS_PREREQUISITE_OR_EXTENSION",
                "INTRO may not require prior self facets or teach an extension.",
            ))
        if brief.role in {LearningRole.RECALL, LearningRole.APPLY} and (brief.must_teach_facets or brief.extension_keys):
            issues.append(WritingBriefConsistencyIssue(
                brief.occurrence_id,
                "NON_TEACH_ROLE_HAS_NEW_GRANT",
                f"role={brief.role}; facets={brief.must_teach_facets}; extensions={brief.extension_keys}",
            ))
    return issues


def _require_consistent_briefs(briefs: list[OccurrenceWritingBrief]) -> None:
    issues = check_writing_brief_consistency(briefs)
    if issues:
        details = "; ".join(f"{item.occurrence_id}:{item.rule}" for item in issues)
        raise ValueError(f"WritingBrief consistency failure; recompile final SemanticDelta/availability before writing: {details}")


def build_verified_occurrence_writing_brief(
    *,
    occurrence: PlannedOccurrence,
    delta: SemanticDelta,
    source: SourceKnowledgePoint,
    point: KnowledgePoint,
    verified_before: InstructionalAvailabilityState,
) -> OccurrenceWritingBrief:
    """Create one brief from the verified state immediately before writing.

    This is the runtime counterpart to the legacy whole-book brief builders.
    It consumes a deterministic runtime compilation result and does not infer a
    role, change a delta, or treat planned snapshots as evidence of teaching.
    ``availability_source_occurrence_ids`` preserves the rendered occurrence
    that established every available facet used by the brief.
    """
    if not occurrence.trusted_for_state:
        raise ValueError(f"Cannot create a writing brief from untrusted occurrence {occurrence.occurrence_id!r}.")
    if occurrence.role not in ROLE_WRITING_CONTRACTS:
        raise ValueError(f"Unsupported writing role {occurrence.role!r} for occurrence {occurrence.occurrence_id!r}.")
    record = verified_before.availability_by_knowledge.get(occurrence.knowledge_id)
    available = list(record.available_facets) if record else []
    prerequisite_context = [
        f"{item.knowledge_id}: {', '.join(item.required_facets) or 'context'}"
        for item in occurrence.required_prerequisites
    ]
    if occurrence.required_self_facets:
        prerequisite_context.insert(0, f"self: {', '.join(occurrence.required_self_facets)}")
    must_not = [facet for facet in available if facet not in occurrence.intended_grants]
    allowed, forbidden, recap_limit, must_include, avoid = _text_behavior_constraints(
        role=occurrence.role,
        available_facets=available,
        must_teach_facets=list(occurrence.intended_grants),
        extension_keys=list(occurrence.intended_extension_keys),
        repeated_aspects=list(delta.repeated_aspects),
    )
    source_ids: list[str] = []
    if record:
        source_ids = list(dict.fromkeys(
            record.facet_source_occurrence_ids[facet]
            for facet in available
            if facet in record.facet_source_occurrence_ids
        ))
    brief = OccurrenceWritingBrief(
        occurrence_id=occurrence.occurrence_id,
        source_knowledge_point_id=occurrence.source_knowledge_point_id,
        canonical_knowledge_id=occurrence.knowledge_id,
        source_title=source.title,
        canonical_title=point.title,
        chapter_id=occurrence.chapter_id,
        section_id=occurrence.section_id,
        role=occurrence.role,
        already_available_facets=available,
        required_facets=list(occurrence.required_self_facets),
        must_teach_facets=list(occurrence.intended_grants),
        must_not_reteach_facets=must_not,
        extension_keys=list(occurrence.intended_extension_keys),
        repeated_aspects_to_avoid=list(delta.repeated_aspects),
        prerequisite_context=prerequisite_context,
        contribution_goal=occurrence.intended_contribution,
        source_chunk_ids=list(occurrence.source_chunk_ids),
        writing_contract=ROLE_WRITING_CONTRACTS[occurrence.role],
        semantic_delta_evidence_ids=list(delta.evidence_chunk_ids),
        task_ordinal=occurrence.position.task_ordinal,
        occurrence_ordinal=occurrence.position.occurrence_ordinal,
        allowed_content=allowed,
        forbidden_content=forbidden,
        max_recap_sentences=recap_limit,
        must_include_points=must_include,
        must_avoid_patterns=avoid,
        availability_source_occurrence_ids=source_ids,
    )
    _require_consistent_briefs([brief])
    return brief


def build_occurrence_writing_briefs(evaluation: SemanticPlanningEvaluation) -> list[OccurrenceWritingBrief]:
    """Build planning-preview briefs from assumed planned availability.

    This preserves the existing whole-book planning artifact for diagnosis and
    static review.  It does not prove that earlier planned teaching rendered or
    passed local checks.  Runtime writer execution must instead use
    ``compile_occurrence_for_verified_availability`` followed by
    ``build_verified_occurrence_writing_brief``.
    """
    knowledge_map = evaluation.knowledge_map
    deltas = {item.occurrence_id: item for item in evaluation.semantic_deltas}
    snapshots = {item.occurrence_id: item for item in knowledge_map.availability_snapshots}
    sources = {item.source_knowledge_point_id: item for item in knowledge_map.source_knowledge_points}
    points = {item.knowledge_id: item for item in knowledge_map.knowledge_points}
    briefs: list[OccurrenceWritingBrief] = []
    for occurrence in knowledge_map.planned_occurrences:
        delta = deltas.get(occurrence.occurrence_id)
        snapshot = snapshots.get(occurrence.occurrence_id)
        source = sources.get(occurrence.source_knowledge_point_id)
        point = points.get(occurrence.knowledge_id)
        if not delta or not snapshot or not source or not point:
            raise ValueError(f"Cannot create a writing brief for incomplete occurrence {occurrence.occurrence_id!r}.")
        if not occurrence.trusted_for_state:
            raise ValueError(f"Cannot create a writing brief from untrusted occurrence {occurrence.occurrence_id!r}.")
        before = snapshot.before.availability_by_knowledge.get(occurrence.knowledge_id)
        available = list(before.available_facets) if before else []
        prerequisite_context = [
            f"{item.knowledge_id}: {', '.join(item.required_facets) or 'context'}"
            for item in occurrence.required_prerequisites
        ]
        if occurrence.required_self_facets:
            prerequisite_context.insert(0, f"self: {', '.join(occurrence.required_self_facets)}")
        must_not = [facet for facet in available if facet not in occurrence.intended_grants]
        allowed, forbidden, recap_limit, must_include, avoid = _text_behavior_constraints(
            role=occurrence.role,
            available_facets=available,
            must_teach_facets=list(occurrence.intended_grants),
            extension_keys=list(occurrence.intended_extension_keys),
            repeated_aspects=list(delta.repeated_aspects),
        )
        briefs.append(
            OccurrenceWritingBrief(
                occurrence_id=occurrence.occurrence_id,
                source_knowledge_point_id=occurrence.source_knowledge_point_id,
                canonical_knowledge_id=occurrence.knowledge_id,
                source_title=source.title,
                canonical_title=point.title,
                chapter_id=occurrence.chapter_id,
                section_id=occurrence.section_id,
                role=occurrence.role,
                already_available_facets=available,
                required_facets=list(occurrence.required_self_facets),
                must_teach_facets=list(occurrence.intended_grants),
                must_not_reteach_facets=must_not,
                extension_keys=list(occurrence.intended_extension_keys),
                repeated_aspects_to_avoid=list(delta.repeated_aspects),
                prerequisite_context=prerequisite_context,
                contribution_goal=occurrence.intended_contribution,
                source_chunk_ids=list(occurrence.source_chunk_ids),
                writing_contract=ROLE_WRITING_CONTRACTS[occurrence.role],
                semantic_delta_evidence_ids=list(delta.evidence_chunk_ids),
                task_ordinal=occurrence.position.task_ordinal,
                occurrence_ordinal=occurrence.position.occurrence_ordinal,
                allowed_content=allowed,
                forbidden_content=forbidden,
                max_recap_sentences=recap_limit,
                must_include_points=must_include,
                must_avoid_patterns=avoid,
            )
        )
    _require_consistent_briefs(briefs)
    return briefs


def briefs_for_chapter(briefs: list[OccurrenceWritingBrief], chapter_id: str) -> list[OccurrenceWritingBrief]:
    return [item for item in briefs if item.chapter_id == chapter_id]


def fallbacks_for_chapter(fallbacks: list[FallbackOccurrence], chapter_id: str) -> list[FallbackOccurrence]:
    return [item for item in fallbacks if item.chapter_id == chapter_id]


def render_writing_briefs_markdown(briefs: list[OccurrenceWritingBrief]) -> str:
    lines = ["# Occurrence Writing Briefs", ""]
    for brief in briefs:
        lines.extend([
            f"## {brief.occurrence_id}",
            f"- role: {brief.role}",
            f"- source / canonical: {brief.source_title} / {brief.canonical_title}",
            f"- already available: {', '.join(brief.already_available_facets) or 'none'}",
            f"- availability sources: {', '.join(brief.availability_source_occurrence_ids) or 'planning snapshot only'}",
            f"- required: {', '.join(brief.required_facets) or 'none'}",
            f"- must teach: {', '.join(brief.must_teach_facets) or 'none'}",
            f"- must not reteach: {', '.join(brief.must_not_reteach_facets) or 'none'}",
            f"- extensions: {', '.join(brief.extension_keys) or 'none'}",
            f"- repeated aspects to avoid: {', '.join(brief.repeated_aspects_to_avoid) or 'none'}",
            f"- prerequisite context: {'; '.join(brief.prerequisite_context) or 'none'}",
            f"- contribution goal: {brief.contribution_goal or 'none'}",
            f"- source chunks: {', '.join(brief.source_chunk_ids) or 'none'}",
            f"- allowed content: {', '.join(brief.allowed_content) or 'none'}",
            f"- forbidden content: {', '.join(brief.forbidden_content) or 'none'}",
            f"- max recap sentences: {brief.max_recap_sentences}",
            f"- must include: {', '.join(brief.must_include_points) or 'none'}",
            f"- must avoid patterns: {', '.join(brief.must_avoid_patterns) or 'none'}",
            f"- writing contract: {brief.writing_contract}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def build_occurrence_writing_briefs_from_payload(payload: dict[str, Any]) -> list[OccurrenceWritingBrief]:
    """Restore planning-preview briefs from a persisted Phase 1.5 artifact.

    This deliberately reads only accepted occurrence, delta and planned
    before-state fields. It does not call an LLM or re-plan identity, role or
    contribution, and it must not be used as runtime evidence that a prior
    occurrence actually taught its intended grants.
    """
    knowledge_map = payload.get("knowledge_map") if isinstance(payload, dict) else None
    if not isinstance(knowledge_map, dict):
        raise ValueError("Expected a Phase 1.5 semantic planning evaluation payload.")
    deltas = {item.get("occurrence_id"): item for item in payload.get("semantic_deltas", []) if isinstance(item, dict)}
    sources = {item.get("source_knowledge_point_id"): item for item in knowledge_map.get("source_knowledge_points", []) if isinstance(item, dict)}
    points = {item.get("knowledge_id"): item for item in knowledge_map.get("knowledge_points", []) if isinstance(item, dict)}
    snapshots = {item.get("occurrence_id"): item for item in knowledge_map.get("availability_snapshots", []) if isinstance(item, dict)}
    briefs: list[OccurrenceWritingBrief] = []
    for occurrence in knowledge_map.get("planned_occurrences", []):
        if not isinstance(occurrence, dict):
            continue
        occurrence_id = occurrence.get("occurrence_id", "")
        delta = deltas.get(occurrence_id)
        source = sources.get(occurrence.get("source_knowledge_point_id"))
        point = points.get(occurrence.get("knowledge_id"))
        snapshot = snapshots.get(occurrence_id)
        if not all((occurrence_id, delta, source, point, snapshot)) or not occurrence.get("trusted_for_state"):
            raise ValueError(f"Cannot create a writing brief from untrusted or incomplete occurrence {occurrence_id!r}.")
        before = (snapshot.get("before") or {}).get("availability_by_knowledge") or {}
        record = before.get(occurrence.get("knowledge_id")) or {}
        available = list(record.get("available_facets") or [])
        required = list(occurrence.get("required_self_facets") or [])
        prerequisite_context = [f"self: {', '.join(required)}"] if required else []
        prerequisite_context.extend(
            f"{item.get('knowledge_id')}: {', '.join(item.get('required_facets') or []) or 'context'}"
            for item in occurrence.get("required_prerequisites") or []
            if isinstance(item, dict)
        )
        role = occurrence.get("role")
        if role not in ROLE_WRITING_CONTRACTS:
            raise ValueError(f"Unsupported writing role {role!r} for occurrence {occurrence_id!r}.")
        grants = list(occurrence.get("intended_grants") or [])
        extensions = list(occurrence.get("intended_extension_keys") or [])
        repeated_aspects = list(delta.get("repeated_aspects") or [])
        allowed, forbidden, recap_limit, must_include, avoid = _text_behavior_constraints(
            role=role,
            available_facets=available,
            must_teach_facets=grants,
            extension_keys=extensions,
            repeated_aspects=repeated_aspects,
        )
        position = occurrence.get("position") or {}
        briefs.append(
            OccurrenceWritingBrief(
                occurrence_id=occurrence_id,
                source_knowledge_point_id=occurrence["source_knowledge_point_id"],
                canonical_knowledge_id=occurrence["knowledge_id"],
                source_title=str(source.get("title") or ""),
                canonical_title=str(point.get("title") or ""),
                chapter_id=occurrence["chapter_id"],
                section_id=occurrence["section_id"],
                role=role,
                already_available_facets=available,
                required_facets=required,
                must_teach_facets=grants,
                must_not_reteach_facets=[item for item in available if item not in grants],
                extension_keys=extensions,
                repeated_aspects_to_avoid=repeated_aspects,
                prerequisite_context=prerequisite_context,
                contribution_goal=str(occurrence.get("intended_contribution") or ""),
                source_chunk_ids=list(occurrence.get("source_chunk_ids") or []),
                writing_contract=ROLE_WRITING_CONTRACTS[role],
                semantic_delta_evidence_ids=list(delta.get("evidence_chunk_ids") or []),
                task_ordinal=int(position.get("task_ordinal") or 0),
                occurrence_ordinal=int(position.get("occurrence_ordinal") or 0),
                allowed_content=allowed,
                forbidden_content=forbidden,
                max_recap_sentences=recap_limit,
                must_include_points=must_include,
                must_avoid_patterns=avoid,
            )
        )
    _require_consistent_briefs(briefs)
    return briefs


def build_writing_brief_coverage(evaluation: SemanticPlanningEvaluation) -> WritingBriefCoverage:
    """Create a Phase 2B full-book coverage plan without silently dropping uncertainty."""
    from materials2textbook.io_utils import to_jsonable

    return build_writing_brief_coverage_from_payload(
        {
            "knowledge_map": to_jsonable(evaluation.knowledge_map),
            "semantic_deltas": to_jsonable(evaluation.semantic_deltas),
        }
    )


def build_writing_brief_coverage_from_payload(payload: dict[str, Any]) -> WritingBriefCoverage:
    """Split persisted semantic output into accepted brief and explicit fallback units.

    The existing strict loader remains the source of truth for accepted briefs.
    A malformed, rejected, missing or low-confidence occurrence becomes a
    record in ``fallback_occurrences`` rather than disappearing into the old
    renderer without traceability.
    """
    knowledge_map = payload.get("knowledge_map") if isinstance(payload, dict) else None
    if not isinstance(knowledge_map, dict):
        raise ValueError("Expected a Phase 1.5 semantic planning evaluation payload.")
    sources = {
        item.get("source_knowledge_point_id"): item
        for item in knowledge_map.get("source_knowledge_points", [])
        if isinstance(item, dict)
    }
    deltas = {
        item.get("occurrence_id"): item
        for item in payload.get("semantic_deltas", [])
        if isinstance(item, dict)
    }
    snapshots = {
        item.get("occurrence_id"): item
        for item in knowledge_map.get("availability_snapshots", [])
        if isinstance(item, dict)
    }
    rejected_by_occurrence = {
        str(item.get("occurrence_id")): str(item.get("reason") or "rejected_semantic_proposal")
        for item in payload.get("rejected_proposals", [])
        if isinstance(item, dict) and item.get("occurrence_id")
    }
    coverage = WritingBriefCoverage()
    base_map = dict(knowledge_map)
    for occurrence in knowledge_map.get("planned_occurrences", []):
        if not isinstance(occurrence, dict):
            continue
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        source_id = str(occurrence.get("source_knowledge_point_id") or "")
        source = sources.get(source_id) or {}
        position = occurrence.get("position") or {}
        if occurrence.get("render_decision") == RenderDecision.ZERO_RENDER:
            zero_reason = str(occurrence.get("non_render_reason") or "")
            trace = occurrence.get("zero_render_audit_trace") or []
            if not occurrence_id or not zero_reason or not isinstance(trace, list):
                coverage.fallback_occurrences.append(FallbackOccurrence(
                    occurrence_id=occurrence_id or f"fallback:{len(coverage.fallback_occurrences) + 1}",
                    source_knowledge_point_id=source_id,
                    canonical_knowledge_id=str(occurrence.get("knowledge_id") or ""),
                    source_title=str(source.get("title") or occurrence.get("context_title") or source_id),
                    chapter_id=str(occurrence.get("chapter_id") or source.get("chapter_id") or ""),
                    section_id=str(occurrence.get("section_id") or source.get("section_id") or ""),
                    task_ordinal=int(position.get("task_ordinal") or 0),
                    occurrence_ordinal=int(position.get("occurrence_ordinal") or 0),
                    source_chunk_ids=list(occurrence.get("source_chunk_ids") or source.get("source_chunk_ids") or []),
                    reason="invalid_explicit_zero_render_decision",
                    planning_confidence=float(occurrence.get("planning_confidence") or 0.0),
                ))
                continue
            else:
                coverage.zero_render_occurrences.append(ZeroRenderOccurrence(
                    occurrence_id=occurrence_id,
                    source_knowledge_point_id=source_id,
                    canonical_knowledge_id=str(occurrence.get("knowledge_id") or ""),
                    chapter_id=str(occurrence.get("chapter_id") or source.get("chapter_id") or ""),
                    section_id=str(occurrence.get("section_id") or source.get("section_id") or ""),
                    outline_node_id=str(occurrence.get("outline_node_id") or occurrence.get("section_id") or ""),
                    task_ordinal=int(position.get("task_ordinal") or 0),
                    occurrence_ordinal=int(position.get("occurrence_ordinal") or 0),
                    role=str(occurrence.get("role") or ""),
                    non_render_reason=zero_reason,
                    prior_verified_support=tuple(item for item in occurrence.get("prior_verified_support") or [] if isinstance(item, str)),
                    audit_trace=tuple(item for item in trace if isinstance(item, str)),
                ))
                continue
        if occurrence.get("evidence_resolution_status") == "DROP_OCCURRENCE_GOAL":
            coverage.dropped_occurrence_goals.append(DroppedOccurrenceGoal(
                occurrence_id=occurrence_id, canonical_knowledge_id=str(occurrence.get("knowledge_id") or ""),
                chapter_id=str(occurrence.get("chapter_id") or source.get("chapter_id") or ""),
                section_id=str(occurrence.get("section_id") or source.get("section_id") or ""),
                reason="evidence_bounded_auto_contraction_removed_all_instructional_increment",
            ))
            continue
        delta = deltas.get(occurrence_id)
        snapshot = snapshots.get(occurrence_id)
        reason = ""
        if not occurrence_id:
            reason = "missing_occurrence_id"
        elif not delta:
            reason = "missing_semantic_delta"
        elif not snapshot:
            reason = "missing_availability_snapshot"
        elif occurrence_id in rejected_by_occurrence:
            reason = f"rejected_semantic_plan:{rejected_by_occurrence[occurrence_id]}"
        elif not occurrence.get("trusted_for_state"):
            confidence = float(delta.get("confidence") or occurrence.get("planning_confidence") or 0.0)
            reason = "low_confidence_semantic_plan" if confidence < 0.75 else "untrusted_semantic_plan"
        if not reason:
            isolated_payload = {
                "knowledge_map": {**base_map, "planned_occurrences": [occurrence]},
                "semantic_deltas": [delta],
            }
            try:
                coverage.briefs.extend(build_occurrence_writing_briefs_from_payload(isolated_payload))
                continue
            except (KeyError, TypeError, ValueError) as exc:
                reason = f"invalid_semantic_plan:{type(exc).__name__}"
        coverage.fallback_occurrences.append(
            FallbackOccurrence(
                occurrence_id=occurrence_id or f"fallback:{len(coverage.fallback_occurrences) + 1}",
                source_knowledge_point_id=source_id,
                canonical_knowledge_id=str(occurrence.get("knowledge_id") or ""),
                source_title=str(source.get("title") or occurrence.get("context_title") or source_id),
                chapter_id=str(occurrence.get("chapter_id") or source.get("chapter_id") or ""),
                section_id=str(occurrence.get("section_id") or source.get("section_id") or ""),
                task_ordinal=int(position.get("task_ordinal") or 0),
                occurrence_ordinal=int(position.get("occurrence_ordinal") or 0),
                source_chunk_ids=list(occurrence.get("source_chunk_ids") or source.get("source_chunk_ids") or []),
                reason=reason or "unresolved_semantic_plan",
                planning_confidence=float(occurrence.get("planning_confidence") or 0.0),
            )
        )
    return coverage
