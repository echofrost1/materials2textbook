"""Sequential production execution for verified instructional availability."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

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
    # Audit-only view of occurrences that reached (or were stopped before)
    # the section authoring frontier.  It never changes runtime state.
    authoring_frontier: list[dict] = field(default_factory=list)
    verified_state: InstructionalAvailabilityState = field(default_factory=InstructionalAvailabilityState)
    semantic_evidence_call_count: int = 0
    semantic_evidence_model: str = ""
    preview_mode: bool = False

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
            "authoring_frontier": self.authoring_frontier,
            "verified_state": asdict(self.verified_state),
            "semantic_evidence": {
                "call_count": self.semantic_evidence_call_count,
                "model": self.semantic_evidence_model,
            },
            "preview_mode": self.preview_mode,
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
    source_bounded_prerequisite_calibration: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
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
            source_bounded_prerequisite_calibration=source_bounded_prerequisite_calibration,
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
    source_bounded_calibration: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    source_bounded_prerequisite_calibration: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    local_recovery_writer: Callable[[Any, Any, Any, Mapping[str, Any], list[EvidenceChunk]], str | None] | None = None,
    preview_mode: bool = False,
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
        attach_factual_claim_plan,
        build_factual_claim_plan,
        build_section_authoring_brief,
    )
    from materials2textbook.knowledge_map.section_authoring_recovery import (
        ACCEPTED,
        MINIMAL_SUPPORTED_REWRITE,
        apply_section_recovery,
        apply_recovery_patch_to_draft,
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
    result = SemanticExecutionResult(
        coverage=WritingBriefCoverage(),
        verified_state=state,
        preview_mode=preview_mode,
    )
    excluded = set(excluded_occurrence_ids or ())

    def _brief_with_materialized_evidence(
        brief: Any,
        spans: Iterable[Mapping[str, Any]],
        packet: Any,
        occurrence_id: str = "",
        section_brief: Any | None = None,
    ) -> Any:
        """Carry packet-authorized span evidence into occurrence-local audit.

        Section authoring owns the evidence binding, while the sequential
        runtime audits/grants one occurrence at a time.  A section writer may
        therefore legitimately cite a packet chunk even when the occurrence's
        planning delta did not carry that chunk ID.  Once the deterministic
        materializer has produced a span, its IDs are already alias-resolved
        and packet-validated; adding exactly those IDs to the immutable brief
        keeps the occurrence-local claim audit bounded to the section packet.
        No evidence is retrieved or expanded here.
        """
        packet_ids = {
            str(item)
            for item in (
                *getattr(packet, "authorized_primary_evidence_ids", ()),
                *getattr(packet, "authorized_reference_evidence_ids", ()),
            )
            if str(item).strip()
        }
        span_ids = []
        for item in spans:
            for evidence_id in item.get("evidence_ids", ()) or ():
                value = str(evidence_id or "").strip()
                if value and value in packet_ids and value in evidence_by_id and value not in span_ids:
                    span_ids.append(value)
        # A single-occurrence section may have a discourse/body block whose
        # model association is intentionally empty; deterministic
        # materialization already maps that block to the sole occurrence.
        # In that narrow case, carry the packet IDs bound to obligations linked
        # to this occurrence.  This is still packet-local and evidence-gated;
        # it is not a section-wide or book-wide evidence fallback.
        if not span_ids and occurrence_id and section_brief is not None:
            linked_ids: list[str] = []
            for obligation in getattr(section_brief, "all_obligations", ()):
                if occurrence_id not in tuple(getattr(obligation, "linked_occurrence_ids", ())):
                    continue
                for evidence_id in getattr(obligation, "authorized_evidence_ids", ()):
                    value = str(evidence_id or "").strip()
                    if value and value in packet_ids and value in evidence_by_id and value not in linked_ids:
                        linked_ids.append(value)
            span_ids = linked_ids
        if not span_ids:
            return brief
        source_ids = list(dict.fromkeys([*brief.source_chunk_ids, *span_ids]))
        delta_ids = list(dict.fromkeys([*brief.semantic_delta_evidence_ids, *span_ids]))
        return replace(
            brief,
            source_chunk_ids=source_ids,
            semantic_delta_evidence_ids=delta_ids,
        )

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
                preview_mode=preview_mode,
                source_bounded_prerequisite_calibration=source_bounded_prerequisite_calibration,
            )
            if not compilation.executable or compilation.compiled_occurrence is None or compilation.effective_delta is None:
                result.authoring_frontier.append(
                    _frontier_record(
                        seed,
                        role=seed.role,
                        prerequisite_status=(
                            "UNSATISFIED_CROSS_PREREQUISITE"
                            if compilation.issue_code == "CROSS_PREREQUISITE_NOT_VERIFIED"
                            else "NOT_COMPILED"
                        ),
                        authoring_status=(
                            "PREREQUISITE_BLOCKED"
                            if compilation.issue_code == "CROSS_PREREQUISITE_NOT_VERIFIED"
                            else "NOT_REACHED"
                        ),
                        exact_first_failure=compilation.issue_code or "RUNTIME_COMPILATION_FAILED",
                        audit=compilation.audit,
                    )
                )
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
            result.authoring_frontier.append(
                _frontier_record(
                    seed,
                    role=occurrence.role,
                    prerequisite_status="SATISFIED_OR_NONE",
                    authoring_status="ZERO_RENDER" if zero is not None else "AUTHORING_FRONTIER",
                    exact_first_failure="",
                    audit=compilation.audit,
                )
            )

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
                source_bounded_calibration=source_bounded_calibration,
            )
            packet = build_section_evidence_packet(
                book_plan,
                section_id,
                blueprint,
                chunks,
                prior_verified_support=_state_dict(state),
                source_bounded_calibration=source_bounded_calibration,
            )
            brief = build_section_authoring_brief(
                book_plan,
                section_id,
                blueprint,
                packet,
                occurrence_constraints=constraints,
                prior_verified_support=_state_dict(state),
            )
            factual_claim_plan = build_factual_claim_plan(
                brief,
                packet,
                chunks,
                claim_judge=semantic_entailment_judge,
            )
            brief = attach_factual_claim_plan(brief, factual_claim_plan)
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
            for item in result.authoring_frontier:
                if item.get("section_id") == section_id and item.get("authoring_status") == "AUTHORING_FRONTIER":
                    item["authoring_status"] = "SECTION_WRITER_FAILED"
                    item["exact_first_failure"] = render_error
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

        # Recovery is executed before occurrence-level validation.  A safe
        # local contraction can therefore repair the exact text that will be
        # wrapped and checked below; it never changes the Brief, Packet,
        # occurrence role, or evidence ownership.
        #
        # The section materializer's audit is intentionally permissive about
        # pedagogical blocks and plan-approved claim spans.  The sequential
        # grant gate below is stricter: it audits each occurrence with its
        # own brief and the complete packet-authorized evidence.  Run that
        # same occurrence-local semantic audit once before recovery and feed
        # its block-local records into the existing recovery classifier.  In
        # this way a claim that would otherwise be discovered only after the
        # recovery window (for example an unsupported exercise instruction)
        # can receive the same bounded local contraction as any other claim.
        def _occurrence_preflight_claim_audit() -> list[dict[str, Any]]:
            records: list[dict[str, Any]] = []
            for candidate_seed, candidate_occurrence, candidate_delta, candidate_zero in compiled_items:
                if candidate_zero is not None:
                    continue
                candidate_spans = tuple(rendered.occurrence_span_map.get(candidate_occurrence.occurrence_id, ()))
                if not candidate_spans:
                    continue
                candidate_source = sources[candidate_seed.source_knowledge_point_id]
                candidate_point = points[candidate_occurrence.knowledge_id]
                candidate_brief = build_verified_occurrence_writing_brief(
                    occurrence=candidate_occurrence,
                    delta=candidate_delta,
                    source=candidate_source,
                    point=candidate_point,
                    verified_before=state,
                )
                candidate_brief = _brief_with_materialized_evidence(
                    candidate_brief,
                    candidate_spans,
                    packet,
                    candidate_occurrence.occurrence_id,
                    brief,
                )
                candidate_body = "\\n\\n".join(
                    str(item.get("text") or "").strip()
                    for item in candidate_spans
                    if str(item.get("text") or "").strip()
                ).strip()
                candidate_wrapped = wrap_rendered_occurrence(
                    candidate_brief,
                    candidate_body,
                    generation_provenance=str(rendered.generation_provenance.get("writer") or "section-writer"),
                )
                audit = audit_rendered_claims(
                    markdown=candidate_wrapped,
                    briefs=[asdict(candidate_brief)],
                    evidence_by_id=evidence_by_id,
                    artifact_root="runtime-section-preflight",
                    judge=semantic_entailment_judge,
                    semantic_routing_categories=CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
                )
                block_ids = [
                    str(item.get("block_id") or item.get("span_id") or "").strip()
                    for item in candidate_spans
                    if str(item.get("block_id") or item.get("span_id") or "").strip()
                ]
                for index, record in enumerate(audit.records):
                    item = record.to_dict()
                    # A source-fact claim with no packet-authorized evidence
                    # cannot be rescued by a semantic judge.  Treat it as an
                    # occurrence-local unsupported claim before recovery;
                    # this keeps assessment/case text from becoming an
                    # implicit factual channel after a local rewrite.
                    if (
                        not tuple(item.get("authorized_evidence_ids") or ())
                        and str(item.get("content_type") or "")
                        in {"SOURCE_FACT", "UNSUPPORTED_DOMAIN_CLAIM"}
                    ):
                        item["final_status"] = ClaimStatus.UNSUPPORTED
                        item["semantic_result"] = ClaimStatus.UNSUPPORTED
                        item["resolution"] = "DETERMINISTIC"
                        item["rationale"] = "source-fact claim has no authorized packet evidence"
                        item["unsupported_part"] = claim_text = str(
                            item.get("claim_text") or item.get("source_span") or ""
                        )
                    claim_text = str(item.get("claim_text") or item.get("source_span") or "")
                    matching_block = ""
                    for span in candidate_spans:
                        block_id = str(span.get("block_id") or span.get("span_id") or "").strip()
                        span_text = str(span.get("text") or "")
                        if block_id and claim_text and claim_text in span_text:
                            matching_block = block_id
                            break
                    if not matching_block:
                        matching_block = block_ids[min(index, len(block_ids) - 1)] if block_ids else ""
                    item["source_occurrence_id"] = candidate_occurrence.occurrence_id
                    # Keep the synthetic claim suffix free of ``:claim:``
                    # markers.  The recovery classifier intentionally parses
                    # the last marker to recover the block id; embedding the
                    # original claim id (which itself contains that marker)
                    # would turn ``b01`` into ``b01:claim:...`` and make the
                    # exact block patch fail closed.
                    item["occurrence_id"] = (
                        f"{section_id}:{matching_block}:claim:{index}"
                        if matching_block
                        else candidate_occurrence.occurrence_id
                    )
                    records.append(item)
            return records

        initial_rendered = rendered
        recovery_issues: list[Any] = []
        recovery_proposals: list[Any] = []
        recovery_attempts: list[dict[str, Any]] = []
        recovery_rounds: list[dict[str, Any]] = []
        working_draft = deepcopy(draft)
        # A block can have several independent unsupported claims.  Recovery
        # proposals currently share the stable block-level proposal id, so
        # use the exact target text as part of the de-duplication key; this
        # lets one bounded round contract each distinct offending span while
        # still preventing the same target from looping across rounds.
        seen_proposal_ids: set[tuple[str, str]] = set()

        def _section_occurrence_conformance(candidate: Any) -> dict[str, Any]:
            for candidate_seed, candidate_occurrence, candidate_delta, candidate_zero in compiled_items:
                if candidate_zero is not None:
                    continue
                candidate_spans = tuple(candidate.occurrence_span_map.get(candidate_occurrence.occurrence_id, ()))
                if not candidate_spans:
                    return {"overall": ConformanceStatus.VIOLATION, "reason": "EXPECTED_RENDER_MISSING"}
                candidate_source = sources[candidate_seed.source_knowledge_point_id]
                candidate_point = points[candidate_occurrence.knowledge_id]
                candidate_brief = build_verified_occurrence_writing_brief(
                    occurrence=candidate_occurrence,
                    delta=candidate_delta,
                    source=candidate_source,
                    point=candidate_point,
                    verified_before=state,
                )
                # A section-level occurrence may legitimately be represented
                # by several ordered body/activity spans.  Conformance must
                # inspect the complete mapped contribution, not only the
                # first span (which caused false VIOLATION results in the
                # real equipment-recognition replay).
                candidate_body = "\n\n".join(
                    str(item.get("text") or "").strip()
                    for item in candidate_spans
                    if str(item.get("text") or "").strip()
                ).strip()
                candidate_brief = _brief_with_materialized_evidence(
                    candidate_brief,
                    candidate_spans,
                    packet,
                    candidate_occurrence.occurrence_id,
                    brief,
                )
                candidate_wrapped = wrap_rendered_occurrence(
                    candidate_brief,
                    candidate_body,
                    generation_provenance=str(candidate.generation_provenance.get("writer") or "section-writer"),
                )
                candidate_result = check_rendered_conformance([candidate_brief], candidate_wrapped).results[0]
                if candidate_result.overall != ConformanceStatus.MATCH:
                    return {
                        "overall": candidate_result.overall,
                        "occurrence_id": candidate_occurrence.occurrence_id,
                    }
            return {"overall": ConformanceStatus.MATCH}

        def _apply_recovery_round(proposals: Iterable[Any]) -> bool:
            nonlocal working_draft, rendered
            accepted_any = False
            for proposal in proposals:
                proposal_key = (proposal.proposal_id, proposal.target_text)
                if not proposal.retry_allowed or proposal_key in seen_proposal_ids:
                    continue
                seen_proposal_ids.add(proposal_key)
                attempt_proposal = proposal
                replacement_text = None
                # A recovery writer may only return a replacement for this
                # exact block.  It receives the immutable Brief/Packet and
                # the current draft; it cannot replan roles, obligations, or
                # evidence scope.  If it returns nothing, the existing
                # deterministic contraction remains the fail-closed fallback.
                if local_recovery_writer is not None and proposal.action == "CONTRACT_BLOCK":
                    try:
                        replacement_text = local_recovery_writer(
                            proposal,
                            brief,
                            packet,
                            working_draft,
                            chunks,
                        )
                    except Exception as exc:
                        replacement_text = None
                        proposal_provenance = dict(proposal.provenance)
                        proposal_provenance["local_recovery_writer_error"] = f"{type(exc).__name__}: {exc}"
                        attempt_proposal = replace(proposal, provenance=proposal_provenance)
                    if replacement_text and replacement_text.strip():
                        proposal_provenance = dict(attempt_proposal.provenance)
                        proposal_provenance.update({
                            "recovery_strategy": "minimal_supported_rewrite",
                            "same_brief": True,
                            "same_packet": True,
                            "same_evidence_scope": True,
                        })
                        attempt_proposal = replace(
                            attempt_proposal,
                            action=MINIMAL_SUPPORTED_REWRITE,
                            provenance=proposal_provenance,
                        )
                    else:
                        replacement_text = None
                attempt = apply_section_recovery(
                    brief=brief,
                    packet=packet,
                    original_draft=working_draft,
                    proposal=attempt_proposal,
                    replacement_text=replacement_text,
                    claim_judge=semantic_entailment_judge,
                    occurrence_conformance_checker=_section_occurrence_conformance,
                    allow_unresolved_other_issues=True,
                )
                recovery_attempts.append(attempt.to_dict())
                if attempt.status == ACCEPTED and attempt.post_rendered is not None:
                    try:
                        working_draft = apply_recovery_patch_to_draft(
                            working_draft,
                            attempt_proposal,
                            replacement_text=replacement_text,
                        )
                    except Exception:
                        # The acceptance gate and deterministic patch helper
                        # share the same operation; a mismatch is fail-closed.
                        break
                    rendered = attempt.post_rendered
                    accepted_any = True
            return accepted_any

        # A local recovery may need a second pass when removing one
        # unsupported sentence exposes another claim in the same block.  Two
        # bounded rounds are enough to preserve the fail-closed behavior while
        # avoiding an unbounded writer/recovery loop.
        for round_index in range(2):
            preflight_claim_audit = _occurrence_preflight_claim_audit()
            if preflight_claim_audit:
                provenance = dict(rendered.generation_provenance)
                provenance["local_claim_evidence_audit"] = preflight_claim_audit
                provenance["local_claim_audit_source"] = "occurrence_local_preflight"
                rendered = replace(rendered, generation_provenance=provenance)
            round_issues = classify_section_recovery(rendered, brief, packet)
            round_proposals = plan_section_recovery(
                round_issues,
                brief=brief,
                packet=packet,
            )
            recovery_issues.extend(round_issues)
            recovery_proposals.extend(round_proposals)
            if not round_proposals:
                recovery_rounds.append({
                    "round": round_index + 1,
                    "issues": len(round_issues),
                    "proposals": 0,
                    "accepted": 0,
                })
                break
            accepted = _apply_recovery_round(round_proposals)
            recovery_rounds.append({
                "round": round_index + 1,
                "issues": len(round_issues),
                "proposals": len(round_proposals),
                "accepted": sum(
                    1
                    for item in recovery_attempts
                    if item.get("status") == ACCEPTED
                ),
            })
            if not accepted:
                break

        section_assembly = {
            "section_id": section_id,
            "status": rendered.render_status,
            "brief": brief.to_dict(),
            "packet": packet.to_dict(),
            "initial_rendered_section": initial_rendered.to_dict(),
            "rendered_section": rendered.to_dict(),
            "recovery": {
                "issues": [item.to_dict() for item in recovery_issues],
                "proposals": [item.to_dict() for item in recovery_proposals],
                "attempts": recovery_attempts,
                "rounds": recovery_rounds,
                "auto_applied": any(item.get("status") == ACCEPTED for item in recovery_attempts),
            },
        }
        result.section_assemblies.append(section_assembly)
        _append_section_blocks(result, section_blocked, state)

        # Sequentially validate each occurrence span and advance only from its
        # own locally verified student-visible text.
        for seed, occurrence, effective_delta, zero in compiled_items:
            if zero is not None:
                _update_frontier_status(
                    result.authoring_frontier,
                    occurrence.occurrence_id,
                    authoring_status="ZERO_RENDER",
                    exact_first_failure="",
                    grant_applied=False,
                )
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
                _update_frontier_status(
                    result.authoring_frontier,
                    occurrence.occurrence_id,
                    authoring_status="EXPECTED_RENDER_MISSING",
                    exact_first_failure="EXPECTED_RENDER_MISSING",
                    grant_applied=False,
                )
                blocked = _section_block(seed, "EXPECTED_RENDER_MISSING", "section materialization did not map the occurrence")
                _append_section_blocks(result, [blocked], state)
                state.position = seed.position
                continue
            span = spans[0]
            occurrence_brief = _brief_with_materialized_evidence(
                occurrence_brief,
                spans,
                packet,
                occurrence.occurrence_id,
                brief,
            )
            # Validate/grant against the complete deterministic occurrence
            # contribution.  The first span remains the stable audit anchor,
            # while all mapped spans participate in semantic conformance.
            body = "\n\n".join(
                str(item.get("text") or "").strip()
                for item in spans
                if str(item.get("text") or "").strip()
            ).strip()
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
            transition = advance_verified_instructional_availability(
                state=deepcopy(state) if preview_mode else state,
                occurrence=occurrence,
                execution=execution,
            )
            # PREVIEW_FULLBOOK is a human-readable inspection run.  It may
            # show a later section even when strict sequential prerequisites
            # are not verified, but it is never allowed to establish runtime
            # availability or alter the strict state machine.
            preview_grant = transition.grant_applied
            if preview_mode:
                state.position = seed.position
            result.transitions.append({
                "occurrence_id": occurrence.occurrence_id,
                "render_decision": "RENDER",
                "grant_applied": False if preview_mode else transition.grant_applied,
                "preview_grant_candidate": preview_grant if preview_mode else False,
                "granted_facets": [] if preview_mode else list(transition.granted_facets),
                "granted_extension_keys": [] if preview_mode else list(transition.granted_extension_keys),
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
                "preview_runtime_status": (
                    "NOT_APPLIED_PREVIEW_ONLY" if preview_mode else "STRICT_RUNTIME"
                ),
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
            if not preview_mode:
                state = transition.after
            _update_frontier_status(
                result.authoring_frontier,
                occurrence.occurrence_id,
                authoring_status=("VERIFIED_GRANT" if transition.grant_applied and not preview_mode else "RENDERED_NOT_GRANTED"),
                exact_first_failure=(
                    ""
                    if transition.grant_applied and not preview_mode
                    else "; ".join(transition.blocked_reasons)
                    or (str(conformance.overall) if conformance.overall != ConformanceStatus.MATCH else "")
                    or (str(evidence_status) if evidence_status != SupportStatus.SUPPORTED else "")
                ),
                grant_applied=bool(transition.grant_applied and not preview_mode),
                granted_facets=[] if preview_mode else list(transition.granted_facets),
                granted_extension_keys=[] if preview_mode else list(transition.granted_extension_keys),
            )

    result.verified_state = state
    result.semantic_evidence_call_count = int(getattr(semantic_entailment_judge, "call_count", 0) if semantic_entailment_judge else 0)
    result.semantic_evidence_model = str(getattr(semantic_entailment_judge, "model", "") if semantic_entailment_judge else "")
    return result


def _section_constraint(occurrence: PlannedOccurrence, delta: SemanticDelta, state: InstructionalAvailabilityState, zero: Any) -> dict[str, Any]:
    return {
        "occurrence_id": occurrence.occurrence_id,
        "section_id": occurrence.section_id,
        "knowledge_id": occurrence.knowledge_id,
        "canonical_knowledge_id": occurrence.knowledge_id,
        "role": occurrence.role,
        "already_available_facets": list(_available_facets(state, occurrence.knowledge_id)),
        "required_facets": list(occurrence.required_self_facets),
        "must_teach_facets": list(occurrence.intended_grants),
        "must_not_reteach_facets": list(occurrence.repeated_aspects),
        "extension_keys": list(occurrence.intended_extension_keys),
        "required_current_contribution": [occurrence.intended_contribution] if occurrence.intended_contribution else [],
        "contribution_goal": occurrence.intended_contribution,
        "intended_contribution": occurrence.intended_contribution,
        "new_context": occurrence.new_context,
        "contribution_evidence_chunk_ids": list(occurrence.contribution_evidence_chunk_ids),
        "planning_evidence_chunk_ids": list(occurrence.planning_evidence_chunk_ids),
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


def _frontier_record(
    seed: PlannedOccurrence,
    *,
    role: str,
    prerequisite_status: str,
    authoring_status: str,
    exact_first_failure: str,
    audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "frontier_status": "AUTHORING_FRONTIER" if prerequisite_status == "SATISFIED_OR_NONE" else "NOT_FRONTIER",
        "section_id": seed.section_id,
        "occurrence_id": seed.occurrence_id,
        "canonical_knowledge_id": seed.knowledge_id,
        "role": role,
        "required_facets": list(seed.required_self_facets),
        "required_prerequisites": [
            {
                "knowledge_id": item.knowledge_id,
                "required_facets": list(item.required_facets),
                "minimum_required_facet": getattr(item, "minimum_required_facet", ""),
                "necessity": getattr(item, "necessity", "HARD"),
                "relation": item.relation,
            }
            for item in seed.required_prerequisites
        ],
        "prerequisite_status": prerequisite_status,
        "authoring_status": authoring_status,
        "exact_first_failure": exact_first_failure,
        "grant_applied": False,
        "granted_facets": [],
        "granted_extension_keys": [],
            # Compilation diagnostics are normally a mapping, but the
            # deterministic compiler may return an ordered list of audit
            # records for an untrusted/blocked plan.  Preserve that shape in
            # the frontier artifact instead of coercing it through ``dict``
            # (which would raise for a list and hide the real first failure).
            "compilation_audit": deepcopy(audit) if audit is not None else {},
        }


def _update_frontier_status(
    frontier: list[dict[str, Any]],
    occurrence_id: str,
    *,
    authoring_status: str,
    exact_first_failure: str,
    grant_applied: bool,
    granted_facets: list[str] | None = None,
    granted_extension_keys: list[str] | None = None,
) -> None:
    for item in frontier:
        if item.get("occurrence_id") != occurrence_id:
            continue
        item["authoring_status"] = authoring_status
        item["exact_first_failure"] = exact_first_failure
        item["grant_applied"] = bool(grant_applied)
        if granted_facets is not None:
            item["granted_facets"] = list(granted_facets)
        if granted_extension_keys is not None:
            item["granted_extension_keys"] = list(granted_extension_keys)
        return
