from __future__ import annotations

from materials2textbook.knowledge_map.models import (
    AvailabilitySnapshot,
    KnowledgeMapping,
    LearningRole,
    PlannedOccurrence,
    RelationJudgement,
    ValidationIssue,
)
from materials2textbook.knowledge_map.semantic import MIN_TRUSTED_CONFIDENCE, SemanticPlanner


def validate_planned_trajectory(
    *,
    occurrences: list[PlannedOccurrence],
    mappings: list[KnowledgeMapping],
    snapshots: list[AvailabilitySnapshot],
    semantic_planner: SemanticPlanner,
    recall_after_tasks: int = 3,
) -> list[ValidationIssue]:
    snapshots_by_occurrence = {snapshot.occurrence_id: snapshot for snapshot in snapshots}
    issues: list[ValidationIssue] = []
    for mapping in mappings:
        if mapping.mapping_type == "UNCERTAIN" or not mapping.canonical_knowledge_ids:
            issues.append(
                _issue(
                    "UNRESOLVED_KNOWLEDGE_IDENTITY",
                    "high",
                    "",
                    mapping.source_knowledge_point_id,
                    None,
                    "The source knowledge point has no trusted canonical mapping.",
                    {"mapping_type": mapping.mapping_type, "confidence": mapping.confidence},
                    "MANUAL_REVIEW",
                )
            )

    earlier_by_knowledge: dict[str, list[PlannedOccurrence]] = {}
    for occurrence in sorted(occurrences, key=lambda item: item.position):
        snapshot = snapshots_by_occurrence[occurrence.occurrence_id]
        if not occurrence.trusted_for_state:
            issues.append(
                _issue(
                    "SEMANTIC_PLANNING_LOW_CONFIDENCE",
                    "medium",
                    occurrence.knowledge_id,
                    occurrence.occurrence_id,
                    occurrence,
                    "This semantic proposal or canonical mapping is visible for review but was not allowed to update instructional availability.",
                    {
                        "planning_confidence": occurrence.planning_confidence,
                        "mapping_confidence": occurrence.mapping_confidence,
                        "threshold": MIN_TRUSTED_CONFIDENCE,
                        "has_planning_rationale": bool(occurrence.planning_rationale.strip()),
                        "has_planning_evidence": bool(occurrence.planning_evidence_chunk_ids),
                    },
                    "MANUAL_REVIEW",
                )
            )
        if not snapshot.self_requirements_available:
            issues.append(
                _issue(
                    "SELF_REQUIREMENT_GAP",
                    "high",
                    occurrence.knowledge_id,
                    occurrence.occurrence_id,
                    occurrence,
                    "Required prior facets of the same knowledge point have not been instructionally provided earlier.",
                    {
                        "required_self_facets": occurrence.required_self_facets,
                        "required_self_extension_keys": occurrence.required_self_extension_keys,
                    },
                    "UPGRADE_TO_TEACH",
                )
            )
        for requirement in occurrence.required_prerequisites:
            record = snapshot.before.availability_by_knowledge.get(requirement.knowledge_id)
            facets_available = bool(record) and set(requirement.required_facets).issubset(record.available_facets)
            extensions_available = bool(record) and set(requirement.required_extension_keys).issubset(record.available_extension_keys)
            if facets_available and extensions_available:
                continue
            issues.append(
                _issue(
                    "PREREQUISITE_GAP",
                    "high" if requirement.relation == "HARD" else "medium",
                    occurrence.knowledge_id,
                    occurrence.occurrence_id,
                    occurrence,
                    "A required cross-knowledge prerequisite is not instructionally available before this task.",
                    {
                        "prerequisite_knowledge_id": requirement.knowledge_id,
                        "required_facets": requirement.required_facets,
                        "required_extension_keys": requirement.required_extension_keys,
                        "available_facets": record.available_facets if record else [],
                        "available_extension_keys": record.available_extension_keys if record else [],
                    },
                    "UPGRADE_TO_TEACH",
                )
            )

        issues.extend(_recall_policy_issues(occurrence, snapshot, recall_after_tasks))
        prior = earlier_by_knowledge.setdefault(occurrence.knowledge_id, [])
        issues.extend(_role_and_increment_issues(occurrence, snapshot, prior, semantic_planner))
        prior.append(occurrence)
    return sorted(issues, key=lambda item: (item.position, item.type, item.issue_id))


