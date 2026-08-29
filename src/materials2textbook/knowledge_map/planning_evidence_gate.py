"""Writer-pre evidence gate for immutable semantic plans.

The semantic planner may propose a learning increment, but it may not turn an
unproven claim into writer input.  This module evaluates only the evidence IDs
already bound to an accepted occurrence; it never widens that evidence set.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
import re
from types import SimpleNamespace

from materials2textbook.knowledge_map.semantic_evaluation import SemanticPlanningEvaluation
from materials2textbook.knowledge_map.writing_briefs import (
    OccurrenceWritingBrief,
    RejectedPlanOccurrence,
    WritingBriefCoverage,
)
from materials2textbook.schemas import EvidenceChunk


class EvidenceSupportStatus:
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


class ClaimSupportType:
    """Where a planning claim is allowed to obtain its proof.

    This intentionally distinguishes source assertions from statements made by
    the instructional trajectory.  A task-use contribution is not a claim that
    an evidence chunk literally describes the task-use sentence.
    """

    SOURCE_FACT = "SOURCE_FACT"
    TRAJECTORY_FACT = "TRAJECTORY_FACT"
    STRUCTURAL_FACT = "STRUCTURAL_FACT"
    MIXED = "MIXED"


class ManualReviewKind:
    SYSTEM_RESOLVABLE = "SYSTEM_RESOLVABLE"
    DOMAIN_EXPERT_REQUIRED = "DOMAIN_EXPERT_REQUIRED"


@dataclass(frozen=True)
class EvidenceSupportFinding:
    claim_type: str
    requested_values: tuple[str, ...]
    supported_values: tuple[str, ...]
    unsupported_values: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    rationale: str
    support_type: str = ClaimSupportType.SOURCE_FACT
    support_references: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if not self.requested_values:
            return EvidenceSupportStatus.SUPPORTED
        if not self.supported_values:
            return EvidenceSupportStatus.UNSUPPORTED
        if self.unsupported_values:
            return EvidenceSupportStatus.PARTIAL
        return EvidenceSupportStatus.SUPPORTED


@dataclass(frozen=True)
class PlanningEvidenceDecision:
    occurrence_id: str
    status: str
    writer_eligible: bool
    allowed_evidence_ids: tuple[str, ...]
    findings: tuple[EvidenceSupportFinding, ...]
    rejection_reason: str = ""
    binding_audit: "EvidenceBindingAudit | None" = None
    contraction: "PlanContraction | None" = None


@dataclass(frozen=True)
class EvidenceBindingAudit:
    occurrence_id: str
    previous_evidence_ids: tuple[str, ...]
    retrieved_candidate_ids: tuple[str, ...]
    accepted_evidence_ids: tuple[str, ...]
    retrieval_rationale: str
    support_score: float


@dataclass(frozen=True)
class PlanContraction:
    """An evidence-bounded copy of a plan; upstream semantic objects stay immutable."""

    occurrence_id: str
    status: str  # EVIDENCE_BOUNDED_AUTO_CONTRACTION | DROP_OCCURRENCE_GOAL | MANUAL_REVIEW
    before: dict
    after: dict
    removed_fields: tuple[str, ...]
    rationale: str
    review_kind: str = ManualReviewKind.SYSTEM_RESOLVABLE


@dataclass(frozen=True)
class PlanningEvidenceGateReport:
    decisions: tuple[PlanningEvidenceDecision, ...]

    def by_occurrence_id(self) -> dict[str, PlanningEvidenceDecision]:
        return {item.occurrence_id: item for item in self.decisions}

    @property
    def unsupported_count(self) -> int:
        return sum(item.status == EvidenceSupportStatus.UNSUPPORTED for item in self.decisions)


@dataclass(frozen=True)
class EvidenceCoverageResolution:
    """Read-only semantic-plan projection after evidence binding/contraction."""

    report: PlanningEvidenceGateReport
    contracted_payload: dict
    contractions: tuple[PlanContraction, ...]
    dropped_occurrence_ids: tuple[str, ...] = ()


def evaluate_planning_evidence(
    *, evaluation: SemanticPlanningEvaluation, chunks: list[EvidenceChunk],
) -> PlanningEvidenceGateReport:
    """Check every accepted semantic occurrence before it can become writer input.

    The gate uses lexical, deterministic evidence signals.  It is deliberately
    conservative about *missing* support (``PARTIAL`` remains auditable), and
    fail-closed only when a claimed semantic fact has no support at all.
    """
    evidence_by_id = {item.chunk_id: item for item in chunks}
    deltas = {item.occurrence_id: item for item in evaluation.semantic_deltas}
    edges = {item.edge_id: item for item in evaluation.knowledge_map.prerequisites}
    decisions: list[PlanningEvidenceDecision] = []
    for occurrence in evaluation.knowledge_map.planned_occurrences:
        if not occurrence.trusted_for_state:
            continue
        delta = deltas.get(occurrence.occurrence_id)
        bound_ids = tuple(dict.fromkeys([
            *occurrence.source_chunk_ids,
            *(delta.evidence_chunk_ids if delta else []),
            *occurrence.contribution_evidence_chunk_ids,
        ]))
        bound = [evidence_by_id[item] for item in bound_ids if item in evidence_by_id]
        allowed_ids = tuple(item.chunk_id for item in bound)
        text = _evidence_text(bound)
        findings = (
            _facet_finding(occurrence.intended_grants, text, allowed_ids),
            _extension_finding(occurrence.intended_extension_keys, text, allowed_ids),
            _contribution_finding(
                occurrence.intended_contribution, occurrence=occurrence, delta=delta,
                text=text, ids=allowed_ids, prior_occurrences=_prior_occurrence_ids(evaluation, occurrence),
            ),
            _cross_prerequisite_finding(occurrence, delta, edges, allowed_ids),
        )
        statuses = [item.status for item in findings]
        status = (
            EvidenceSupportStatus.UNSUPPORTED
            if EvidenceSupportStatus.UNSUPPORTED in statuses
            else EvidenceSupportStatus.PARTIAL
            if EvidenceSupportStatus.PARTIAL in statuses
            else EvidenceSupportStatus.SUPPORTED
        )
        decisions.append(PlanningEvidenceDecision(
            occurrence_id=occurrence.occurrence_id,
            status=status,
            writer_eligible=status != EvidenceSupportStatus.UNSUPPORTED,
            allowed_evidence_ids=allowed_ids,
            findings=findings,
            rejection_reason="unsupported_planning_claim" if status == EvidenceSupportStatus.UNSUPPORTED else "",
        ))
    return PlanningEvidenceGateReport(tuple(decisions))


def evaluate_planning_evidence_from_payload(
    *, payload: dict, chunks: list[EvidenceChunk],
) -> PlanningEvidenceGateReport:
    """Payload adapter used at the persisted semantic-plan → writer boundary.

    It mirrors the typed gate without re-running semantic planning, so a saved
    accepted plan gets the same fail-closed evidence check immediately before
    the writer.
    """
    knowledge_map = payload.get("knowledge_map") if isinstance(payload, dict) else None
    if not isinstance(knowledge_map, dict):
        raise ValueError("Expected a semantic planning evaluation payload.")
    evidence_by_id = {item.chunk_id: item for item in chunks}
    deltas = {item.get("occurrence_id"): item for item in payload.get("semantic_deltas", []) if isinstance(item, dict)}
    edges = {
        item.get("edge_id"): SimpleNamespace(**item)
        for item in knowledge_map.get("prerequisites", [])
        if isinstance(item, dict) and item.get("edge_id")
    }
    decisions: list[PlanningEvidenceDecision] = []
    for raw in knowledge_map.get("planned_occurrences", []):
        if not isinstance(raw, dict) or not raw.get("trusted_for_state"):
            continue
        occurrence = SimpleNamespace(**raw)
        occurrence.required_prerequisites = [SimpleNamespace(**item) for item in raw.get("required_prerequisites", []) if isinstance(item, dict)]
        delta_raw = deltas.get(raw.get("occurrence_id"))
        delta = SimpleNamespace(**delta_raw) if isinstance(delta_raw, dict) else None
        bound_ids = tuple(dict.fromkeys([
            *list(raw.get("source_chunk_ids") or []),
            *(list(delta_raw.get("evidence_chunk_ids") or delta_raw.get("evidence_ids") or []) if isinstance(delta_raw, dict) else []),
            *list(raw.get("contribution_evidence_chunk_ids") or []),
        ]))
        binding = _bind_knowledge_evidence(raw=raw, knowledge_map=knowledge_map, deltas=deltas, chunks=chunks, previous_ids=bound_ids)
        bound_ids = binding.accepted_evidence_ids
        bound = [evidence_by_id[item] for item in bound_ids if item in evidence_by_id]
        allowed_ids = tuple(item.chunk_id for item in bound)
        findings = (
            _facet_finding(list(raw.get("intended_grants") or []), _evidence_text(bound), allowed_ids),
            _extension_finding(list(raw.get("intended_extension_keys") or []), _evidence_text(bound), allowed_ids),
            _contribution_finding(
                str(raw.get("intended_contribution") or ""), occurrence=occurrence, delta=delta,
                text=_evidence_text(bound), ids=allowed_ids,
                prior_occurrences=_prior_occurrence_ids_from_payload(knowledge_map, raw),
            ),
            _cross_prerequisite_finding(occurrence, delta, edges, allowed_ids),
        )
        statuses = [item.status for item in findings]
        status = EvidenceSupportStatus.UNSUPPORTED if EvidenceSupportStatus.UNSUPPORTED in statuses else (EvidenceSupportStatus.PARTIAL if EvidenceSupportStatus.PARTIAL in statuses else EvidenceSupportStatus.SUPPORTED)
        decisions.append(PlanningEvidenceDecision(
            occurrence_id=str(raw["occurrence_id"]), status=status, writer_eligible=status != EvidenceSupportStatus.UNSUPPORTED,
            allowed_evidence_ids=allowed_ids, findings=findings,
            rejection_reason="unsupported_planning_claim" if status == EvidenceSupportStatus.UNSUPPORTED else "",
            binding_audit=binding,
        ))
    return PlanningEvidenceGateReport(tuple(decisions))


def resolve_evidence_coverage_from_payload(*, payload: dict, chunks: list[EvidenceChunk]) -> EvidenceCoverageResolution:
    """Bind restricted evidence and automatically apply unique safe contractions.

    The returned payload is a copy.  It does not mutate the original semantic
    plan or BookPlan. A contraction is automatic only when deterministic rules
    leave one conservative evidence-bounded result; preference between multiple
    pedagogically valid results remains DOMAIN_EXPERT_REQUIRED.
    """
    initial = evaluate_planning_evidence_from_payload(payload=payload, chunks=chunks)
    revised = deepcopy(payload)
    by_id = {item.get("occurrence_id"): item for item in revised.get("knowledge_map", {}).get("planned_occurrences", []) if isinstance(item, dict)}
    deltas = {item.get("occurrence_id"): item for item in revised.get("semantic_deltas", []) if isinstance(item, dict)}
    chunks_by_id = {item.chunk_id: item for item in chunks}
    contractions: list[PlanContraction] = []
    dropped: list[str] = []
    for decision in initial.decisions:
        if decision.status != EvidenceSupportStatus.UNSUPPORTED:
            continue
        raw = by_id.get(decision.occurrence_id)
        if raw is None:
            continue
        unsupported = {item.claim_type: set(item.unsupported_values) for item in decision.findings}
        before = {
            "role": raw.get("role"), "new_facets": list(raw.get("intended_grants") or []),
            "new_extension_keys": list(raw.get("intended_extension_keys") or []),
            "intended_contribution": raw.get("intended_contribution") or "",
        }
        raw["intended_grants"] = [item for item in raw.get("intended_grants") or [] if item not in unsupported.get("new_facets", set())]
        raw["intended_extension_keys"] = [item for item in raw.get("intended_extension_keys") or [] if item not in unsupported.get("new_extension_keys", set())]
        if raw.get("intended_contribution") in unsupported.get("intended_contribution", set()):
            raw["intended_contribution"] = ""
        bound = [chunks_by_id[item] for item in decision.allowed_evidence_ids if item in chunks_by_id]
        supported_facets = _supported_facets(_evidence_text(bound), decision.allowed_evidence_ids)
        prior = _prior_occurrence_ids_from_payload(revised["knowledge_map"], raw)
        role, grants, status, rationale, review_kind = _derive_contracted_role(
            original_role=str(before["role"] or ""), remaining_grants=list(raw["intended_grants"]),
            remaining_extensions=list(raw["intended_extension_keys"]), supported_facets=supported_facets,
            prior_occurrences=prior, prior_facets=_prior_available_facets_from_payload(revised["knowledge_map"], raw),
            uses_prior_knowledge=bool(raw.get("uses_prior_knowledge")),
            procedural_retype_allowed=_procedural_retype_allowed(raw),
        )
        raw["role"] = role
        raw["intended_grants"] = grants
        if status in {"DROP_OCCURRENCE_GOAL", "MANUAL_REVIEW"}:
            raw["trusted_for_state"] = False
            raw["evidence_resolution_status"] = status
            dropped.append(decision.occurrence_id)
        else:
            raw["trusted_for_state"] = True
            raw["evidence_resolution_status"] = status
        delta = deltas.get(decision.occurrence_id)
        if delta is not None:
            # This is the contracted payload's SemanticDelta, not a mutation
            # of the immutable upstream planning artifact.
            delta["new_facets"] = list(grants)
            delta["new_extension_keys"] = list(raw["intended_extension_keys"])
            delta["contribution_summary"] = str(raw.get("intended_contribution") or "")
            delta["evidence_bounded_contraction"] = True
        removed = tuple(key for key, old, new in (
            ("new_facets", before["new_facets"], raw["intended_grants"]),
            ("new_extension_keys", before["new_extension_keys"], raw["intended_extension_keys"]),
            ("intended_contribution", before["intended_contribution"], raw["intended_contribution"]),
        ) if old != new)
        contractions.append(PlanContraction(decision.occurrence_id, status, before, {
            "role": raw.get("role"), "new_facets": list(raw.get("intended_grants") or []),
            "new_extension_keys": list(raw.get("intended_extension_keys") or []),
            "intended_contribution": raw.get("intended_contribution") or "",
        }, removed, rationale, review_kind))
    final = evaluate_planning_evidence_from_payload(payload=revised, chunks=chunks)
    return EvidenceCoverageResolution(final, revised, tuple(contractions), tuple(dropped))


def apply_planning_evidence_gate(
    *, coverage: WritingBriefCoverage, report: PlanningEvidenceGateReport,
) -> WritingBriefCoverage:
    """Return writer input with unsupported briefs made explicit, never fallback.

    A rejected semantic plan cannot be silently rendered through the legacy
    fallback path.  The caller must stop the book run for manual review or
    explicitly remove that occurrence from scope.
    """
    decisions = report.by_occurrence_id()
    accepted: list[OccurrenceWritingBrief] = []
    rejected = list(coverage.rejected_plan_occurrences)
    for brief in coverage.briefs:
        decision = decisions.get(brief.occurrence_id)
        if decision is None or decision.writer_eligible:
            accepted.append(brief)
            continue
        rejected.append(RejectedPlanOccurrence(
            occurrence_id=brief.occurrence_id,
            source_knowledge_point_id=brief.source_knowledge_point_id,
            canonical_knowledge_id=brief.canonical_knowledge_id,
            chapter_id=brief.chapter_id,
            section_id=brief.section_id,
            task_ordinal=brief.task_ordinal,
            occurrence_ordinal=brief.occurrence_ordinal,
            reason=decision.rejection_reason,
            evidence_status=decision.status,
            allowed_evidence_ids=decision.allowed_evidence_ids,
        ))
    return replace(coverage, briefs=accepted, rejected_plan_occurrences=rejected)


def render_planning_evidence_gate_markdown(report: PlanningEvidenceGateReport) -> str:
    lines = ["# Planning Evidence Gate", "", "Unsupported plans are excluded from normal writer input and require manual review.", ""]
    for decision in report.decisions:
        lines.extend([
            f"## {decision.occurrence_id}",
            f"- status: {decision.status}",
            f"- writer eligible: {decision.writer_eligible}",
            f"- allowed evidence: {', '.join(decision.allowed_evidence_ids) or 'none'}",
        ])
        if decision.binding_audit:
            lines.extend([
                f"- previous evidence: {', '.join(decision.binding_audit.previous_evidence_ids) or 'none'}",
                f"- retrieved candidates: {', '.join(decision.binding_audit.retrieved_candidate_ids) or 'none'}",
                f"- binding rationale: {decision.binding_audit.retrieval_rationale}",
                f"- retrieval support score: {decision.binding_audit.support_score:.3f}",
            ])
        for finding in decision.findings:
            lines.append(
                f"- {finding.claim_type} [{finding.support_type}]: {finding.status}; supported={', '.join(finding.supported_values) or 'none'}; "
                f"unsupported={', '.join(finding.unsupported_values) or 'none'}"
            )
            if finding.support_references:
                lines.append(f"  - support references: {', '.join(finding.support_references)}")
        if decision.contraction:
            lines.append(f"- contraction: {decision.contraction.status}; removed={', '.join(decision.contraction.removed_fields) or 'none'}")
        if decision.rejection_reason:
            lines.append(f"- rejection reason: {decision.rejection_reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _evidence_text(chunks: list[EvidenceChunk]) -> str:
    return " ".join(" ".join([item.title, item.summary, item.content]) for item in chunks).lower()


def _facet_finding(values: list[str], text: str, ids: tuple[str, ...]) -> EvidenceSupportFinding:
    signals = {
        "ORIENTED": ("overview", "introduction", "认识", "概述", "入门"),
        "EXPLAIN": ("explain", "principle", "reason", "cause", "because", "definition", "method", "原理", "解释", "原因", "影响", "作用", "方法"),
        "PERFORM": ("step", "operate", "perform", "procedure", "adjust", "操作", "步骤", "执行", "调整", "方法"),
        "ANALYZE": ("analy", "diagnos", "compare", "judg", "condition", "分析", "判断", "异常", "原因"),
    }
    supported = [item for item in values if any(token in text for token in signals.get(item, ()))]
    # Orientation can be grounded by a named evidence chunk even where the
    # source naturally lacks a literal “overview” token.
    if "ORIENTED" in values and ids and "ORIENTED" not in supported:
        supported.append("ORIENTED")
    return _finding("new_facets", values, supported, ids, "Facet signals must occur in bound evidence.")


def _extension_finding(values: list[str], text: str, ids: tuple[str, ...]) -> EvidenceSupportFinding:
    supported = [item for item in values if _extension_supported(item, text)]
    return _finding("new_extension_keys", values, supported, ids, "Every extension key must have literal bound-evidence support.")


def _contribution_finding(value: str, *, occurrence, delta, text: str, ids: tuple[str, ...], prior_occurrences: tuple[str, ...]) -> EvidenceSupportFinding:
    if not value.strip():
        return _finding("intended_contribution", [], [], ids, "No contribution claim was planned.")
    role = str(getattr(occurrence, "role", ""))
    grants = list(getattr(occurrence, "intended_grants", []) or [])
    extensions = list(getattr(occurrence, "intended_extension_keys", []) or [])
    # Contribution semantics are not all source claims.  A recall/apply is
    # supported by the preceding verified trajectory; an INTRO is a fact about
    # the fixed outline position.  TEACH/EXTEND combine source facts with that
    # deterministic position/trajectory evidence.
    if role in {"APPLY", "RECALL"} or (role == "TEACH" and not grants and not extensions):
        refs = prior_occurrences
        supported = [value] if refs else []
        return EvidenceSupportFinding(
            "intended_contribution", (value,), tuple(supported), () if supported else (value,), ids,
            "Contribution is a trajectory fact: prior verified instruction is used or restored here.",
            ClaimSupportType.TRAJECTORY_FACT, refs,
        )
    if role == "INTRO":
        return EvidenceSupportFinding(
            "intended_contribution", (value,), (value,), (), ids,
            "Contribution is a structural fact: this is the first planned occurrence in the fixed BookPlan.",
            ClaimSupportType.STRUCTURAL_FACT, (str(getattr(occurrence, "occurrence_id", "")),),
        )
    facet_supported = bool(grants) and all(_facet_supported(item, text, ids) for item in grants)
    extension_supported = all(_extension_supported(item, text) for item in extensions)
    source_supported = bool(ids) and facet_supported and extension_supported
    supported = [value] if source_supported else []
    return EvidenceSupportFinding(
        "intended_contribution", (value,), tuple(supported), () if supported else (value,), ids,
        "Contribution is mixed: new instructional facts require bound source evidence and the role is fixed by the trajectory.",
        ClaimSupportType.MIXED, tuple([*ids, str(getattr(occurrence, "occurrence_id", ""))]),
    )


def _cross_prerequisite_finding(occurrence, delta, edges, ids: tuple[str, ...]) -> EvidenceSupportFinding:
    values = [item.knowledge_id for item in occurrence.required_prerequisites]
    if not values:
        return _finding("cross_prerequisite_rationale", [], [], ids, "No cross-knowledge prerequisite use was planned.")
    supported: list[str] = []
    for use in occurrence.required_prerequisites:
        edge = edges.get(use.edge_id) if use.edge_id else None
        if edge and str(getattr(edge, "rationale", "")).strip() and set(getattr(edge, "evidence_chunk_ids", [])).intersection(ids):
            supported.append(use.knowledge_id)
        elif delta and str(getattr(delta, "rationale", "")).strip() and set(getattr(delta, "evidence_chunk_ids", getattr(delta, "evidence_ids", []))).intersection(ids):
            # Semantic prerequisite uses may be new edges. They remain only
            # partially auditable without a persistent prerequisite edge.
            supported.append(use.knowledge_id)
    finding = _finding("cross_prerequisite_rationale", values, supported, ids, "Requires an edge rationale/evidence or the bound semantic rationale.")
    if supported and any(not (edges.get(item.edge_id) if item.edge_id else None) for item in occurrence.required_prerequisites):
        return EvidenceSupportFinding(
            finding.claim_type, finding.requested_values, finding.supported_values,
            finding.unsupported_values, finding.evidence_ids,
            "Semantic rationale is bound, but at least one prerequisite edge is not persisted yet.",
        )
    return finding


def _finding(claim_type: str, values: list[str], supported: list[str], ids: tuple[str, ...], rationale: str) -> EvidenceSupportFinding:
    return EvidenceSupportFinding(
        claim_type=claim_type,
        requested_values=tuple(values),
        supported_values=tuple(dict.fromkeys(supported)),
        unsupported_values=tuple(item for item in values if item not in supported),
        evidence_ids=ids,
        rationale=rationale,
    )


def _facet_supported(value: str, text: str, ids: tuple[str, ...]) -> bool:
    return value in _facet_finding([value], text, ids).supported_values


def _supported_facets(text: str, ids: tuple[str, ...]) -> list[str]:
    return [facet for facet in ("ORIENTED", "EXPLAIN", "PERFORM", "ANALYZE") if _facet_supported(facet, text, ids)]


def _derive_contracted_role(
    *, original_role: str, remaining_grants: list[str], remaining_extensions: list[str],
    supported_facets: list[str], prior_occurrences: tuple[str, ...], prior_facets: list[str], uses_prior_knowledge: bool,
    procedural_retype_allowed: bool,
) -> tuple[str, list[str], str, str, str]:
    """Derive the one conservative role left after removing unsupported claims."""
    if remaining_extensions:
        return "EXTEND", remaining_grants, "EVIDENCE_BOUNDED_AUTO_CONTRACTION", "Only source-supported extension increments remain.", ManualReviewKind.SYSTEM_RESOLVABLE
    if remaining_grants:
        return original_role, remaining_grants, "EVIDENCE_BOUNDED_AUTO_CONTRACTION", "Unsupported claims were removed; the remaining teaching increment is source-supported.", ManualReviewKind.SYSTEM_RESOLVABLE
    # A source may demonstrate a procedural capability even where the planner
    # overclaimed an explanation. Preserve that fact as TEACH/PERFORM.
    alternatives = [item for item in supported_facets if item not in {"ORIENTED", "EXPLAIN"}]
    if original_role == "TEACH" and len(alternatives) > 1:
        return original_role, [], "MANUAL_REVIEW", "Multiple evidence-supported replacement facets require a domain teaching preference.", ManualReviewKind.DOMAIN_EXPERT_REQUIRED
    if original_role == "TEACH" and procedural_retype_allowed and alternatives == ["PERFORM"]:
        return "TEACH", ["PERFORM"], "EVIDENCE_BOUNDED_AUTO_CONTRACTION", "The source supports procedural performance, not the removed explanatory claim.", ManualReviewKind.SYSTEM_RESOLVABLE
    # First occurrence with authorised evidence may establish orientation only.
    if original_role == "TEACH" and not prior_occurrences and supported_facets:
        return "INTRO", ["ORIENTED"], "EVIDENCE_BOUNDED_AUTO_CONTRACTION", "Only a first-contact orientation remains evidence-bounded.", ManualReviewKind.SYSTEM_RESOLVABLE
    # A later task that explicitly uses already taught knowledge can become
    # APPLY without claiming any new facet or extension.
    if prior_occurrences and uses_prior_knowledge and any(item in prior_facets for item in ("EXPLAIN", "PERFORM", "ANALYZE")):
        return "APPLY", [], "EVIDENCE_BOUNDED_AUTO_CONTRACTION", "No new source claim remains; the fixed task uses prior verified instruction.", ManualReviewKind.SYSTEM_RESOLVABLE
    return original_role, [], "DROP_OCCURRENCE_GOAL", "No evidence-bounded instructional increment remains after contraction.", ManualReviewKind.SYSTEM_RESOLVABLE


def _prior_available_facets_from_payload(knowledge_map: dict, raw: dict) -> list[str]:
    prior_ids = set(_prior_occurrence_ids_from_payload(knowledge_map, raw))
    facets: list[str] = []
    for item in knowledge_map.get("planned_occurrences", []):
        if isinstance(item, dict) and item.get("occurrence_id") in prior_ids:
            facets.extend(item.get("intended_grants") or [])
    return list(dict.fromkeys(facets))


def _procedural_retype_allowed(raw: dict) -> bool:
    """Avoid inventing an operation objective from generic applicability text."""
    label = " ".join(str(raw.get(item) or "") for item in ("context_title", "source_title", "title")) .lower()
    return any(token in label for token in ("parameter", "setting", "operation", "procedure", "参数", "设置", "操作"))


def _prior_occurrence_ids(evaluation: SemanticPlanningEvaluation, occurrence) -> tuple[str, ...]:
    position = getattr(occurrence, "position", None)
    if position is None:
        return ()
    earlier = [
        item.occurrence_id for item in evaluation.knowledge_map.planned_occurrences
        if item.knowledge_id == occurrence.knowledge_id and item.occurrence_id != occurrence.occurrence_id
        and item.position < position and item.trusted_for_state
    ]
    return tuple(earlier)


def _prior_occurrence_ids_from_payload(knowledge_map: dict, raw: dict) -> tuple[str, ...]:
    position = raw.get("position") or {}
    current = (int(position.get("chapter_ordinal") or 0), int(position.get("task_ordinal") or 0), int(position.get("occurrence_ordinal") or 0))
    result = []
    for item in knowledge_map.get("planned_occurrences", []):
        if not isinstance(item, dict) or item.get("knowledge_id") != raw.get("knowledge_id") or item.get("occurrence_id") == raw.get("occurrence_id") or not item.get("trusted_for_state"):
            continue
        candidate = item.get("position") or {}
        candidate_key = (int(candidate.get("chapter_ordinal") or 0), int(candidate.get("task_ordinal") or 0), int(candidate.get("occurrence_ordinal") or 0))
        if candidate_key < current:
            result.append(str(item.get("occurrence_id")))
    return tuple(result)


def _bind_knowledge_evidence(*, raw: dict, knowledge_map: dict, deltas: dict, chunks: list[EvidenceChunk], previous_ids: tuple[str, ...]) -> EvidenceBindingAudit:
    """Retrieve only from the occurrence's canonical/source evidence envelope.

    This is intentionally *not* a whole-book search.  It can inherit evidence
    through canonical mappings and decomposition, but nothing outside that
    lineage or the current task's source evidence is eligible.
    """
    chunk_by_id = {item.chunk_id: item for item in chunks}
    canonical_id = raw.get("knowledge_id")
    source_id = raw.get("source_knowledge_point_id")
    allowed: set[str] = set(previous_ids)
    source_titles: list[str] = [str(raw.get("context_title") or "")]
    for item in knowledge_map.get("knowledge_points", []):
        if isinstance(item, dict) and item.get("knowledge_id") == canonical_id:
            allowed.update(item.get("source_chunk_ids") or [])
            source_titles.extend([str(item.get("title") or ""), *[str(alias) for alias in item.get("aliases") or []]])
    for item in knowledge_map.get("source_knowledge_points", []):
        if isinstance(item, dict) and item.get("source_knowledge_point_id") == source_id:
            allowed.update(item.get("source_chunk_ids") or [])
            source_titles.append(str(item.get("title") or ""))
    for mapping in knowledge_map.get("mappings", []):
        if not isinstance(mapping, dict):
            continue
        mapped_ids = set(mapping.get("canonical_knowledge_ids") or [])
        if mapping.get("source_knowledge_point_id") == source_id or canonical_id in mapped_ids:
            allowed.update(mapping.get("evidence_chunk_ids") or [])
            mapped_source_id = mapping.get("source_knowledge_point_id")
            for source in knowledge_map.get("source_knowledge_points", []):
                if isinstance(source, dict) and source.get("source_knowledge_point_id") == mapped_source_id:
                    allowed.update(source.get("source_chunk_ids") or [])
                    source_titles.append(str(source.get("title") or ""))
    # A chunk is only a slice of an authorised source asset. Other approved
    # slices of that same asset remain within the canonical evidence envelope.
    asset_ids = {chunk_by_id[item].asset_id for item in allowed if item in chunk_by_id}
    allowed.update(item.chunk_id for item in chunks if item.asset_id in asset_ids)
    candidates = [chunk_by_id[item] for item in allowed if item in chunk_by_id]
    query = " ".join(source_titles)
    scored = sorted(
        ((_knowledge_evidence_score(query, chunk), chunk.chunk_id) for chunk in candidates),
        reverse=True,
    )
    # Existing bindings remain; retrieval adds only meaningfully related items
    # from the restricted envelope.  A score is recorded for audit, never used
    # to lower a support threshold.
    retrieved = tuple(item for score, item in scored if score >= 0.2 and item not in previous_ids)
    accepted = tuple(dict.fromkeys([*previous_ids, *retrieved]))
    return EvidenceBindingAudit(
        occurrence_id=str(raw.get("occurrence_id") or ""), previous_evidence_ids=previous_ids,
        retrieved_candidate_ids=retrieved, accepted_evidence_ids=accepted,
        retrieval_rationale="canonical source evidence + mapping/decomposition inheritance + current occurrence source evidence (including authorised asset slices)",
        support_score=scored[0][0] if scored else 0.0,
    )


def _knowledge_evidence_score(query: str, chunk: EvidenceChunk) -> float:
    query_terms = set(_claim_terms(query))
    corpus_terms = set(_claim_terms(" ".join([chunk.title, chunk.summary, chunk.content])))
    if not query_terms:
        return 0.0
    overlap = len(query_terms & corpus_terms) / len(query_terms)
    title = " ".join([chunk.title, chunk.summary]).lower()
    # Keep an exact canonical/source title match clearly auditable.
    if any(term in title for term in query_terms):
        overlap = max(overlap, 0.5)
    return round(overlap, 3)


def _extension_supported(key: str, text: str) -> bool:
    terms = [item for item in re.split(r"[:_\-]+", key.lower()) if item and item not in {"constraint", "variant", "condition"}]
    aliases = {
        "current": ("current", "电流"),
        "limit": ("limit", "限制", "限流"),
        "thin": ("thin", "薄"),
        "plate": ("plate", "板"),
        "abnormal": ("abnormal", "异常"),
    }
    return bool(terms) and all(any(alias in text for alias in aliases.get(term, (term,))) for term in terms)


def _claim_terms(value: str) -> list[str]:
    english = [item.lower() for item in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", value)]
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    generic = {"teach", "teaching", "current", "task", "knowledge", "apply", "使用", "当前", "任务", "讲授", "知识"}
    return [item for item in [*english, *chinese] if item not in generic]
