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
    frontier = result.authoring_frontier
    assert len(frontier) == 1
    assert frontier[0]["frontier_status"] == "AUTHORING_FRONTIER"
    assert frontier[0]["authoring_status"] == "VERIFIED_GRANT"
    assert frontier[0]["occurrence_id"] == "occ-1"
    assert frontier[0]["grant_applied"] is True
    assert "compilation_audit" in frontier[0]


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


def test_section_execution_applies_local_claim_contraction_before_grant() -> None:
    plan, chunk, source, point, occurrence, delta, _knowledge_map, report = _fixture()

    def writer(brief, packet, chunks):
        obligation_ids = [item.obligation_id for item in brief.required_obligations]
        return {
            "blocks": [{
                "block_id": "b1",
                "channel": "body",
                "text": (
                    "The explanation defines the equipment setup. The steps show how to perform "
                    "the setup and verify the connection. This operation always guarantees every result."
                ),
                "intended_obligation_ids": obligation_ids,
                "intended_occurrence_ids": ["occ-1"],
                "evidence_ids": ["E1"],
            }],
            "generation_provenance": {"writer": "fixture-overreach-writer"},
        }

    class Judge:
        call_count = 0
        model = "fixture-judge"

        def judge(self, *, claim, evidence, context):
            self.call_count += 1
            if "always guarantees" in claim.lower():
                return {
                    "status": "UNSUPPORTED",
                    "supporting_evidence_ids": [],
                    "rationale": "The authorized evidence does not support an absolute guarantee.",
                    "confidence": 1.0,
                    "unsupported_part": claim,
                }
            return {
                "status": "SUPPORTED",
                "supporting_evidence_ids": [item["evidence_id"] for item in evidence],
                "rationale": "The fixture evidence supports the bounded claim.",
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
    recovery = result.section_assemblies[0]["recovery"]
    assert recovery["proposals"]
    assert recovery["auto_applied"] is True
    assert any(item["status"] == "ACCEPTED" for item in recovery["attempts"])
    assert result.transitions[-1]["grant_applied"] is True


def test_section_execution_accepts_block_local_minimal_supported_rewrite() -> None:
    plan, chunk, source, point, occurrence, delta, _knowledge_map, report = _fixture()

    def writer(brief, packet, chunks):
        return {
            "blocks": [{
                "block_id": "b1",
                "channel": "body",
                "text": (
                    "The explanation defines the equipment setup. "
                    "This operation always guarantees every result."
                ),
                "intended_obligation_ids": [item.obligation_id for item in brief.required_obligations],
                "intended_occurrence_ids": ["occ-1"],
                "evidence_ids": ["E1"],
            }],
            "generation_provenance": {"writer": "fixture-overreach-writer"},
        }

    class Judge:
        call_count = 0
        model = "fixture-judge"

        def judge(self, *, claim, evidence, context):
            self.call_count += 1
            if "always guarantees" in claim.lower():
                return {
                    "status": "UNSUPPORTED",
                    "supporting_evidence_ids": [],
                    "rationale": "The authorized evidence does not support an absolute guarantee.",
                    "confidence": 1.0,
                    "unsupported_part": claim,
                }
            return {
                "status": "SUPPORTED",
                "supporting_evidence_ids": [item["evidence_id"] for item in evidence],
                "rationale": "The fixture evidence supports the bounded claim.",
                "confidence": 1.0,
            }

    def local_rewrite(proposal, brief, packet, draft, chunks):
        assert proposal.block_id == "b1"
        assert proposal.allowed_evidence_ids == ("e1",)
        assert brief.packet_id == packet.packet_id
        assert draft["blocks"][0]["evidence_ids"] == ["E1"]
        assert [item.chunk_id for item in chunks] == ["e1"]
        return "The explanation defines the equipment setup and its verification steps."

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
        local_recovery_writer=local_rewrite,
    )
    recovery = result.section_assemblies[0]["recovery"]
    assert recovery["auto_applied"] is True
    assert recovery["attempts"][0]["action"] == "MINIMAL_SUPPORTED_REWRITE"
    assert recovery["attempts"][0]["status"] == "ACCEPTED"
    assert recovery["issue_count"] == len(recovery["issues"])
    assert recovery["proposal_count"] == len(recovery["proposals"])
    assert recovery["executed_attempts"] == len(recovery["attempts"])
    assert recovery["terminal_attempts"] == recovery["executed_attempts"]
    assert recovery["recovery_terminal_state_accounting_valid"] is True
    assert sum(recovery["terminal_state_counts"].values()) == recovery["terminal_attempts"]
    assert result.transitions[-1]["grant_applied"] is True


