from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from materials2textbook.agents.book_plan_completeness import diagnose_book_plan_completeness
from materials2textbook.domain_config import DomainConfig
from materials2textbook.knowledge_map.evidence_packet import (
    PARTIAL_OBLIGATION,
    SOURCE_GAP,
    SUPPORTED_OBLIGATION,
    SectionEvidencePacketError,
    build_section_evidence_packet,
    validate_section_evidence_packet,
)
from materials2textbook.knowledge_map.outline import book_plan_deep_equal, book_plan_fingerprint
from materials2textbook.knowledge_map.teaching_blueprint import build_section_teaching_blueprint
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _chunk(chunk_id: str, content: str, *, title: str = "Basic operation") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=f"asset-{chunk_id}",
        title=title,
        content=content,
        summary=content,
        keywords=["basic", "operation", "procedure", "quality"],
        subject=title,
        material_block="block-1",
        material_block_code="B1",
        recommended_chapter="Basic operation",
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )


def _plan(*, primary: list[str] | None = None, reference: list[str] | None = None) -> BookPlan:
    section = BookSectionPlan(
        section_id="section-01",
        section_no="1.1",
        title="Basic operation",
        knowledge_point_ids=["basic operation"],
        primary_material_ids=list(primary or ["chunk-1"]),
        reference_material_ids=list(reference or []),
        needs_case=False,
        needs_exercises=False,
        section_purpose="Explain the basic operation and its quality conditions.",
        expected_learning_outcome="Explain the basic operation and identify quality conditions.",
        current_task_action="Complete the basic operation and verify the result.",
        knowledge_scope=["basic operation"],
        assessment_purpose="Assess the observable operation and quality judgment.",
        activity_purpose="Observe the operation and its result.",
    )
    return BookPlan(
        book_id="book-1",
        title="Basic operation",
        planning_strategy="rule",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter-01",
                chapter_no=1,
                title="Basic operation",
                learning_goals=["Explain and perform the basic operation."],
                sections=[section],
                primary_material_ids=list(primary or ["chunk-1"]),
            )
        ],
    )


def _blueprint(plan: BookPlan, *, authorized: list[str] | None = None):
    chunks = [
        _chunk(
            "chunk-1",
            "Explain basic operation and quality conditions. Complete basic operation and verify the result. "
            "Assess observable operation and quality judgment. Observe the operation and its result.",
        )
    ]
    if "chunk-2" in plan.chapters[0].sections[0].reference_material_ids:
        chunks.append(_chunk("chunk-2", "Basic operation quality result and conditions."))
    report = diagnose_book_plan_completeness(
        plan,
        chunks,
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    assert report.safe_to_freeze is True
    return build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=authorized,
    )


def test_packet_covers_every_required_obligation_with_a_bounded_status() -> None:
    plan = _plan()
    blueprint = _blueprint(plan, authorized=["chunk-1"])
    before_plan = deepcopy(plan)
    chunks = [_chunk("chunk-1", "Basic operation procedure and quality result.")]

    packet = build_section_evidence_packet(plan, "section-01", blueprint, chunks)
    required = {item.obligation_id for item in blueprint.obligations if item.required == "REQUIRED"}
    actual = {item.obligation_id for item in packet.obligation_bindings}

    assert required <= actual
    assert all(item.status in {SUPPORTED_OBLIGATION, PARTIAL_OBLIGATION, SOURCE_GAP} for item in packet.obligation_bindings)
    assert packet.authorized_primary_evidence_ids == ("chunk-1",)
    assert packet.authorized_reference_evidence_ids == ()
    assert validate_section_evidence_packet(packet, blueprint, expected_source_signature=book_plan_fingerprint(plan)) == []
    assert book_plan_deep_equal(plan, before_plan)


