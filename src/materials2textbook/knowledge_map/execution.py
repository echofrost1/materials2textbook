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
    wrap_rendered_occurrence,
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


def execute_verified_sections(
    *,
    book_plan: Any,
    completeness_report: Any,
    occurrences: list[PlannedOccurrence],
    deltas: list[SemanticDelta],
    sources: dict[str, SourceKnowledgePoint],
    points: dict[str, KnowledgePoint],
    chunks: list[EvidenceChunk],
    section_writer: Callable[[Any, Any, list[EvidenceChunk]], dict[str, Any]],
    excluded_occurrence_ids: set[str] | None = None,
    semantic_entailment_judge: Any | None = None,
) -> SemanticExecutionResult:
    """Run the completeness-first section authoring path.

    A section is authored once from its Blueprint/Packet/Brief.  Occurrences
    remain the sequential execution and availability unit: each occurrence
    consumes only the verified state before its position and grants only the
    facet/extension support proven by its own rendered span.  This function is
    intentionally an opt-in production path so the legacy occurrence runner
    remains available for compatibility and regression tests.
    """

    from materials2textbook.knowledge_map.evidence_packet import build_section_evidence_packet
    from materials2textbook.knowledge_map.outline import book_plan_fingerprint
    from materials2textbook.knowledge_map.section_authoring import (
        author_section,
        build_section_authoring_brief,
    )
    from materials2textbook.knowledge_map.section_authoring_recovery import (
        classify_section_recovery,
        plan_section_recovery,
    )
    from materials2textbook.knowledge_map.teaching_blueprint import build_section_teaching_blueprint
    from materials2textbook.knowledge_map.semantic_evaluation import compile_occurrence_for_verified_availability

    ordered = sorted(occurrences, key=lambda item: item.position)
    delta_by_id = {item.occurrence_id: item for item in deltas}
    evidence_by_id = {item.chunk_id: item for item in chunks}
    trajectories: dict[str, list[PlannedOccurrence]] = {}
    for item in ordered:
        trajectories.setdefault(item.knowledge_id, []).append(item)
    first_position = {key: items[0].position for key, items in trajectories.items() if items}
    by_section: dict[str, list[PlannedOccurrence]] = {}
    for item in ordered:
        by_section.setdefault(item.section_id, []).append(item)
    state = InstructionalAvailabilityState()
    result = SemanticExecutionResult(coverage=WritingBriefCoverage(), verified_state=state)
    excluded = set(excluded_occurrence_ids or ())

    for section_id, seeds in by_section.items():
        compiled_items: list[tuple[PlannedOccurrence, Any, SemanticDelta, Any]] = []
        constraints: list[dict[str, Any]] = []
        section_blocked: list[dict[str, Any]] = []
        for seed in seeds:
            if seed.occurrence_id in excluded:
                section_blocked.append(_section_block(seed, "SKIPPED_BY_SEMANTIC_EVIDENCE_GATE"))
                continue
            delta = delta_by_id.get(seed.occurrence_id)
            source = sources.get(seed.source_knowledge_point_id)
            point = points.get(seed.knowledge_id)
            if delta is None or source is None or point is None:
                section_blocked.append(_section_block(seed, "INCOMPLETE_SEMANTIC_EXECUTION_INPUT"))
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
                section_blocked.append(_section_block(seed, compilation.issue_code or "RUNTIME_COMPILATION_FAILED", compilation.issue_details))
                continue
            occurrence = compilation.compiled_occurrence
            effective_delta = compilation.effective_delta
            zero = decide_zero_render_occurrence(
                occurrence=occurrence,
                delta=effective_delta,
                prior_verified_support=_source_occurrences(state, seed.knowledge_id),
            )
            compiled_items.append((seed, occurrence, effective_delta, zero))
            constraints.append(_section_constraint(occurrence, effective_delta, state, zero))

        # Build the section contracts against the frozen plan.  A failed
        # completeness gate is a production admission failure, not permission
        # to fall back to the old short occurrence writer.
        try:
            blueprint = build_section_teaching_blueprint(
                book_plan,
                section_id,
                completeness_report=completeness_report,
                source_book_plan_signature=book_plan_fingerprint(book_plan),
                prior_verified_support=_state_dict(state),
                occurrences=constraints,
            )
            packet = build_section_evidence_packet(
                book_plan,
                section_id,
                blueprint,
                chunks,
                prior_verified_support=_state_dict(state),
            )
            brief = build_section_authoring_brief(
                book_plan,
                section_id,
                blueprint,
                packet,
                occurrence_constraints=constraints,
                prior_verified_support=_state_dict(state),
            )
        except Exception as exc:
            for seed, _occurrence, _delta, _zero in compiled_items:
                section_blocked.append(_section_block(seed, "SECTION_AUTHORING_INPUT_FAILED", f"{type(exc).__name__}: {exc}"))
            _append_section_blocks(result, section_blocked, state)
            continue

        # If every occurrence in this section was blocked before writing, do
        # not call the section writer with an empty occurrence contract.  That
        # would invite a body whose blocks cannot be traced to any executable
        # occurrence.  Keep the Blueprint/Packet audit, but preserve the
        # fail-closed execution reason for the section.
        if not compiled_items:
            _append_section_blocks(result, section_blocked, state)
            result.section_assemblies.append({
                "section_id": section_id,
                "status": "BLOCKED_BEFORE_AUTHORING",
                "brief": brief.to_dict(),
                "packet": packet.to_dict(),
                "rendered_section": None,
                "recovery": {
                    "issues": [],
                    "proposals": [],
                    "auto_applied": False,
                    "reason": "all section occurrences were blocked before authoring",
                },
            })
            continue

        rendered = None
        render_error = ""
        try:
            draft = section_writer(brief, packet, chunks)
            rendered = author_section(brief, packet, lambda _brief: draft, claim_judge=semantic_entailment_judge)
        except Exception as exc:
            render_error = f"{type(exc).__name__}: {exc}"
            for seed, _occurrence, _delta, _zero in compiled_items:
                section_blocked.append(_section_block(seed, "SECTION_WRITER_FAILED", render_error))
            _append_section_blocks(result, section_blocked, state)
            result.section_assemblies.append({
                "section_id": section_id,
                "status": "BLOCKED",
                "brief": brief.to_dict(),
                "packet": packet.to_dict(),
                "writer_error": render_error,
                "recovery": [],
            })
            continue

        section_assembly = {
            "section_id": section_id,
            "status": rendered.render_status,
            "brief": brief.to_dict(),
            "packet": packet.to_dict(),
            "rendered_section": rendered.to_dict(),
            "recovery": [],
        }
        result.section_assemblies.append(section_assembly)
        _append_section_blocks(result, section_blocked, state)

        # Sequentially validate each occurrence span and advance only from its
        # own locally verified student-visible text.
        for seed, occurrence, effective_delta, zero in compiled_items:
            if zero is not None:
                result.coverage.zero_render_occurrences.append(zero)
                result.transitions.append({
                    "occurrence_id": occurrence.occurrence_id,
                    "render_decision": "ZERO_RENDER",
                    "grant_applied": False,
                    "before": _state_dict(state),
                    "after": _state_dict(state),
                    "authoring_granularity": "section",
                })
                state.position = seed.position
                continue
            source = sources[seed.source_knowledge_point_id]
            point = points[seed.knowledge_id]
            occurrence_brief = build_verified_occurrence_writing_brief(
                occurrence=occurrence,
                delta=effective_delta,
                source=source,
                point=point,
                verified_before=state,
            )
            spans = tuple(rendered.occurrence_span_map.get(occurrence.occurrence_id, ()))
            if not spans:
                blocked = _section_block(seed, "EXPECTED_RENDER_MISSING", "section materialization did not map the occurrence")
                _append_section_blocks(result, [blocked], state)
                state.position = seed.position
                continue
            span = spans[0]
            body = str(span.get("text") or "").strip()
            wrapped = wrap_rendered_occurrence(
                occurrence_brief,
                body,
                generation_provenance=str(rendered.generation_provenance.get("writer") or "section-writer"),
            )
            conformance = check_rendered_conformance([occurrence_brief], wrapped).results[0]
            claims = verify_rendered_evidence(
                markdown=wrapped,
                digital_book=DigitalBook(book_id="section-execution", title="", metadata={}, projects=[]),
                briefs=[occurrence_brief],
                evidence_by_id=evidence_by_id,
            )
            semantic_audit = audit_rendered_claims(
                markdown=wrapped,
                briefs=[asdict(occurrence_brief)],
                evidence_by_id=evidence_by_id,
                artifact_root="runtime-section",
                judge=semantic_entailment_judge,
                semantic_routing_categories=CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
            )
            own_records = [item for item in semantic_audit.records if item.occurrence_id == occurrence.occurrence_id]
            evidence_status = _semantic_evidence_status(own_records, occurrence)
            record = next((item for item in extract_rendered_occurrences(wrapped) if item.occurrence_id == occurrence.occurrence_id), None)
            execution = OccurrenceExecutionResult(
                occurrence_id=occurrence.occurrence_id,
                rendered_span_id=(record.block_id or record.occurrence_id) if record else None,
                rendered_body=body if record else "",
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
                generation_provenance=str(rendered.generation_provenance.get("writer") or "section-writer"),
                semantic_claim_ids=tuple(item.claim_id for item in own_records),
                semantic_claim_statuses=tuple(item.final_status for item in own_records),
                semantic_audit_records=tuple(item.to_dict() for item in own_records),
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
                "conformance_result": conformance.to_dict() if hasattr(conformance, "to_dict") else asdict(conformance),
                "evidence": evidence_status,
                "generation_provenance": execution.generation_provenance,
                "semantic_claim_ids": list(execution.semantic_claim_ids),
                "semantic_claim_statuses": list(execution.semantic_claim_statuses),
                "semantic_evidence": list(execution.semantic_audit_records),
                "authoring_granularity": "section",
            })
            result.coverage.briefs.append(occurrence_brief)
            result.markdown_occurrences.append({
                "occurrence_id": occurrence.occurrence_id,
                "chapter_id": occurrence.chapter_id,
                "section_id": occurrence.section_id,
                "source_title": source.title,
                "role": occurrence.role,
                "body": body,
                "rendered_span_id": execution.rendered_span_id,
                "section_authoring": True,
            })
            if not transition.grant_applied or conformance.overall != ConformanceStatus.MATCH or evidence_status != SupportStatus.SUPPORTED:
                result.blocked_occurrences.append({
                    "occurrence_id": occurrence.occurrence_id,
                    "issue_code": "SECTION_OCCURRENCE_VALIDATION_FAILED",
                    "details": "section span did not satisfy local conformance and evidence gates",
                    "rendered": bool(record),
                    "materialized": bool(record),
                })
                result.coverage.execution_blocked_occurrences.append(result.blocked_occurrences[-1])
            state = transition.after

        # Recovery is part of the official section execution audit.  This
        # first integration only classifies and plans local actions; it does
        # not silently retry or rewrite a section.  Any future accepted patch
        # must still pass the existing recovery revalidation gates before a
        # verified grant can be established.
        recovery_issues = classify_section_recovery(rendered, brief, packet)
        recovery_proposals = plan_section_recovery(
            recovery_issues,
            brief=brief,
            packet=packet,
        )
        section_assembly["recovery"] = {
            "issues": [item.to_dict() for item in recovery_issues],
            "proposals": [item.to_dict() for item in recovery_proposals],
            "auto_applied": False,
        }

    result.verified_state = state
    result.semantic_evidence_call_count = int(getattr(semantic_entailment_judge, "call_count", 0) if semantic_entailment_judge else 0)
    result.semantic_evidence_model = str(getattr(semantic_entailment_judge, "model", "") if semantic_entailment_judge else "")
    return result