def test_section_conformance_checks_all_occurrence_spans() -> None:
    plan, chunk, source, point, occurrence, delta, _knowledge_map, report = _fixture()

    def writer(brief, packet, chunks):
        obligation_ids = [item.obligation_id for item in brief.required_obligations]
        return {
            "blocks": [
                {
                    "block_id": "b1",
                    "channel": "body",
                    "text": "The explanation defines the equipment setup.",
                    "intended_obligation_ids": obligation_ids,
                    "intended_occurrence_ids": ["occ-1"],
                    "evidence_ids": ["E1"],
                },
                {
                    "block_id": "b2",
                    "channel": "body",
                    "text": "The steps complete the equipment setup, connect it, and verify the connection result.",
                    "intended_obligation_ids": obligation_ids,
                    "intended_occurrence_ids": ["occ-1"],
                    "evidence_ids": ["E1"],
                },
            ],
            "generation_provenance": {"writer": "fixture-multi-span-writer"},
        }

    class Judge:
        call_count = 0
        model = "fixture-judge"

        def judge(self, *, claim, evidence, context):
            self.call_count += 1
            return {
                "status": "SUPPORTED",
                "supporting_evidence_ids": [item["evidence_id"] for item in evidence],
                "rationale": "fixture evidence supports both mapped spans",
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
    assert result.transitions[-1]["grant_applied"] is True


def test_section_packet_evidence_reaches_occurrence_local_claim_audit() -> None:
    """Section-owned evidence must drive the sequential occurrence audit.

    A source-bounded section packet can authorize a chunk even when the
    occurrence's planning delta did not carry that chunk ID.  The final
    occurrence-local claim audit must consume the materialized packet span,
    without widening the evidence scope.
    """
    plan, chunk, source, point, occurrence, delta, _knowledge_map, report = _fixture()
    # The frozen occurrence can carry broader retrieval context than the
    # section packet.  Runtime auditing must remain bounded to the mapped
    # packet span rather than unioning this unrelated chunk back in.
    unrelated = replace(chunk, chunk_id="e2", content="Unrelated retrieval context")
    occurrence = replace(occurrence, source_chunk_ids=["e2"])
    delta = replace(delta, evidence_chunk_ids=["e2"])

    def writer(brief, packet, chunks):
        obligation_ids = [item.obligation_id for item in brief.required_obligations]
        return {
            "blocks": [{
                "block_id": "b1",
                "channel": "body",
                "text": (
                    "The explanation defines the equipment setup. The steps show how to perform "
                    "the setup and verify the connection. Complete the equipment setup and verify "
                    "the connection."
                ),
                "intended_obligation_ids": obligation_ids,
                "intended_occurrence_ids": ["occ-1"],
                "evidence_ids": ["E1"],
            }],
            "generation_provenance": {"writer": "fixture-packet-evidence-writer"},
        }

    class Judge:
        call_count = 0
        model = "fixture-judge"

        def judge(self, *, claim, evidence, context):
            self.call_count += 1
            assert evidence, "materialized packet evidence must reach occurrence audit"
            assert {item["evidence_id"] for item in evidence} == {"e1"}
            return {
                "status": "SUPPORTED",
                "supporting_evidence_ids": ["e1"],
                "rationale": "packet-authorized evidence supports the claim",
                "confidence": 1.0,
            }

    result = execute_verified_sections(
        book_plan=plan,
        completeness_report=report,
        occurrences=[occurrence],
        deltas=[delta],
        sources={source.source_knowledge_point_id: source},
        points={point.knowledge_id: point},
        chunks=[chunk, unrelated],
        section_writer=writer,
        semantic_entailment_judge=Judge(),
    )
    assert result.transitions[-1]["grant_applied"] is True


def test_single_occurrence_body_without_model_association_uses_linked_packet_scope() -> None:
    """The existing sole-occurrence mapping remains packet-bounded."""
    plan, chunk, source, point, occurrence, delta, _knowledge_map, report = _fixture()
    occurrence = replace(occurrence, source_chunk_ids=[])
    delta = replace(delta, evidence_chunk_ids=[])

    def writer(brief, packet, chunks):
        return {
            "blocks": [{
                "block_id": "b1",
                "channel": "body",
                "text": (
                    "The explanation defines the equipment setup. The steps show how to perform "
                    "the setup and verify the connection. Complete the equipment setup and verify "
                    "the connection."
                ),
                # The deterministic one-occurrence mapping owns this block;
                # there is no section-wide evidence fallback in the audit.
                "intended_obligation_ids": [],
                "intended_occurrence_ids": [],
                "evidence_ids": [],
            }],
            "generation_provenance": {"writer": "fixture-unassociated-body-writer"},
        }

    class Judge:
        call_count = 0
        model = "fixture-judge"

        def judge(self, *, claim, evidence, context):
            self.call_count += 1
            assert {item["evidence_id"] for item in evidence} == {"e1"}
            return {
                "status": "SUPPORTED",
                "supporting_evidence_ids": ["e1"],
                "rationale": "linked obligation evidence supports the claim",
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
    assert result.transitions[-1]["grant_applied"] is True