def test_packet_keeps_primary_and_reference_ownership_separate() -> None:
    plan = _plan(primary=["chunk-1"], reference=["chunk-2"])
    blueprint = _blueprint(plan, authorized=["chunk-1", "chunk-2"])
    chunks = [
        _chunk("chunk-1", "Basic operation procedure."),
        _chunk("chunk-2", "Basic operation quality result."),
    ]
    packet = build_section_evidence_packet(plan, "section-01", blueprint, chunks)

    assert packet.authorized_primary_evidence_ids == ("chunk-1",)
    assert packet.authorized_reference_evidence_ids == ("chunk-2",)
    accepted = {
        evidence_id
        for binding in packet.obligation_bindings
        for evidence_id in binding.accepted_evidence_ids
    }
    assert accepted <= {"chunk-1", "chunk-2"}


def test_out_of_scope_candidate_is_rejected_and_never_accepted() -> None:
    plan = _plan(primary=["chunk-1"])
    blueprint = _blueprint(plan, authorized=["chunk-1"])
    chunks = [
        _chunk("chunk-1", "Unrelated material."),
        _chunk("chunk-2", "Basic operation procedure and quality result."),
    ]
    packet = build_section_evidence_packet(plan, "section-01", blueprint, chunks)

    assert packet.retrieval_audit["unauthorized_accepted_evidence_count"] == 0
    assert "chunk-2" in packet.retrieval_audit["out_of_scope_candidate_ids"]
    assert all(
        evidence_id != "chunk-2"
        for binding in packet.obligation_bindings
        for evidence_id in binding.accepted_evidence_ids
    )
    assert any(
        candidate.get("evidence_id") == "chunk-2"
        and candidate.get("reason") == "OUTSIDE_SECTION_OWNERSHIP"
        for binding in packet.obligation_bindings
        for candidate in binding.rejected_candidates
    )


def test_partial_overlap_and_empty_pool_become_auditable_source_results() -> None:
    plan = _plan()
    partial_blueprint = _blueprint(plan, authorized=["chunk-1"])
    partial_packet = build_section_evidence_packet(
        plan,
        "section-01",
        partial_blueprint,
        [_chunk("chunk-1", "Basic operation.")],
    )
    assert any(item.status == PARTIAL_OBLIGATION for item in partial_packet.obligation_bindings)

    empty_blueprint = _blueprint(plan, authorized=[])
    empty_packet = build_section_evidence_packet(plan, "section-01", empty_blueprint, [])
    required_bindings = [item for item in empty_packet.obligation_bindings if item.obligation_id in {
        obligation.obligation_id for obligation in empty_blueprint.obligations if obligation.required == "REQUIRED"
    }]
    assert required_bindings
    assert all(item.status == SOURCE_GAP for item in required_bindings)
    assert len(empty_packet.source_gaps) == len(required_bindings)


def test_packet_rejects_blueprint_from_another_plan_and_preserves_blueprint_immutability() -> None:
    plan = _plan()
    blueprint = _blueprint(plan, authorized=["chunk-1"])
    other_plan = _plan(primary=["different-chunk"])
    with pytest.raises(SectionEvidencePacketError, match="different BookPlan"):
        build_section_evidence_packet(
            other_plan,
            "section-01",
            blueprint,
            [_chunk("chunk-1", "Basic operation procedure.")],
        )

    with pytest.raises(FrozenInstanceError):
        blueprint.outline_node_id = "other"  # type: ignore[misc]


def test_packet_records_duplicate_chunk_ids_and_is_serializable() -> None:
    plan = _plan()
    blueprint = _blueprint(plan, authorized=["chunk-1"])
    packet = build_section_evidence_packet(
        plan,
        "section-01",
        blueprint,
        [
            _chunk("chunk-1", "Basic operation procedure."),
            _chunk("chunk-1", "Duplicate record."),
        ],
    )

    assert packet.retrieval_audit["duplicate_chunk_ids"] == ["chunk-1"]
    payload = packet.to_dict()
    assert payload["packet_id"].startswith("packet:section-01:")
    assert payload["obligation_bindings"]
    assert payload["provenance"]["book_plan_mutated"] is False
