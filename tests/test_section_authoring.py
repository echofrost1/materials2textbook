from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from materials2textbook.agents.book_plan_completeness import diagnose_book_plan_completeness
from materials2textbook.domain_config import DomainConfig
from materials2textbook.knowledge_map.evidence_packet import (
    PARTIAL_OBLIGATION,
    SOURCE_GAP,
    build_section_evidence_packet,
)
from materials2textbook.knowledge_map.outline import book_plan_deep_equal, book_plan_fingerprint
from materials2textbook.knowledge_map.section_authoring import (
    BLOCKED_CORE_SOURCE_GAP,
    RENDERED,
    RENDERED_PARTIAL,
    SectionAuthoringError,
    author_section,
    build_section_authoring_brief,
    materialize_section_draft,
)
from materials2textbook.knowledge_map.teaching_blueprint import build_section_teaching_blueprint
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _chunk(chunk_id: str = "chunk-1", content: str | None = None) -> EvidenceChunk:
    content = content or (
        "Explain the basic operation and quality conditions. Complete the basic operation and verify the result. "
        "Assess the observable operation and quality judgment. Observe the operation and its result. "
        "Apply the operation to a bounded situation and practice the task."
    )
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=f"asset-{chunk_id}",
        title="Basic operation",
        content=content,
        summary=content,
        keywords=["basic", "operation", "procedure", "quality", "result", "task"],
        subject="Basic operation",
        material_block="block-1",
        material_block_code="B1",
        recommended_chapter="Basic operation",
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )


def _plan(*, needs_case: bool = False, needs_exercises: bool = False, primary: list[str] | None = None) -> BookPlan:
    section = BookSectionPlan(
        section_id="section-01",
        section_no="1.1",
        title="Basic operation",
        knowledge_point_ids=["basic operation"],
        primary_material_ids=list(primary or ["chunk-1"]),
        needs_case=needs_case,
        needs_exercises=needs_exercises,
        section_purpose="Explain the basic operation and its quality conditions.",
        expected_learning_outcome="Explain the basic operation and identify quality conditions.",
        current_task_action="Complete the basic operation and verify the result.",
        knowledge_scope=["basic operation"],
        case_purpose="Apply the operation to a bounded situation." if needs_case else "",
        exercise_purpose="Practice the operation in the task." if needs_exercises else "",
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


def _inputs(
    *,
    plan: BookPlan | None = None,
    chunks: list[EvidenceChunk] | None = None,
    authorized: list[str] | None = None,
    constraints: list[dict[str, object]] | None = None,
):
    plan = plan or _plan()
    chunks = chunks or [_chunk()]
    report = diagnose_book_plan_completeness(
        plan,
        chunks,
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    assert report.safe_to_freeze is True, report.counts
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=authorized if authorized is not None else ["chunk-1"],
    )
    packet = build_section_evidence_packet(plan, "section-01", blueprint, chunks)
    brief = build_section_authoring_brief(
        plan,
        "section-01",
        blueprint,
        packet,
        occurrence_constraints=constraints,
        prior_verified_support={"basic operation": {"EXPLAIN": ["occ-prior"]}},
    )
    return plan, blueprint, packet, brief


def _draft(brief, *, body: str = "The basic operation follows the documented procedure and checks the result.", **extra):
    spans = {}
    evidence_usage = []
    for obligation in brief.required_obligations:
        allowed = brief.authorized_evidence_per_obligation.get(obligation.obligation_id, ())
        if not allowed:
            continue
        spans[obligation.obligation_id] = [
            {
                "span_id": f"span-{obligation.obligation_id}",
                "text": body,
                "evidence_ids": [allowed[0]],
            }
        ]
    draft = {
        "body": body,
        "obligation_span_map": spans,
        "occurrence_span_map": {},
        "evidence_usage": evidence_usage,
        "generation_provenance": {"writer": "fake-writer", "model": "fixture"},
    }
    draft.update(extra)
    return draft


def test_section_brief_compiles_obligations_and_occurrence_constraints() -> None:
    plan, blueprint, packet, brief = _inputs(
        constraints=[
            {
                "occurrence_id": "occ-1",
                "role": "APPLY",
                "already_available_facets": ["EXPLAIN"],
                "must_not_reteach_facets": ["EXPLAIN"],
                "contribution_goal": "Use the operation in the current task.",
            }
        ]
    )

    assert brief.outline_node_id == "section-01"
    assert brief.required_obligations
    assert brief.authorized_evidence_per_obligation
    assert "EXPLAIN" in brief.may_recap
    assert "EXPLAIN" in brief.forbidden_reteach
    assert "Use the operation in the current task." in brief.required_current_contribution
    assert brief.provenance["writer_may_replan"] is False
    assert book_plan_deep_equal(plan, _plan())
    assert blueprint.blueprint_id == packet.blueprint_id


def test_fake_writer_materializes_one_coherent_body_and_maps_obligations_occurrences_and_evidence() -> None:
    plan, _, packet, brief = _inputs(
        constraints=[{"occurrence_id": "occ-1", "role": "TEACH"}]
    )
    body = "The basic operation follows the documented procedure and checks the result."
    obligation_map = _draft(brief, body=body)["obligation_span_map"]
    draft = _draft(
        brief,
        body=body,
        occurrence_span_map={"occ-1": [{"span_id": "occ-span-1", "text": body}]},
        obligation_span_map=obligation_map,
    )
    rendered = author_section(brief, packet, lambda _brief: draft)

    assert rendered.render_status in {RENDERED, RENDERED_PARTIAL}
    assert rendered.student_visible_body == body
    assert rendered.obligation_span_map
    assert rendered.occurrence_span_map["occ-1"][0]["text"] == body
    assert rendered.evidence_usage
    assert rendered.generation_provenance["writer"] == "fake-writer"
    assert book_plan_deep_equal(plan, _plan())


def test_case_exercise_assessment_and_summary_requirements_are_not_silent() -> None:
    plan, _, packet, brief = _inputs(plan=_plan(needs_case=True, needs_exercises=True))
    assert brief.case_activity_requirement == "REQUIRED"
    assert brief.exercise_requirement == "REQUIRED"
    assert brief.assessment_requirement == "REQUIRED"
    assert brief.summary_requirement == "OPTIONAL"
    body = "The basic operation follows the documented procedure and checks the result."
    draft = _draft(
        brief,
        body=body,
        case_activity="Apply the operation to the bounded situation.",
        exercises=["Practice the operation and check the result."],
        assessment="Assess the operation and the quality result.",
        summary="The learner can complete and verify the operation.",
    )
    rendered = author_section(brief, packet, lambda _brief: draft)
    assert rendered.case_activity
    assert rendered.exercises
    assert rendered.assessment
    assert rendered.summary


def test_partial_obligation_can_render_only_authorized_supported_portion() -> None:
    plan = _plan()
    chunks = [_chunk("chunk-1", "basic operation")]
    plan, _, packet, brief = _inputs(plan=plan, chunks=chunks)
    partial_ids = {
        item.obligation_id
        for item in brief.required_obligations
        if item.evidence_status == PARTIAL_OBLIGATION
    }
    assert partial_ids
    body = "The basic operation is introduced with the supported evidence."
    rendered = materialize_section_draft(brief, packet, _draft(brief, body=body))
    assert rendered.render_status in {RENDERED, RENDERED_PARTIAL}
    assert rendered.blocked is False


def test_local_source_gap_does_not_erase_supported_section_content() -> None:
    plan = _plan()
    chunks = [_chunk("chunk-1", "Explain the basic operation and complete the basic operation.")]
    plan, _, packet, brief = _inputs(plan=plan, chunks=chunks)
    assert any(item.source_gap for item in brief.required_obligations)
    body = "The supported part of the basic operation is explained and can be completed."
    draft = _draft(brief, body=body)
    rendered = materialize_section_draft(brief, packet, draft)
    assert rendered.body
    assert rendered.render_status == RENDERED_PARTIAL
    assert rendered.blocked is False
    assert any(reason.startswith("SOURCE_GAP:") for reason in rendered.block_reasons)


def test_core_source_gap_blocks_section_without_model_common_sense() -> None:
    plan = _plan()
    plan, blueprint, packet, brief = _inputs(plan=plan, chunks=[], authorized=[])
    assert all(item.status == SOURCE_GAP for item in packet.obligation_bindings)
    rendered = materialize_section_draft(brief, packet, {"body": "", "obligation_span_map": {}})
    assert rendered.render_status == BLOCKED_CORE_SOURCE_GAP
    assert rendered.blocked is True
    assert not rendered.body


def test_internal_labels_and_forbidden_reteach_are_rejected() -> None:
    plan, _, packet, brief = _inputs(
        constraints=[{"occurrence_id": "occ-1", "role": "TEACH", "forbidden_content": ["complete definition"]}]
    )
    with pytest.raises(SectionAuthoringError, match="internal semantic label"):
        materialize_section_draft(brief, packet, _draft(brief, body="This section teaches EXPLAIN."))
    with pytest.raises(SectionAuthoringError, match="forbidden reteach"):
        materialize_section_draft(brief, packet, _draft(brief, body="The complete definition is repeated here."))


def test_unauthorized_evidence_and_unknown_span_mapping_are_rejected() -> None:
    plan, _, packet, brief = _inputs()
    body = "The basic operation follows the documented procedure and checks the result."
    first_obligation = brief.required_obligations[0].obligation_id
    unauthorized = _draft(
        brief,
        body=body,
        obligation_span_map={
            first_obligation: [{"text": body, "evidence_ids": ["not-authorized"]}]
        },
    )
    with pytest.raises(SectionAuthoringError, match="unauthorized evidence"):
        materialize_section_draft(brief, packet, unauthorized)

    unknown = _draft(
        brief,
        body=body,
        occurrence_span_map={"unknown-occurrence": [{"text": body}]},
    )
    with pytest.raises(SectionAuthoringError, match="unknown occurrences"):
        materialize_section_draft(brief, packet, unknown)


def test_bookplan_blueprint_and_packet_remain_immutable() -> None:
    plan, blueprint, packet, brief = _inputs()
    before = deepcopy(plan)
    assert book_plan_fingerprint(plan) == brief.source_book_plan_signature
    assert book_plan_deep_equal(plan, before)
    with pytest.raises(FrozenInstanceError):
        brief.section_title = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        packet.packet_id = "changed"  # type: ignore[misc]
    assert book_plan_deep_equal(plan, before)
