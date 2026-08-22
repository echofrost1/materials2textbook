from __future__ import annotations

from copy import deepcopy

from materials2textbook.knowledge_map.models import (
    AvailabilitySnapshot,
    InstructionalAvailabilityState,
    KnowledgeAvailabilityRecord,
    LearningRole,
    OccurrenceExecutionResult,
    PlannedOccurrence,
    VerifiedAvailabilityTransition,
)


def simulate_planned_instructional_availability(
    occurrences: list[PlannedOccurrence],
) -> list[AvailabilitySnapshot]:
    """Simulate *planned* availability for diagnosis and static validation only.

    This function intentionally models the proposal that every trusted planned
    occurrence will render and validate.  It must never be used as production
    evidence that a later occurrence may rely on earlier teaching.  Runtime
    availability is advanced exclusively by
    :func:`advance_verified_instructional_availability`.
    """
    state = InstructionalAvailabilityState()
    snapshots: list[AvailabilitySnapshot] = []
    for occurrence in sorted(occurrences, key=lambda item: item.position):
        before = deepcopy(state)
        self_available = self_requirements_available(before, occurrence)
        cross_available = cross_requirements_available(before, occurrence)
        blocked_reasons: list[str] = []
        if not occurrence.trusted_for_state:
            blocked_reasons.append("semantic_planning_confidence_below_threshold")
        if not self_available:
            blocked_reasons.append("self_requirements_not_instructionally_available")
        if not cross_available:
            blocked_reasons.append("cross_knowledge_prerequisites_not_instructionally_available")

        transition_applied = not blocked_reasons
        if transition_applied:
            _apply_transition(state, occurrence)
        state.position = occurrence.position
        snapshots.append(
            AvailabilitySnapshot(
                occurrence_id=occurrence.occurrence_id,
                position=occurrence.position,
                before=before,
                after=deepcopy(state),
                self_requirements_available=self_available,
                cross_requirements_available=cross_available,
                transition_applied=transition_applied,
                blocked_reasons=blocked_reasons,
                availability_kind="PLANNED",
            )
        )
    return snapshots


def simulate_instructional_availability(
    occurrences: list[PlannedOccurrence],
) -> list[AvailabilitySnapshot]:
    """Compatibility name for planning-only availability simulation.

    New production code must call either
    ``simulate_planned_instructional_availability`` (diagnostics) or
    ``advance_verified_instructional_availability`` (runtime), so the two
    meanings cannot be confused at a call site.
    """
    return simulate_planned_instructional_availability(occurrences)


def advance_verified_instructional_availability(
    *,
    state: InstructionalAvailabilityState,
    occurrence: PlannedOccurrence,
    execution: OccurrenceExecutionResult,
) -> VerifiedAvailabilityTransition:
    """Advance runtime availability only after verified rendered teaching.

    A grant is established only where the execution record proves both a
    non-empty student-visible body and the same facet/extension in its local
    conformance and evidence outputs.  A failed render, empty body,
    conformance failure, evidence failure, untrusted plan, or unavailable
    prerequisite is an auditable non-transition rather than an implicit
    fallback success.
    """
    if execution.occurrence_id != occurrence.occurrence_id:
        raise ValueError(
            "Execution result must belong to the occurrence whose availability is advanced."
        )
    before = deepcopy(state)
    self_available = self_requirements_available(before, occurrence)
    cross_available = cross_requirements_available(before, occurrence)
    blocked_reasons: list[str] = []
    if not occurrence.trusted_for_state:
        blocked_reasons.append("semantic_planning_confidence_below_threshold")
    if not execution.rendered_span_id:
        blocked_reasons.append("no_rendered_student_visible_span")
    if not execution.rendered_body.strip():
        blocked_reasons.append("rendered_student_visible_body_empty")
    if execution.generation_provenance in {"rule_template_fallback", "explicit_fallback"}:
        blocked_reasons.append("fallback_body_not_instructional")
    if execution.conformance_status not in {"MATCH", "PASS"}:
        blocked_reasons.append(f"local_conformance_not_passed:{execution.conformance_status or 'MISSING'}")
    if execution.evidence_status not in {"SUPPORTED", "PASS"}:
        blocked_reasons.append(f"local_evidence_not_passed:{execution.evidence_status or 'MISSING'}")
    if not self_available:
        blocked_reasons.append("self_requirements_not_verified_available")
    if not cross_available:
        blocked_reasons.append("cross_knowledge_prerequisites_not_verified_available")

    granted_facets = tuple(
        facet
        for facet in occurrence.intended_grants
        if facet in execution.conformance_verified_facets and facet in execution.evidence_supported_facets
    )
    granted_extensions = tuple(
        extension_key
        for extension_key in occurrence.intended_extension_keys
        if extension_key in execution.conformance_verified_extension_keys
        and extension_key in execution.evidence_supported_extension_keys
    )
    missing_facets = sorted(set(occurrence.intended_grants) - set(granted_facets))
    missing_extensions = sorted(set(occurrence.intended_extension_keys) - set(granted_extensions))
    if missing_facets:
        blocked_reasons.append(f"facet_grant_not_verified:{','.join(missing_facets)}")
    if missing_extensions:
        blocked_reasons.append(f"extension_grant_not_verified:{','.join(missing_extensions)}")

    grant_applied = not blocked_reasons
    if grant_applied:
        _apply_verified_transition(state, occurrence, granted_facets, granted_extensions)
    state.position = occurrence.position
    return VerifiedAvailabilityTransition(
        occurrence_id=occurrence.occurrence_id,
        position=occurrence.position,
        before=before,
        after=deepcopy(state),
        execution=execution,
        self_requirements_available=self_available,
        cross_requirements_available=cross_available,
        grant_applied=grant_applied,
        granted_facets=granted_facets if grant_applied else (),
        granted_extension_keys=granted_extensions if grant_applied else (),
        blocked_reasons=blocked_reasons,
    )


