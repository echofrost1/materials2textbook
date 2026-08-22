"""Sequential production execution for verified instructional availability."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from materials2textbook.knowledge_map.availability import advance_verified_instructional_availability
from materials2textbook.knowledge_map.models import (
    InstructionalAvailabilityState,
    KnowledgePoint,
    OccurrenceExecutionResult,
    PlannedOccurrence,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    check_rendered_conformance,
    extract_rendered_occurrences,
)
from materials2textbook.knowledge_map.rendered_evidence_verification import (
    SupportStatus,
    verify_rendered_evidence,
)
from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (
    CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
    ClaimStatus,
    audit_rendered_claims,
)
from materials2textbook.knowledge_map.writing_briefs import (
    OccurrenceWritingBrief,
    WritingBriefCoverage,
    build_verified_occurrence_writing_brief,
    decide_zero_render_occurrence,
)
from materials2textbook.schemas import DigitalBook, EvidenceChunk


@dataclass
class SemanticExecutionResult:
    coverage: WritingBriefCoverage
    markdown_occurrences: list[dict] = field(default_factory=list)
    section_assemblies: list[dict] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    blocked_occurrences: list[dict] = field(default_factory=list)
    verified_state: InstructionalAvailabilityState = field(default_factory=InstructionalAvailabilityState)
    semantic_evidence_call_count: int = 0
    semantic_evidence_model: str = ""

    def to_dict(self) -> dict:
        return {
            "coverage": {
                "briefs": [asdict(item) for item in self.coverage.briefs],
                "fallback_occurrences": [asdict(item) for item in self.coverage.fallback_occurrences],
                "rejected_plan_occurrences": [asdict(item) for item in self.coverage.rejected_plan_occurrences],
                "dropped_occurrence_goals": [asdict(item) for item in self.coverage.dropped_occurrence_goals],
                "zero_render_occurrences": [asdict(item) for item in self.coverage.zero_render_occurrences],
                "execution_blocked_occurrences": list(self.coverage.execution_blocked_occurrences),
            },
            "markdown_occurrences": self.markdown_occurrences,
            "section_assemblies": self.section_assemblies,
            "transitions": self.transitions,
            "blocked_occurrences": self.blocked_occurrences,
            "verified_state": asdict(self.verified_state),
            "semantic_evidence": {
                "call_count": self.semantic_evidence_call_count,
                "model": self.semantic_evidence_model,
            },
        }


def execute_verified_occurrences(
    *,
    occurrences: list[PlannedOccurrence],
    deltas: list[SemanticDelta],
    sources: dict[str, SourceKnowledgePoint],
    points: dict[str, KnowledgePoint],
    chunks: list[EvidenceChunk],
    render_occurrence: Callable[[OccurrenceWritingBrief], str],
    excluded_occurrence_ids: set[str] | None = None,
    semantic_entailment_judge: Any | None = None,
) -> SemanticExecutionResult:
    """Compile, render, verify, and grant each occurrence in book order.

    Planned availability is intentionally absent from this function.  Later
    occurrences see only grants produced by prior non-empty rendered spans
    that passed both local conformance and local evidence verification.
    """
    from materials2textbook.knowledge_map.semantic_evaluation import compile_occurrence_for_verified_availability

    delta_by_id = {item.occurrence_id: item for item in deltas}
    ordered = sorted(occurrences, key=lambda item: item.position)
    trajectories: dict[str, list[PlannedOccurrence]] = {}
    for item in ordered:
        trajectories.setdefault(item.knowledge_id, []).append(item)
    first_position = {key: items[0].position for key, items in trajectories.items() if items}
    state = InstructionalAvailabilityState()
    result = SemanticExecutionResult(coverage=WritingBriefCoverage(), verified_state=state)
    evidence_by_id = {item.chunk_id: item for item in chunks}

    excluded_occurrence_ids = set(excluded_occurrence_ids or ())
    for seed in ordered:
        if seed.occurrence_id in excluded_occurrence_ids:
            blocked = {
                "occurrence_id": seed.occurrence_id,
                "issue_code": "SKIPPED_BY_SEMANTIC_EVIDENCE_GATE",
                "details": "The occurrence was rejected or dropped by the planning evidence gate and was not sent to the writer.",
                "canonical_knowledge_id": seed.knowledge_id,
                "outline_node_id": seed.section_id,
                "rendered": False,
                "materialized": False,
            }
            result.blocked_occurrences.append(blocked)
            result.coverage.execution_blocked_occurrences.append(blocked)
            result.transitions.append({
                "occurrence_id": seed.occurrence_id,
                "render_decision": "NOT_EXECUTED",
                "grant_applied": False,
                "blocked_reasons": ["SKIPPED_BY_SEMANTIC_EVIDENCE_GATE"],
                "before": _state_dict(state),
                "after": _state_dict(state),
            })
            state.position = seed.position
            continue
        delta = delta_by_id.get(seed.occurrence_id)
        source = sources.get(seed.source_knowledge_point_id)
        point = points.get(seed.knowledge_id)
        if delta is None or source is None or point is None:
            blocked = {
                "occurrence_id": seed.occurrence_id,
                "issue_code": "INCOMPLETE_SEMANTIC_EXECUTION_INPUT",
                "details": "The occurrence is missing a semantic delta, source knowledge point, or canonical knowledge point before writing.",
                "canonical_knowledge_id": seed.knowledge_id,
                "outline_node_id": seed.section_id,
                "rendered": False,
                "materialized": False,
            }
            result.blocked_occurrences.append(blocked)
            result.coverage.execution_blocked_occurrences.append(blocked)
            state.position = seed.position
            continue
        prior = [item for item in ordered if item.knowledge_id == seed.knowledge_id and item.position < seed.position]
        compilation = compile_occurrence_for_verified_availability(
            seed=seed,
            delta=delta,
            verified_before=state,
            has_previous=bool(prior),
            source_context=seed.context_title or source.context_title,
            future_contexts=[
                item.context_title or sources[item.source_knowledge_point_id].context_title
                for item in trajectories.get(seed.knowledge_id, [])
                if item.position > seed.position and item.source_knowledge_point_id in sources
            ],
            first_position=first_position,
        )
        if not compilation.executable or compilation.compiled_occurrence is None or compilation.effective_delta is None:
            blocked = {
                "occurrence_id": seed.occurrence_id,
                "issue_code": compilation.issue_code or "RUNTIME_COMPILATION_FAILED",
                "details": compilation.issue_details,
                "audit": compilation.audit,
                "prior_verified_facets": _available_facets(state, seed.knowledge_id),
            }
            result.blocked_occurrences.append(blocked)
            result.coverage.execution_blocked_occurrences.append(blocked)
            result.transitions.append({"occurrence_id": seed.occurrence_id, "grant_applied": False, "blocked_reasons": [compilation.issue_code or "RUNTIME_COMPILATION_FAILED"]})
            state.position = seed.position
            continue

        occurrence = compilation.compiled_occurrence
        effective_delta = compilation.effective_delta
        zero = decide_zero_render_occurrence(
            occurrence=occurrence,
            delta=effective_delta,
            prior_verified_support=_source_occurrences(state, seed.knowledge_id),
        )
        if zero is not None:
            result.coverage.zero_render_occurrences.append(zero)
            result.transitions.append({
                "occurrence_id": seed.occurrence_id,
                "render_decision": "ZERO_RENDER",
                "grant_applied": False,
                "before": _state_dict(state),
                "after": _state_dict(state),
            })
            state.position = seed.position
            continue

        brief = build_verified_occurrence_writing_brief(
            occurrence=occurrence,
            delta=effective_delta,
            source=source,
            point=point,
            verified_before=state,
        )
        render_error = ""
        try:
            candidate = render_occurrence(brief)
        except Exception as exc:  # Keep the occurrence auditable; never fallback to planned success.
            candidate = ""
            render_error = f"{type(exc).__name__}: {exc}"
        record = next((item for item in extract_rendered_occurrences(candidate) if item.occurrence_id == brief.occurrence_id), None)
        conformance = check_rendered_conformance([brief], candidate).results[0]
        claims = verify_rendered_evidence(
            markdown=candidate,
            digital_book=DigitalBook(book_id="local-execution", title="", metadata={}, projects=[]),
            briefs=[brief],
            evidence_by_id=evidence_by_id,
        )
        own_claims = [item for item in claims if item.occurrence_id == brief.occurrence_id and item.target == "markdown"]
        semantic_audit = audit_rendered_claims(
            markdown=candidate,
            briefs=[asdict(brief)],
            evidence_by_id=evidence_by_id,
            artifact_root="runtime-occurrence",
            judge=semantic_entailment_judge,
            semantic_routing_categories=CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
        )
        own_semantic_records = [
            item for item in semantic_audit.records if item.occurrence_id == brief.occurrence_id
        ]
        evidence_status = _semantic_evidence_status(own_semantic_records, occurrence)
        execution = OccurrenceExecutionResult(
            occurrence_id=occurrence.occurrence_id,
            rendered_span_id=(record.block_id or record.occurrence_id) if record else None,
            rendered_body=record.markdown if record else "",
            conformance_status=conformance.overall,
            evidence_status=evidence_status,
            conformance_verified_facets=tuple(
                facet for facet, status in conformance.must_teach_coverage.items() if status == ConformanceStatus.MATCH
            ),
            conformance_verified_extension_keys=tuple(
                key for key, status in conformance.extension_coverage.items() if status == ConformanceStatus.MATCH
            ),
            evidence_supported_facets=tuple(occurrence.intended_grants) if evidence_status == SupportStatus.SUPPORTED else (),
            evidence_supported_extension_keys=tuple(occurrence.intended_extension_keys) if evidence_status == SupportStatus.SUPPORTED else (),
            generation_provenance=record.generation_provenance if record else "unknown",
            semantic_claim_ids=tuple(item.claim_id for item in own_semantic_records),
            semantic_claim_statuses=tuple(item.final_status for item in own_semantic_records),
            semantic_audit_records=tuple(item.to_dict() for item in own_semantic_records),
        )
        transition = advance_verified_instructional_availability(state=state, occurrence=occurrence, execution=execution)
        result.transitions.append({
            "occurrence_id": occurrence.occurrence_id,
            "render_decision": "RENDER",
            "grant_applied": transition.grant_applied,
            "granted_facets": list(transition.granted_facets),
            "granted_extension_keys": list(transition.granted_extension_keys),
            "blocked_reasons": list(transition.blocked_reasons),
            "before": _state_dict(transition.before),
            "after": _state_dict(transition.after),
            "conformance": conformance.overall,
            "evidence": evidence_status,
            "generation_provenance": execution.generation_provenance,
            "semantic_claim_ids": list(execution.semantic_claim_ids),
            "semantic_claim_statuses": list(execution.semantic_claim_statuses),
            "semantic_evidence": list(execution.semantic_audit_records),
            "writer_error": render_error,
        })
        if render_error:
            blocked = {
                "occurrence_id": occurrence.occurrence_id,
                "issue_code": "WRITER_EXECUTION_FAILED",
                "details": render_error,
            }
            result.blocked_occurrences.append(blocked)
            result.coverage.execution_blocked_occurrences.append(blocked)
        if record is None:
            result.blocked_occurrences.append({
                "occurrence_id": occurrence.occurrence_id,
                "issue_code": "EXPECTED_RENDER_MISSING",
                "details": "Writer returned no code-owned occurrence span.",
            })
        else:
            result.markdown_occurrences.append({
                "occurrence_id": occurrence.occurrence_id,
                "chapter_id": occurrence.chapter_id,
                "section_id": occurrence.section_id,
                "source_title": source.title,
                "role": occurrence.role,
                "body": record.markdown,
                "rendered_span_id": execution.rendered_span_id,
            })
        result.coverage.briefs.append(brief)
        state = transition.after

    result.verified_state = state
    result.semantic_evidence_call_count = int(getattr(semantic_entailment_judge, "call_count", 0) if semantic_entailment_judge else 0)
    result.semantic_evidence_model = str(getattr(semantic_entailment_judge, "model", "") if semantic_entailment_judge else "")
    return result


def _evidence_status(claims, occurrence: PlannedOccurrence) -> str:
    statuses = [item.support_status for item in claims]
    if SupportStatus.UNSUPPORTED in statuses:
        return SupportStatus.UNSUPPORTED
    if SupportStatus.UNCERTAIN in statuses:
        return SupportStatus.UNCERTAIN
    if occurrence.intended_grants or occurrence.intended_extension_keys:
        return SupportStatus.SUPPORTED if statuses else SupportStatus.UNSUPPORTED
    return SupportStatus.SUPPORTED


def _semantic_evidence_status(records: list[Any], occurrence: PlannedOccurrence) -> str:
    """Resolve the same claim-level semantics used by the final audit.

    A partial or unresolved source-fact claim cannot establish a complete
    facet/extension grant.  Occurrences with no new grant obligation retain
    the old neutral status because they do not advance availability.
    """
    if not records:
        return SupportStatus.SUPPORTED if not (occurrence.intended_grants or occurrence.intended_extension_keys) else SupportStatus.UNSUPPORTED
    statuses = [item.final_status for item in records]
    if ClaimStatus.UNSUPPORTED in statuses:
        return SupportStatus.UNSUPPORTED
    if ClaimStatus.PARTIALLY_SUPPORTED in statuses:
        return SupportStatus.UNCERTAIN
    return SupportStatus.SUPPORTED


def _available_facets(state: InstructionalAvailabilityState, knowledge_id: str) -> list[str]:
    record = state.availability_by_knowledge.get(knowledge_id)
    return list(record.available_facets) if record else []


def _source_occurrences(state: InstructionalAvailabilityState, knowledge_id: str) -> list[str]:
    record = state.availability_by_knowledge.get(knowledge_id)
    if record is None:
        return []
    return list(dict.fromkeys(record.facet_source_occurrence_ids.values()))


def _state_dict(state: InstructionalAvailabilityState) -> dict:
    return asdict(state)
