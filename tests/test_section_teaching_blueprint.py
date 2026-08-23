from __future__ import annotations

from copy import deepcopy

import pytest

from materials2textbook.agents.book_plan_completeness import diagnose_book_plan_completeness
from materials2textbook.domain_config import DomainConfig
from materials2textbook.knowledge_map.models import PlannedOccurrence
from materials2textbook.knowledge_map.outline import book_plan_deep_equal, book_plan_fingerprint
from materials2textbook.knowledge_map.teaching_blueprint import (
    ASSESSMENT_EXERCISE,
    CASE_ACTIVITY,
    CONCEPT_PRINCIPLE,
    NOT_APPLICABLE,
    OPTIONAL,
    PROCEDURE_OPERATION,
    REQUIRED,
    SectionTeachingBlueprintError,
    build_section_teaching_blueprint,
    validate_section_teaching_blueprint,
)
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _chunk(chunk_id: str = "chunk-1") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=f"asset-{chunk_id}",
        title="Basic operation",
        content="Evidence about the basic operation, its procedure, conditions, and quality result.",
        summary="Basic operation procedure and quality result",
        keywords=["basic", "operation", "procedure", "quality"],
        subject="Basic operation",
        material_block="block-1",
        material_block_code="B1",
        recommended_chapter="Basic operation",
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )


def _section(
    *,
    section_id: str = "section-01",
    title: str = "Basic operation",
    needs_case: bool = False,
    needs_exercises: bool = False,
    primary: list[str] | None = None,
    complete: bool = True,
) -> BookSectionPlan:
    values = {}
    if complete:
        values = {
            "section_purpose": "Build an understanding of the operation and its quality conditions.",
            "expected_learning_outcome": "Explain the operation and identify its quality conditions.",
            "current_task_action": "Inspect the setup and complete the operation in the task.",
            "knowledge_scope": ["basic operation"],
            "case_purpose": "Apply the operation to a bounded work situation." if needs_case else "",
            "exercise_purpose": "Practice identifying and performing the operation." if needs_exercises else "",
            "assessment_purpose": "Assess the observable operation and quality judgment.",
            "activity_purpose": "Observe and discuss the operation and its result.",
        }
    return BookSectionPlan(
        section_id=section_id,
        section_no="1.1",
        title=title,
        knowledge_point_ids=["basic operation"],
        primary_material_ids=list(primary or []),
        needs_case=needs_case,
        needs_exercises=needs_exercises,
        **values,
    )


def _plan(section: BookSectionPlan | None = None) -> BookPlan:
    selected = section or _section(primary=["chunk-1"])
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
                sections=[selected],
                primary_material_ids=["chunk-1"],
            )
        ],
    )


def _valid_inputs(section: BookSectionPlan | None = None):
    plan = _plan(section or _section(primary=["chunk-1"]))
    report = diagnose_book_plan_completeness(
        plan,
        [_chunk()],
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    assert report.safe_to_freeze is True
    return plan, report


def test_blueprint_builds_from_valid_frozen_fixture_without_plan_mutation() -> None:
    plan, report = _valid_inputs()
    before = deepcopy(plan)
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=["chunk-1"],
    )

    assert blueprint.outline_node_id == "section-01"
    assert blueprint.source_book_plan_signature == book_plan_fingerprint(plan)
    assert blueprint.source_gaps == ()
    assert blueprint.authorized_evidence_ids == ("chunk-1",)
    assert any(item.kind == CONCEPT_PRINCIPLE and item.required == REQUIRED for item in blueprint.obligations)
    assert any(item.kind == PROCEDURE_OPERATION and item.required == REQUIRED for item in blueprint.obligations)
    assert validate_section_teaching_blueprint(blueprint) == []
    assert book_plan_deep_equal(plan, before)


