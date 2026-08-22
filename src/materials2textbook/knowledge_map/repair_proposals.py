"""Read-only, deterministic repair proposals for rendered occurrences.

Phase 3A deliberately has no writer, reviser, exporter, or rollback import.
It consumes immutable upstream decisions and a rendered conformance result to
describe a bounded future repair; it never changes rendered text itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re

from materials2textbook.knowledge_map.models import LearningRole
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    RenderedConformanceResult,
    RenderedOccurrence,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief


class RepairAction:
    REMOVE_RETEACH = "REMOVE_RETEACH"
    ADD_REQUIRED_FACET = "ADD_REQUIRED_FACET"
    ADD_EXTENSION = "ADD_EXTENSION"
    RESTORE_MINIMAL_RECALL = "RESTORE_MINIMAL_RECALL"
    REWRITE_TO_ROLE = "REWRITE_TO_ROLE"
    ADD_CONTRIBUTION = "ADD_CONTRIBUTION"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class RepairExecutionSafety:
    AUTO_CANDIDATE = "AUTO_CANDIDATE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


@dataclass(frozen=True)
class ImmutableUpstreamDecisions:
    """Copied for audit only; a repair planner has no authority to alter them."""

    canonical_knowledge_id: str
    learning_role: str
    prerequisite_context: tuple[str, ...]
    semantic_delta_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RepairExpectation:
    anchor_present: bool = True
    role_conformance: str = ConformanceStatus.MATCH
    must_teach_facets: tuple[str, ...] = ()
    extension_keys: tuple[str, ...] = ()
    contribution_goal_coverage: str = ConformanceStatus.MATCH
    forbidden_reteach_absent: bool = True


@dataclass(frozen=True)
class RepairProposal:
    """A bounded, auditable suggestion; never a text patch or role re-plan."""

    proposal_id: str
    occurrence_id: str
    render_target: str
    immutable_upstream: ImmutableUpstreamDecisions
    failure_reasons: tuple[str, ...]
    actions: tuple[str, ...]
    content_to_keep: tuple[str, ...]
    content_to_remove_or_compress: tuple[str, ...]
    content_to_add: tuple[str, ...]
    forbidden_content: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]
    expected_conformance: RepairExpectation
    execution_safety: str
    manual_review_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RepairProposalReport:
    proposals: tuple[RepairProposal, ...]

    def to_dict(self) -> dict:
        return {"proposals": [item.to_dict() for item in self.proposals]}


def propose_repair(
    *,
    brief: OccurrenceWritingBrief,
    conformance: RenderedConformanceResult,
    rendered: RenderedOccurrence | None,
) -> RepairProposal | None:
    """Produce a deterministic proposal only when conformance is not MATCH.

    ``brief`` is the sole source of identity, role, prerequisites and evidence.
    No semantic planner or LLM is called here, so those upstream facts cannot
    be silently reclassified during repair proposal generation.
    """
    if conformance.overall == ConformanceStatus.MATCH:
        return None
    if conformance.occurrence_id != brief.occurrence_id:
        raise ValueError("Rendered conformance result must belong to the supplied writing brief.")
    if rendered is not None and rendered.occurrence_id != brief.occurrence_id:
        raise ValueError("Rendered occurrence must belong to the supplied writing brief.")

    upstream = ImmutableUpstreamDecisions(
        canonical_knowledge_id=brief.canonical_knowledge_id,
        learning_role=brief.role,
        prerequisite_context=tuple(brief.prerequisite_context),
        semantic_delta_evidence_ids=tuple(brief.semantic_delta_evidence_ids),
    )
    evidence_ids = tuple(dict.fromkeys([*brief.source_chunk_ids, *brief.semantic_delta_evidence_ids]))
    failure_reasons = _failure_reasons(conformance)
    actions: list[str] = []
    additions: list[str] = []
    removals = [item.sentence for item in conformance.forbidden_reteach_violation]
    keep_after_removal = _keepable_sentences(rendered.markdown, removals) if rendered is not None else []
    removal_preserves_role = _removal_preserves_role(brief, keep_after_removal)
    forbidden = list(brief.forbidden_content)
    manual_reasons: list[str] = []

    if not conformance.anchor_present or rendered is None:
        actions.append(RepairAction.MANUAL_REVIEW)
        manual_reasons.append("The code-owned occurrence anchor is absent, so no safe rendered span can be targeted.")
        return _proposal(
            brief=brief,
            rendered=rendered,
            upstream=upstream,
            failure_reasons=failure_reasons,
            actions=actions,
            keep=[],
            removals=[],
            additions=["Restore a code-owned occurrence anchor before considering content repair."],
            forbidden=forbidden,
            evidence_ids=evidence_ids,
            safety=RepairExecutionSafety.HUMAN_REVIEW_REQUIRED,
            manual_reasons=manual_reasons,
        )

    if removals:
        actions.append(RepairAction.REMOVE_RETEACH)
        if brief.role == LearningRole.RECALL:
            actions.append(RepairAction.RESTORE_MINIMAL_RECALL)
            additions.append(_minimal_recall_instruction(brief))
        elif not removal_preserves_role:
            actions.append(RepairAction.REWRITE_TO_ROLE)
            additions.append(f"Rewrite the affected span to the immutable {brief.role} writing contract.")

    missing_facets = [facet for facet, status in conformance.must_teach_coverage.items() if status != ConformanceStatus.MATCH]
    if missing_facets:
        actions.append(RepairAction.ADD_REQUIRED_FACET)
        additions.extend(f"Teach required facet: {facet}." for facet in missing_facets)

    missing_extensions = [key for key, status in conformance.extension_coverage.items() if status != ConformanceStatus.MATCH]
    if missing_extensions:
        actions.append(RepairAction.ADD_EXTENSION)
        additions.extend(f"Add planned extension: {key}." for key in missing_extensions)

    # Facet and extension additions are themselves the planned contribution.
    # ADD_CONTRIBUTION is reserved for a role/task contribution that is absent
    # without a more precise missing planned unit.
    if (
        conformance.contribution_goal_coverage != ConformanceStatus.MATCH
        and not missing_facets
        and not missing_extensions
        and brief.role not in {LearningRole.RECALL, LearningRole.TEACH}
        and not removal_preserves_role
    ):
        actions.append(RepairAction.ADD_CONTRIBUTION)
        additions.append(f"Make the planned contribution explicit: {brief.contribution_goal or 'current task contribution'}.")

    if (
        conformance.role_conformance != ConformanceStatus.MATCH
        and RepairAction.REWRITE_TO_ROLE not in actions
        and RepairAction.RESTORE_MINIMAL_RECALL not in actions
        and not missing_facets
        and not missing_extensions
        and not removal_preserves_role
    ):
        actions.append(RepairAction.REWRITE_TO_ROLE)
        additions.append(f"Rewrite the span to the immutable {brief.role} writing contract.")

    if not actions:
        actions.append(RepairAction.MANUAL_REVIEW)
        manual_reasons.append("The checker reported a non-MATCH result without a deterministic repairable condition.")

    actions = list(dict.fromkeys(actions))
    keep = keep_after_removal
    safety = _execution_safety(actions, keep, additions)
    if safety == RepairExecutionSafety.HUMAN_REVIEW_REQUIRED and not manual_reasons:
        manual_reasons.append("The proposal requires generated or role-sensitive prose; keep it as a reviewed future repair.")
    return _proposal(
        brief=brief,
        rendered=rendered,
        upstream=upstream,
        failure_reasons=failure_reasons,
        actions=actions,
        keep=keep,
        removals=removals,
        additions=additions,
        forbidden=forbidden,
        evidence_ids=evidence_ids,
        safety=safety,
        manual_reasons=manual_reasons,
    )


def build_repair_proposal_report(
    *,
    briefs: list[OccurrenceWritingBrief],
    rendered_occurrences: list[RenderedOccurrence],
    conformance_results: list[RenderedConformanceResult],
) -> RepairProposalReport:
    """Join immutable briefs to rendered spans/results without mutating either."""
    briefs_by_id = {item.occurrence_id: item for item in briefs}
    rendered_by_id = {item.occurrence_id: item for item in rendered_occurrences}
    proposals: list[RepairProposal] = []
    for result in conformance_results:
        brief = briefs_by_id.get(result.occurrence_id)
        if not brief:
            continue
        proposal = propose_repair(
            brief=brief,
            conformance=result,
            rendered=rendered_by_id.get(result.occurrence_id),
        )
        if proposal:
            proposals.append(proposal)
    return RepairProposalReport(tuple(proposals))


def render_repair_proposal_report_markdown(report: RepairProposalReport) -> str:
    lines = ["# Repair Proposal Report", "", "This is read-only: no proposal has been applied to Markdown or DigitalBook output.", ""]
    if not report.proposals:
        lines.append("- No rendered conformance failures required a proposal.")
    for proposal in report.proposals:
        lines.extend([
            f"## {proposal.occurrence_id}",
            f"- immutable role / canonical: {proposal.immutable_upstream.learning_role} / {proposal.immutable_upstream.canonical_knowledge_id}",
            f"- failure reasons: {'; '.join(proposal.failure_reasons)}",
            f"- actions: {', '.join(proposal.actions)}",
            f"- keep: {' | '.join(proposal.content_to_keep) or 'none'}",
            f"- remove or compress: {' | '.join(proposal.content_to_remove_or_compress) or 'none'}",
            f"- must add: {' | '.join(proposal.content_to_add) or 'none'}",
            f"- forbidden: {', '.join(proposal.forbidden_content) or 'none'}",
            f"- evidence/source IDs: {', '.join(proposal.evidence_source_ids) or 'none'}",
            f"- expected conformance: role={proposal.expected_conformance.role_conformance}; forbidden reteach absent={proposal.expected_conformance.forbidden_reteach_absent}",
            f"- future execution safety: {proposal.execution_safety}",
        ])
        if proposal.manual_review_reasons:
            lines.append(f"- review rationale: {'; '.join(proposal.manual_review_reasons)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _proposal(**kwargs) -> RepairProposal:
    brief: OccurrenceWritingBrief = kwargs["brief"]
    return RepairProposal(
        proposal_id=f"repair:{brief.occurrence_id}",
        occurrence_id=brief.occurrence_id,
        render_target=kwargs["rendered"].render_target if kwargs["rendered"] else "markdown",
        immutable_upstream=kwargs["upstream"],
        failure_reasons=tuple(dict.fromkeys(kwargs["failure_reasons"])),
        actions=tuple(kwargs["actions"]),
        content_to_keep=tuple(kwargs["keep"]),
        content_to_remove_or_compress=tuple(dict.fromkeys(kwargs["removals"])),
        content_to_add=tuple(dict.fromkeys(kwargs["additions"])),
        forbidden_content=tuple(dict.fromkeys(kwargs["forbidden"])),
        evidence_source_ids=tuple(kwargs["evidence_ids"]),
        expected_conformance=RepairExpectation(
            must_teach_facets=tuple(brief.must_teach_facets),
            extension_keys=tuple(brief.extension_keys),
        ),
        execution_safety=kwargs["safety"],
        manual_review_reasons=tuple(kwargs["manual_reasons"]),
    )


def _failure_reasons(result: RenderedConformanceResult) -> list[str]:
    reasons: list[str] = []
    if not result.anchor_present:
        reasons.append("MISSING_OCCURRENCE_ANCHOR")
    reasons.extend(f"FORBIDDEN_RETEACH:{item.rule}" for item in result.forbidden_reteach_violation)
    reasons.extend(f"MISSING_REQUIRED_FACET:{facet}" for facet, status in result.must_teach_coverage.items() if status != ConformanceStatus.MATCH)
    reasons.extend(f"MISSING_EXTENSION:{key}" for key, status in result.extension_coverage.items() if status != ConformanceStatus.MATCH)
    if result.contribution_goal_coverage != ConformanceStatus.MATCH:
        reasons.append("MISSING_CONTRIBUTION_GOAL")
    if result.role_conformance != ConformanceStatus.MATCH:
        reasons.append("ROLE_CONFORMANCE:" + result.role_conformance)
    reasons.extend(result.notes)
    return reasons or ["NON_MATCH_CONFORMANCE"]


def _minimal_recall_instruction(brief: OccurrenceWritingBrief) -> str:
    needed = ", ".join(brief.required_facets or brief.already_available_facets) or "the immediately needed prior knowledge"
    return f"Restore only the minimum prerequisite context ({needed}) needed for the current task."


def _keepable_sentences(markdown: str, removals: list[str]) -> list[str]:
    removal_set = set(removals)
    sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s*|\n+", markdown) if item.strip()]
    return [item for item in sentences if item not in removal_set and not item.startswith("Evidence:")]


def _execution_safety(actions: list[str], keep: list[str], additions: list[str]) -> str:
    # Exact deletion of checker-cited sentences can be an eventual automation
    # candidate only when it leaves an explicitly retained span and asks for
    # no generated content. Everything else remains review-bound in Phase 3A.
    if actions == [RepairAction.REMOVE_RETEACH] and keep and not additions:
        return RepairExecutionSafety.AUTO_CANDIDATE
    return RepairExecutionSafety.HUMAN_REVIEW_REQUIRED


def _removal_preserves_role(brief: OccurrenceWritingBrief, retained_sentences: list[str]) -> bool:
    """Conservative no-rewrite check after an exact prohibited-sentence deletion."""
    retained = " ".join(retained_sentences).lower()
    if brief.role == LearningRole.APPLY:
        return any(token in retained for token in ("apply", "use", "task", "使用", "应用", "任务"))
    if brief.role == LearningRole.INTRO:
        return any(token in retained for token in ("initial", "intuition", "初识", "方向"))
    return False