def _section_constraint(occurrence: PlannedOccurrence, delta: SemanticDelta, state: InstructionalAvailabilityState, zero: Any) -> dict[str, Any]:
    return {
        "occurrence_id": occurrence.occurrence_id,
        "role": occurrence.role,
        "already_available_facets": list(_available_facets(state, occurrence.knowledge_id)),
        "required_facets": list(occurrence.required_self_facets),
        "must_teach_facets": list(occurrence.intended_grants),
        "must_not_reteach_facets": list(occurrence.repeated_aspects),
        "extension_keys": list(occurrence.intended_extension_keys),
        "required_current_contribution": [occurrence.intended_contribution] if occurrence.intended_contribution else [],
        "contribution_goal": occurrence.intended_contribution,
        "new_facets": list(delta.new_facets),
        "new_extension_keys": list(delta.new_extension_keys),
        "render_decision": "ZERO_RENDER" if zero is not None else "RENDER",
        "availability_source_occurrence_ids": _source_occurrences(state, occurrence.knowledge_id),
    }


def _section_block(seed: PlannedOccurrence, code: str, details: str = "") -> dict[str, Any]:
    return {
        "occurrence_id": seed.occurrence_id,
        "issue_code": code,
        "details": details,
        "canonical_knowledge_id": seed.knowledge_id,
        "outline_node_id": seed.section_id,
        "rendered": False,
        "materialized": False,
        "authoring_granularity": "section",
    }


def _append_section_blocks(result: SemanticExecutionResult, blocks: list[dict[str, Any]], state: InstructionalAvailabilityState) -> None:
    for blocked in blocks:
        result.blocked_occurrences.append(blocked)
        result.coverage.execution_blocked_occurrences.append(blocked)
        result.transitions.append({
            "occurrence_id": blocked.get("occurrence_id"),
            "render_decision": "NOT_EXECUTED",
            "grant_applied": False,
            "blocked_reasons": [blocked.get("issue_code")],
            "before": _state_dict(state),
            "after": _state_dict(state),
            "authoring_granularity": "section",
        })


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
