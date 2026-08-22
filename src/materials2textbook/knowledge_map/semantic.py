from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from materials2textbook.knowledge_map.models import (
    BookPosition,
    KnowledgeKind,
    KnowledgePoint,
    LearningRole,
    MasteryFacet,
    PlannedOccurrence,
    Prerequisite,
    PrerequisiteUse,
    RelationJudgement,
    SourceKnowledgePoint,
)


MIN_TRUSTED_CONFIDENCE = 0.65


def prerequisite_has_runtime_basis(
    *,
    knowledge_id: str,
    required_facets: list[str],
    required_extension_keys: list[str],
    rationale: str,
    evidence_chunk_ids: list[str],
    provenance: str,
    supporting_basis: str,
    confidence: float,
) -> bool:
    """Return whether a prerequisite proposal may affect runtime execution.

    The explicit ``trusted_for_runtime`` flag is only an audit hint; it must
    never bypass the proposal's own canonical target, requirement, rationale,
    support provenance, and confidence checks.
    """
    return bool(
        knowledge_id.strip()
        and rationale.strip()
        and (evidence_chunk_ids or provenance.strip() or supporting_basis.strip())
        and (required_facets or required_extension_keys)
        and confidence >= MIN_TRUSTED_CONFIDENCE
    )


class SemanticPlanner(Protocol):
    """Semantic boundary for an optional LLM-backed planner.

    Implementations must return confidence, rationale, and source evidence IDs.
    The deterministic pipeline decides whether a proposal can affect state.
    """

    def plan_occurrence(
        self,
        *,
        source: SourceKnowledgePoint,
        knowledge: KnowledgePoint,
        occurrence_id: str,
        previous_occurrences: list[PlannedOccurrence],
        prerequisites: list[Prerequisite],
    ) -> PlannedOccurrence: ...

    def canonicalize_source_points(
        self,
        source_points: list[SourceKnowledgePoint],
    ) -> tuple[list[KnowledgePoint], list["KnowledgeMapping"]] | None: ...

    def propose_prerequisites(
        self,
        knowledge_points: list[KnowledgePoint],
        source_points: list[SourceKnowledgePoint],
    ) -> list[Prerequisite]: ...

    def judge_relation(
        self,
        previous: PlannedOccurrence,
        current: PlannedOccurrence,
    ) -> RelationJudgement: ...


