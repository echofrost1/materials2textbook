from __future__ import annotations

from dataclasses import replace

from materials2textbook.knowledge_map.models import (
    BookPosition,
    KnowledgeMapping,
    KnowledgePoint,
    PlannedOccurrence,
    Prerequisite,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.semantic import MIN_TRUSTED_CONFIDENCE, SemanticPlanner


def plan_occurrences(
    *,
    source_points: list[SourceKnowledgePoint],
    knowledge_points: list[KnowledgePoint],
    mappings: list[KnowledgeMapping],
    prerequisites: list[Prerequisite],
    semantic_planner: SemanticPlanner,
) -> list[PlannedOccurrence]:
    knowledge_by_id = {point.knowledge_id: point for point in knowledge_points}
    mapping_by_source = {mapping.source_knowledge_point_id: mapping for mapping in mappings}
    occurrences: list[PlannedOccurrence] = []

    for source in source_points:
        mapping = mapping_by_source[source.source_knowledge_point_id]
        for knowledge_id in mapping.canonical_knowledge_ids:
            knowledge = knowledge_by_id[knowledge_id]
            occurrence_ordinal = len(occurrences) + 1
            occurrence_id = f"occ:{source.source_knowledge_point_id}:{knowledge_id}"
            proposed = semantic_planner.plan_occurrence(
                source=source,
                knowledge=knowledge,
                occurrence_id=occurrence_id,
                previous_occurrences=occurrences,
                prerequisites=prerequisites,
            )
            occurrences.append(
                replace(
                    proposed,
                    position=BookPosition(
                        chapter_ordinal=source.chapter_ordinal,
                        task_ordinal=source.task_ordinal,
                        occurrence_ordinal=occurrence_ordinal,
                        section_ordinal=source.section_ordinal,
                        source_point_ordinal=source.source_point_ordinal,
                    ),
                    mapping_confidence=mapping.confidence,
                    trusted_for_state=(
                        _proposal_is_trusted(proposed)
                        and _mapping_is_trusted(mapping)
                    ),
                )
            )
    return occurrences


def _proposal_is_trusted(proposal: PlannedOccurrence) -> bool:
    return (
        proposal.planning_confidence >= MIN_TRUSTED_CONFIDENCE
        and bool(proposal.planning_rationale.strip())
        and bool(proposal.planning_evidence_chunk_ids)
    )


def _mapping_is_trusted(mapping: KnowledgeMapping) -> bool:
    return (
        mapping.mapping_type != "UNCERTAIN"
        and mapping.confidence >= MIN_TRUSTED_CONFIDENCE
        and bool(mapping.rationale.strip())
        and bool(mapping.evidence_chunk_ids)
    )