def _recall_policy_issues(
    occurrence: PlannedOccurrence,
    snapshot: AvailabilitySnapshot,
    recall_after_tasks: int,
) -> list[ValidationIssue]:
    if occurrence.role not in {LearningRole.APPLY, LearningRole.EXTEND}:
        return []
    requirements = []
    if occurrence.required_self_facets or occurrence.required_self_extension_keys:
        requirements.append((occurrence.knowledge_id, occurrence.required_self_facets, occurrence.required_self_extension_keys, "self"))
    requirements.extend(
        (item.knowledge_id, item.required_facets, item.required_extension_keys, item.use_type)
        for item in occurrence.required_prerequisites
        if item.use_type == "DIRECT"
    )
    issues: list[ValidationIssue] = []
    for knowledge_id, facets, extension_keys, use_type in requirements:
        record = snapshot.before.availability_by_knowledge.get(knowledge_id)
        if not record or record.last_activated_task_ordinal is None:
            continue
        if not set(facets).issubset(record.available_facets) or not set(extension_keys).issubset(record.available_extension_keys):
            continue
        intervening_tasks = occurrence.position.task_ordinal - record.last_activated_task_ordinal - 1
        if intervening_tasks < recall_after_tasks:
            continue
        issues.append(
            _issue(
                "RECALL_POLICY_TRIGGERED",
                "medium",
                occurrence.knowledge_id,
                occurrence.occurrence_id,
                occurrence,
                "The required instructional context has been inactive for more tasks than the configured recall policy permits.",
                {
                    "required_knowledge_id": knowledge_id,
                    "requirement_scope": use_type,
                    "intervening_task_count": intervening_tasks,
                    "recall_after_tasks": recall_after_tasks,
                    "last_activated_task_ordinal": record.last_activated_task_ordinal,
                },
                "ADD_RECALL",
            )
        )
    return issues


def _role_and_increment_issues(
    occurrence: PlannedOccurrence,
    snapshot: AvailabilitySnapshot,
    prior: list[PlannedOccurrence],
    semantic_planner: SemanticPlanner,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    record = snapshot.before.availability_by_knowledge.get(occurrence.knowledge_id)
    if occurrence.role == LearningRole.INTRO and record and record.available_facets:
        issues.append(
            _issue(
                "ROLE_SEQUENCE_CONFLICT",
                "medium",
                occurrence.knowledge_id,
                occurrence.occurrence_id,
                occurrence,
                "INTRO is planned after this knowledge point is already instructionally available.",
                {"available_facets": record.available_facets},
                "REWRITE_AS_APPLY",
            )
        )
    if occurrence.role not in {LearningRole.INTRO, LearningRole.TEACH} or not prior or not record:
        return issues
    if not set(occurrence.intended_grants).issubset(record.available_facets):
        return issues
    if not set(occurrence.intended_extension_keys).issubset(record.available_extension_keys):
        return issues
    previous = prior[-1]
    # Phase 1.5's semantic delta is the sole source for repeated-explanation
    # facts.  The validator triggers the issue deterministically from that
    # fact; the legacy planner relation remains only for Phase 1 fallback.
    if occurrence.repeats_prior_explanation and not occurrence.intended_grants and not occurrence.intended_extension_keys:
        judgement = RelationJudgement("EQUIVALENT", 1.0, "SemanticDelta marks a repeated explanation with no increment.", occurrence.planning_evidence_chunk_ids)
    else:
        judgement = semantic_planner.judge_relation(previous, occurrence)
    if judgement.relation != "EQUIVALENT" or judgement.confidence < MIN_TRUSTED_CONFIDENCE:
        return issues
    issues.append(
        _issue(
            "NO_COGNITIVE_INCREMENT",
            "medium",
            occurrence.knowledge_id,
            occurrence.occurrence_id,
            occurrence,
            "The plan repeats the same instructional role and already-available contribution without a verified new condition.",
            {
                "previous_occurrence_id": previous.occurrence_id,
                "available_facets": record.available_facets,
                "current_grants": occurrence.intended_grants,
                "current_extension_keys": occurrence.intended_extension_keys,
                "relation_confidence": judgement.confidence,
            },
            "REWRITE_AS_APPLY",
            judgement,
        )
    )
    return issues


def _issue(
    issue_type: str,
    severity: str,
    knowledge_id: str,
    occurrence_id: str,
    occurrence: PlannedOccurrence | None,
    diagnosis: str,
    deterministic_evidence: dict,
    suggested_future_repair: str,
    judgement: RelationJudgement | None = None,
) -> ValidationIssue:
    position = occurrence.position if occurrence else _unknown_position()
    suffix = occurrence_id or knowledge_id or "source"
    return ValidationIssue(
        issue_id=f"{issue_type.lower()}:{suffix}",
        type=issue_type,
        severity=severity,
        knowledge_id=knowledge_id,
        occurrence_id=occurrence_id,
        position=position,
        diagnosis=diagnosis,
        deterministic_evidence=deterministic_evidence,
        semantic_judgement=(judgement.relation if judgement else ""),
        suggested_future_repair=suggested_future_repair,
    )


def _unknown_position():
    from materials2textbook.knowledge_map.models import BookPosition

    return BookPosition(0, 0, 0)