@dataclass
class HeuristicSemanticPlanner:
    """Auditable fallback; it makes no cross-knowledge prerequisite claims."""

    def canonicalize_source_points(
        self,
        source_points: list[SourceKnowledgePoint],
    ) -> tuple[list[KnowledgePoint], list["KnowledgeMapping"]] | None:
        # Returning the deterministic result through this explicit boundary lets
        # an LLM-backed implementation replace it without changing state logic.
        from materials2textbook.knowledge_map.canonicalization import _canonicalize_deterministic

        return _canonicalize_deterministic(source_points)

    def propose_prerequisites(
        self,
        knowledge_points: list[KnowledgePoint],
        source_points: list[SourceKnowledgePoint],
    ) -> list[Prerequisite]:
        return []

    def plan_occurrence(
        self,
        *,
        source: SourceKnowledgePoint,
        knowledge: KnowledgePoint,
        occurrence_id: str,
        previous_occurrences: list[PlannedOccurrence],
        prerequisites: list[Prerequisite],
    ) -> PlannedOccurrence:
        text = f"{source.title} {source.context_title}".lower()
        prior = [item for item in previous_occurrences if item.knowledge_id == knowledge.knowledge_id]
        role = self._role(text, prior)
        grants = self._grants(knowledge.kind, role)
        self_facets = self._self_requirements(knowledge.kind, role, prior)
        extension_keys = [f"context:{_key(source.context_title)}"] if role == LearningRole.EXTEND else []
        required_prerequisites = [
            PrerequisiteUse(
                knowledge_id=edge.source_knowledge_id,
                required_facets=list(edge.required_facets),
                required_extension_keys=list(edge.required_extension_keys),
                relation=edge.relation,
                use_type=edge.use_type,
                edge_id=edge.edge_id,
                rationale=edge.rationale,
                evidence_chunk_ids=list(edge.evidence_chunk_ids),
                confidence=edge.confidence,
                provenance=edge.provenance,
                supporting_basis=edge.supporting_basis,
                trusted_for_runtime=prerequisite_has_runtime_basis(
                    knowledge_id=edge.source_knowledge_id,
                    required_facets=list(edge.required_facets),
                    required_extension_keys=list(edge.required_extension_keys),
                    rationale=edge.rationale,
                    evidence_chunk_ids=list(edge.evidence_chunk_ids),
                    provenance=edge.provenance,
                    supporting_basis=edge.supporting_basis,
                    confidence=edge.confidence,
                ),
            )
            for edge in prerequisites
            if edge.target_knowledge_id == knowledge.knowledge_id and edge.source_knowledge_id != knowledge.knowledge_id
        ]
        contribution = self._contribution(knowledge, source, role, extension_keys)
        return PlannedOccurrence(
            occurrence_id=occurrence_id,
            knowledge_id=knowledge.knowledge_id,
            source_knowledge_point_id=source.source_knowledge_point_id,
            position=BookPosition(
                chapter_ordinal=source.chapter_ordinal,
                task_ordinal=source.task_ordinal,
                occurrence_ordinal=0,
                section_ordinal=source.section_ordinal,
                source_point_ordinal=source.source_point_ordinal,
            ),
            chapter_id=source.chapter_id,
            section_id=source.section_id,
            context_title=source.context_title,
            source_chunk_ids=list(source.source_chunk_ids),
            role=role,
            required_self_facets=self_facets,
            required_prerequisites=required_prerequisites,
            intended_grants=grants,
            intended_extension_keys=extension_keys,
            intended_contribution=contribution,
            new_context=source.context_title if role in {LearningRole.APPLY, LearningRole.EXTEND} else "",
            repeated_aspects=[knowledge.title] if prior else [],
            contribution_confidence=0.82,
            contribution_rationale="Deterministic fallback derives contribution from the role and fixed task context.",
            contribution_evidence_chunk_ids=list(source.source_chunk_ids),
            planning_confidence=0.82,
            planning_rationale="Deterministic fallback inferred the role from the fixed outline label and prior occurrences.",
            planning_evidence_chunk_ids=list(source.source_chunk_ids),
            trusted_for_state=True,
        )

    def judge_relation(self, previous: PlannedOccurrence, current: PlannedOccurrence) -> RelationJudgement:
        if current.role == LearningRole.APPLY:
            return RelationJudgement("APPLICATION", 0.9, "The planned role is APPLY.", current.planning_evidence_chunk_ids)
        if current.role == LearningRole.EXTEND or current.intended_extension_keys:
            return RelationJudgement("EXTENSION", 0.9, "The planned role or extension keys indicate a new condition.", current.planning_evidence_chunk_ids)
        if _key(previous.intended_contribution) == _key(current.intended_contribution):
            return RelationJudgement("EQUIVALENT", 0.8, "The planned teaching contribution is the same after normalization.", current.planning_evidence_chunk_ids)
        return RelationJudgement("DISTINCT", 0.68, "The fallback cannot prove that the two planned contributions are equivalent.", current.planning_evidence_chunk_ids)

    def _role(self, text: str, prior: list[PlannedOccurrence]) -> str:
        if _has(text, ("回顾", "复习", "回忆", "recall")):
            return LearningRole.RECALL
        if not prior:
            return LearningRole.INTRO if _has(text, ("认识", "概述", "入门", "初识", "overview", "intro")) else LearningRole.TEACH
        if _has(text, ("拓展", "进阶", "变体", "异常", "限制", "优化", "复杂", "extend")):
            return LearningRole.EXTEND
        if _has(text, ("应用", "实训", "案例", "练习", "实践", "任务", "apply")):
            return LearningRole.APPLY
        return LearningRole.TEACH

    def _grants(self, kind: str, role: str) -> list[str]:
        if role == LearningRole.INTRO:
            return [MasteryFacet.ORIENTED]
        if role in {LearningRole.RECALL, LearningRole.APPLY}:
            return []
        if role == LearningRole.EXTEND:
            return [MasteryFacet.ANALYZE]
        if kind in {KnowledgeKind.SKILL, KnowledgeKind.PROCEDURE}:
            return [MasteryFacet.PERFORM]
        if kind == KnowledgeKind.METHOD:
            return [MasteryFacet.EXPLAIN, MasteryFacet.PERFORM]
        return [MasteryFacet.EXPLAIN]

    def _self_requirements(self, kind: str, role: str, prior: list[PlannedOccurrence]) -> list[str]:
        if role == LearningRole.TEACH and prior and prior[-1].role == LearningRole.INTRO:
            return [MasteryFacet.ORIENTED]
        if role == LearningRole.RECALL:
            return [MasteryFacet.ORIENTED]
        if role in {LearningRole.APPLY, LearningRole.EXTEND}:
            return [MasteryFacet.PERFORM] if kind in {KnowledgeKind.SKILL, KnowledgeKind.PROCEDURE, KnowledgeKind.METHOD} else [MasteryFacet.EXPLAIN]
        return []

    def _contribution(
        self,
        knowledge: KnowledgePoint,
        source: SourceKnowledgePoint,
        role: str,
        extension_keys: list[str],
    ) -> str:
        if role == LearningRole.EXTEND:
            return f"Extend {knowledge.title} for {', '.join(extension_keys)}"
        if role == LearningRole.APPLY:
            return f"Apply {knowledge.title} in {source.context_title}"
        if role == LearningRole.RECALL:
            return f"Recall the minimum prior context for {knowledge.title}"
        return f"{role} {knowledge.title}"


def _has(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value or "").lower()
