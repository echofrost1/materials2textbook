from __future__ import annotations

from dataclasses import replace

from materials2textbook.agents.book_plan_completeness import diagnose_book_plan_completeness
from materials2textbook.domain_config import DomainConfig
from materials2textbook.knowledge_map.execution import execute_verified_sections
from materials2textbook.knowledge_map.models import (
    BookPosition,
    KnowledgeMap,
    KnowledgePoint,
    PlannedOccurrence,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.semantic_evaluation import SemanticPlanningEvaluation
from materials2textbook.knowledge_map.outline import book_plan_deep_equal, book_plan_fingerprint
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _fixture():
    text = (
        "The setup procedure inspects the cable and connector before operation. "
        "Connect the equipment and verify that the connection is secure. "
        "Observe the result and judge the quality condition."
    )
    chunk = EvidenceChunk(
        chunk_id="e1",
        asset_id="a1",
        title="Equipment setup",
        content=text,
        summary=text,
        keywords=["setup", "procedure", "connector", "verify", "quality"],
        subject="equipment",
        material_block="b1",
        material_block_code="B1",
        recommended_chapter="Equipment",
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )
    section = BookSectionPlan(
        section_id="section-1",
        section_no="1.1",
        title="Equipment setup",
        knowledge_point_ids=["equipment setup"],
        primary_material_ids=["e1"],
        section_purpose="Explain the equipment setup procedure.",
        expected_learning_outcome="Complete the setup and verify the connection.",
        current_task_action="Inspect, connect, and verify the setup.",
        knowledge_scope=["equipment setup"],
        assessment_purpose="Assess the setup and verification.",
        activity_purpose="Observe the setup result.",
        needs_case=False,
        needs_exercises=False,
    )
    plan = BookPlan(
        book_id="book-1",
        title="Equipment setup",
        planning_strategy="fixture",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter-1",
                chapter_no=1,
                title="Equipment setup",
                learning_goals=["Complete and verify equipment setup."],
                sections=[section],
                primary_material_ids=["e1"],
            )
        ],
    )
    source = SourceKnowledgePoint(
        source_knowledge_point_id="source-1",
        title="Equipment setup",
        chapter_id="chapter-1",
        section_id="section-1",
        chapter_ordinal=1,
        section_ordinal=1,
        task_ordinal=1,
        source_point_ordinal=1,
        context_title="Equipment setup",
        source_chunk_ids=["e1"],
    )
    point = KnowledgePoint(
        knowledge_id="k-1",
        title="Equipment setup",
        aliases=[],
        kind="PROCEDURE",
        source_chunk_ids=["e1"],
    )
    occurrence = PlannedOccurrence(
        occurrence_id="occ-1",
        knowledge_id="k-1",
        source_knowledge_point_id="source-1",
        position=BookPosition(1, 1, 1, 1, 1),
        chapter_id="chapter-1",
        section_id="section-1",
        context_title="Equipment setup",
        source_chunk_ids=["e1"],
        role="TEACH",
        intended_grants=["EXPLAIN", "PERFORM"],
        intended_contribution="Complete and verify the equipment setup.",
        planning_confidence=1.0,
        trusted_for_state=True,
    )
    delta = SemanticDelta(
        occurrence_id="occ-1",
        repeats_prior_explanation=False,
        uses_prior_knowledge=False,
        recall_needed=False,
        required_self_facets=[],
        required_self_extension_keys=[],
        cross_prerequisite_uses=[],
        new_facets=["EXPLAIN", "PERFORM"],
        new_extension_keys=[],
        new_context="",
        repeated_aspects=[],
        contribution_summary="Complete and verify the equipment setup.",
        confidence=1.0,
        rationale="fixture",
        evidence_chunk_ids=["e1"],
    )
    knowledge_map = KnowledgeMap(
        title=plan.title,
        outline_signature="",
        knowledge_points=[point],
        source_knowledge_points=[source],
        mappings=[],
        prerequisites=[],
        planned_occurrences=[occurrence],
        trajectories=[],
        availability_snapshots=[],
        validation_issues=[],
    )
    report = diagnose_book_plan_completeness(
        plan,
        [chunk],
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Equipment setup"]),
    )
    return plan, chunk, source, point, occurrence, delta, knowledge_map, report


def test_section_execution_grants_only_after_materialized_local_verification() -> None:
    plan, chunk, source, point, occurrence, delta, knowledge_map, report = _fixture()
    before = replace(plan)
    evaluation = SemanticPlanningEvaluation(knowledge_map=knowledge_map, semantic_deltas=[delta])

    def writer(brief, packet, chunks):
        obligation_ids = [item.obligation_id for item in brief.required_obligations]
        # Section writers cite packet-local aliases; materialization resolves
        # E1 back to the authorized chunk deterministically.
        evidence_id = "E1"
        return {
            "blocks": [
                {
                    "block_id": "b1",
                    "channel": "body",
                    "text": "The explanation defines the equipment setup. The steps show how to perform the setup and verify the connection. Complete the equipment setup and verify the connection.",
                    "intended_obligation_ids": obligation_ids,
                    "intended_occurrence_ids": ["occ-1"],
                    "evidence_ids": [evidence_id],
                }
            ],
            "generation_provenance": {"writer": "fixture-section-writer"},
        }

    class Judge:
        call_count = 0
        model = "fixture-judge"

        def judge(self, *, claim, evidence, context):
            self.call_count += 1
            return {
                "status": "SUPPORTED",
                "supporting_evidence_ids": [item["evidence_id"] for item in evidence],
                "rationale": "fixture evidence directly supports the claim",
                "confidence": 1.0,
            }

    result = execute_verified_sections(
        book_plan=plan,
        completeness_report=report,
        occurrences=[occurrence],
        deltas=[delta],
        sources={source.source_knowledge_point_id: source},
        points={point.knowledge_id: point},
        chunks=[chunk],
        section_writer=writer,
        semantic_entailment_judge=Judge(),
    )
    assert result.section_assemblies[0]["status"] in {"RENDERED", "RENDERED_PARTIAL"}
    assert result.markdown_occurrences[0]["section_authoring"] is True
    assert result.transitions[-1]["grant_applied"] is True
    assert result.verified_state.availability_by_knowledge["k-1"].available_facets
    assert isinstance(result.section_assemblies[0]["recovery"], dict)
    assert result.section_assemblies[0]["recovery"]["auto_applied"] is False
    assert book_plan_deep_equal(plan, before)


def test_section_execution_does_not_use_rule_fallback_when_writer_is_unavailable() -> None:
    plan, chunk, source, point, occurrence, delta, knowledge_map, report = _fixture()
    evaluation = SemanticPlanningEvaluation(knowledge_map=knowledge_map, semantic_deltas=[delta])
    result = execute_verified_sections(
        book_plan=plan,
        completeness_report=report,
        occurrences=[occurrence],
        deltas=[delta],
        sources={source.source_knowledge_point_id: source},
        points={point.knowledge_id: point},
        chunks=[chunk],
        section_writer=lambda *_: (_ for _ in ()).throw(RuntimeError("writer unavailable")),
    )
    assert result.verified_state.availability_by_knowledge == {}
    assert result.blocked_occurrences[0]["issue_code"] == "SECTION_WRITER_FAILED"