def self_requirements_available(state: InstructionalAvailabilityState, occurrence: PlannedOccurrence) -> bool:
    record = state.availability_by_knowledge.get(occurrence.knowledge_id)
    if not occurrence.required_self_facets and not occurrence.required_self_extension_keys:
        return True
    return _record_has(record, occurrence.required_self_facets, occurrence.required_self_extension_keys)


def cross_requirements_available(state: InstructionalAvailabilityState, occurrence: PlannedOccurrence) -> bool:
    # The compiler preserves SUPPORTING/BACKGROUND uses for audit and writer
    # context, but they are not instructional prerequisites.  Treating them as
    # blocking here would contradict the compiled prerequisite policy and stop
    # availability propagation for an otherwise teachable occurrence.
    blocking = [
        requirement
        for requirement in occurrence.required_prerequisites
        if requirement.relation == "HARD" and requirement.use_type == "DIRECT"
    ]
    return all(
        _record_has(
            state.availability_by_knowledge.get(requirement.knowledge_id),
            requirement.required_facets,
            requirement.required_extension_keys,
        )
        for requirement in blocking
    )


def _record_has(record: KnowledgeAvailabilityRecord | None, facets: list[str], extension_keys: list[str]) -> bool:
    if record is None:
        return False
    return set(facets).issubset(record.available_facets) and set(extension_keys).issubset(record.available_extension_keys)


def _apply_transition(state: InstructionalAvailabilityState, occurrence: PlannedOccurrence) -> None:
    """Apply an assumed transition for planning diagnostics only."""
    record = state.availability_by_knowledge.setdefault(occurrence.knowledge_id, KnowledgeAvailabilityRecord())
    record.available_facets = _unique([*record.available_facets, *occurrence.intended_grants])
    record.available_extension_keys = _unique([*record.available_extension_keys, *occurrence.intended_extension_keys])
    if record.first_available_position is None and (record.available_facets or record.available_extension_keys):
        record.first_available_position = occurrence.position
    if occurrence.role in {LearningRole.INTRO, LearningRole.TEACH, LearningRole.EXTEND}:
        record.last_taught_task_ordinal = occurrence.position.task_ordinal
    record.last_activated_task_ordinal = occurrence.position.task_ordinal
    for requirement in occurrence.required_prerequisites:
        prerequisite_record = state.availability_by_knowledge.get(requirement.knowledge_id)
        if prerequisite_record:
            prerequisite_record.last_activated_task_ordinal = occurrence.position.task_ordinal


def _apply_verified_transition(
    state: InstructionalAvailabilityState,
    occurrence: PlannedOccurrence,
    granted_facets: tuple[str, ...],
    granted_extensions: tuple[str, ...],
) -> None:
    """Apply a grant whose rendered occurrence has passed both local checks."""
    record = state.availability_by_knowledge.setdefault(occurrence.knowledge_id, KnowledgeAvailabilityRecord())
    for facet in granted_facets:
        if facet and facet not in record.available_facets:
            record.available_facets.append(facet)
            record.facet_source_occurrence_ids[facet] = occurrence.occurrence_id
    for extension_key in granted_extensions:
        if extension_key and extension_key not in record.available_extension_keys:
            record.available_extension_keys.append(extension_key)
            record.extension_source_occurrence_ids[extension_key] = occurrence.occurrence_id
    if record.first_available_position is None and (record.available_facets or record.available_extension_keys):
        record.first_available_position = occurrence.position
    if occurrence.role in {LearningRole.INTRO, LearningRole.TEACH, LearningRole.EXTEND}:
        record.last_taught_task_ordinal = occurrence.position.task_ordinal
    record.last_activated_task_ordinal = occurrence.position.task_ordinal
    for requirement in occurrence.required_prerequisites:
        prerequisite_record = state.availability_by_knowledge.get(requirement.knowledge_id)
        if prerequisite_record:
            prerequisite_record.last_activated_task_ordinal = occurrence.position.task_ordinal


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