def test_blueprint_keeps_occurrence_links_and_prior_verified_support_as_overlay() -> None:
    plan, report = _valid_inputs()
    occurrence = PlannedOccurrence(
        occurrence_id="occ:section-01:kp:01:01",
        knowledge_id="knowledge-basic-operation",
        source_knowledge_point_id="section-01:kp:01",
        position=None,  # type: ignore[arg-type]
        chapter_id="chapter-01",
        section_id="section-01",
        context_title="Basic operation",
        source_chunk_ids=["chunk-1"],
        role="TEACH",
    )
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        prior_verified_support={"basic operation": {"EXPLAIN": ["occ-prior"]}},
        occurrences=[occurrence, {"occurrence_id": "other", "section_id": "other-section"}],
        authorized_evidence_ids=["chunk-1"],
    )

    assert blueprint.prior_verified_support["basic operation"]["EXPLAIN"] == ["occ-prior"]
    assert blueprint.obligations
    assert all("occ:section-01:kp:01:01" in item.linked_occurrence_ids for item in blueprint.obligations)
    assert all("other" not in item.linked_occurrence_ids for item in blueprint.obligations)
    assert all(item.target_knowledge_ids == ("basic operation",) for item in blueprint.obligations)


def test_module_intents_create_case_and_exercise_obligations_only_when_requested() -> None:
    plan, report = _valid_inputs(
        _section(needs_case=True, needs_exercises=True, primary=["chunk-1"])
    )
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=["chunk-1"],
    )
    kinds = [item.kind for item in blueprint.obligations]
    assert CASE_ACTIVITY in kinds
    assert ASSESSMENT_EXERCISE in kinds
    assert all(item.required in {REQUIRED, OPTIONAL, NOT_APPLICABLE} for item in blueprint.obligations)

    no_modules_plan, no_modules_report = _valid_inputs(_section(primary=["chunk-1"]))
    no_modules = build_section_teaching_blueprint(
        no_modules_plan,
        "section-01",
        completeness_report=no_modules_report,
        source_book_plan_signature=book_plan_fingerprint(no_modules_plan),
        authorized_evidence_ids=["chunk-1"],
    )
    assert not any(item.kind == CASE_ACTIVITY and item.required == REQUIRED for item in no_modules.obligations)


def test_missing_authorized_evidence_is_explicit_source_gap() -> None:
    plan, report = _valid_inputs()
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=[],
    )

    assert blueprint.source_gaps
    assert all(item["issue_type"] == "SOURCE_GAP" for item in blueprint.source_gaps)
    assert all(item["status"] == "EVIDENCE_REQUIRED" for item in blueprint.source_gaps)


def test_blueprint_cannot_expand_frozen_section_evidence_ownership() -> None:
    plan, report = _valid_inputs()
    with pytest.raises(SectionTeachingBlueprintError, match="exceeds the section's frozen BookPlan ownership"):
        build_section_teaching_blueprint(
            plan,
            "section-01",
            completeness_report=report,
            source_book_plan_signature=book_plan_fingerprint(plan),
            authorized_evidence_ids=["chunk-not-owned-by-section"],
        )


def test_unfrozen_plan_or_mismatched_report_is_rejected() -> None:
    incomplete_plan = _plan(_section(primary=["chunk-1"], complete=False))
    incomplete_report = diagnose_book_plan_completeness(
        incomplete_plan,
        [_chunk()],
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    assert incomplete_report.safe_to_freeze is False
    with pytest.raises(SectionTeachingBlueprintError, match="completeness freeze gate"):
        build_section_teaching_blueprint(
            incomplete_plan,
            "section-01",
            completeness_report=incomplete_report,
            source_book_plan_signature=book_plan_fingerprint(incomplete_plan),
            authorized_evidence_ids=["chunk-1"],
        )

    valid_plan, valid_report = _valid_inputs()
    with pytest.raises(SectionTeachingBlueprintError, match="fingerprint"):
        build_section_teaching_blueprint(
            valid_plan,
            "section-01",
            completeness_report=valid_report,
            source_book_plan_signature="wrong-signature",
            authorized_evidence_ids=["chunk-1"],
        )


def test_blueprint_does_not_mechanically_emit_irrelevant_obligations() -> None:
    section = _section(primary=["chunk-1"], complete=True)
    plan = _plan(section)
    report = diagnose_book_plan_completeness(
        plan,
        [_chunk()],
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    assert report.safe_to_freeze is True
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=["chunk-1"],
    )

    assert not any(item.kind == CASE_ACTIVITY and item.required == REQUIRED for item in blueprint.obligations)
