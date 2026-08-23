from __future__ import annotations

from copy import deepcopy

from materials2textbook.agents.book_plan_completeness import (
    EVIDENCE_BINDING_GAP,
    GENUINE_SOURCE_GAP,
    REPLANNABLE_PLAN_GAP,
    diagnose_book_plan_completeness,
    optimize_book_plan_completeness,
)
from materials2textbook.agents.book_plan_llm import book_plan_from_dict
from materials2textbook.domain_config import DomainConfig
from materials2textbook.knowledge_map.outline import book_plan_deep_equal
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _chunk(chunk_id: str, title: str, content: str | None = None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=f"asset-{chunk_id}",
        title=title,
        content=content or f"Evidence about {title}: procedure, condition, and quality judgment.",
        summary=title,
        keywords=title.lower().split(),
        subject=title,
        material_block="block-1",
        material_block_code="B1",
        recommended_chapter=title,
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )


def _section(
    section_id: str = "section-01",
    title: str = "Basic operation",
    *,
    primary: list[str] | None = None,
    reference: list[str] | None = None,
    complete: bool = False,
) -> BookSectionPlan:
    values = {}
    if complete:
        values = {
            "section_purpose": "Build the learner's understanding of the operation in this task.",
            "expected_learning_outcome": "Explain the operation and identify its quality conditions.",
            "current_task_action": "Inspect the setup and complete the operation in the task.",
            "knowledge_scope": ["basic operation"],
            "case_purpose": "Apply the operation to a bounded work situation.",
            "exercise_purpose": "Practice identifying and performing the operation.",
            "assessment_purpose": "Assess the observable operation and quality judgment.",
            "activity_purpose": "Observe and discuss the operation and its result.",
        }
    return BookSectionPlan(
        section_id=section_id,
        section_no="1.1",
        title=title,
        knowledge_point_ids=["basic operation"],
        primary_material_ids=list(primary or []),
        reference_material_ids=list(reference or []),
        **values,
    )


def _plan(*, section: BookSectionPlan | None = None, title: str = "Basic operation") -> BookPlan:
    section = section or _section()
    return BookPlan(
        book_id="book-1",
        title=title,
        planning_strategy="rule",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter-01",
                chapter_no=1,
                title=title,
                learning_goals=["Understand and perform the basic operation."],
                sections=[section],
                primary_material_ids=list(section.primary_material_ids),
            )
        ],
    )


def _config(*topics: str) -> DomainConfig:
    return DomainConfig(domain_name="general topic", chapter_order=list(topics))


def test_complete_book_plan_passes_without_mutation() -> None:
    plan = _plan(section=_section(primary=["chunk-1"], complete=True))
    before = deepcopy(plan)
    report = diagnose_book_plan_completeness(
        plan,
        [_chunk("chunk-1", "Basic operation")],
        domain_config=_config("Basic operation"),
    )

    assert report.status == "PASS"
    assert report.safe_to_freeze is True
    assert book_plan_deep_equal(plan, before)


def test_bounded_completeness_repairs_purposes_and_evidence_pool() -> None:
    plan = _plan(section=_section(primary=["chunk-1"]))
    chunks = [
        _chunk("chunk-1", "Basic operation"),
        _chunk("chunk-2", "Basic operation conditions"),
    ]
    candidate = deepcopy(plan)
    candidate.chapters[0].sections[0] = _section(primary=["chunk-1"], complete=True)

    result = optimize_book_plan_completeness(
        plan,
        chunks,
        domain_config=_config("Basic operation"),
        replanner=lambda _current, _issues: candidate,
        allow_replan=True,
    )
    section = result.book_plan.chapters[0].sections[0]

    assert result.initial_report.status == "REPLAN_REQUIRED"
    assert result.final_report.status == "PASS"
    assert section.section_purpose
    assert section.expected_learning_outcome
    assert section.current_task_action
    assert section.knowledge_scope == ["basic operation"]
    assert section.case_purpose and section.exercise_purpose
    assert section.assessment_purpose and section.activity_purpose
    assert "chunk-1" in section.primary_material_ids
    assert "chunk-2" in (section.primary_material_ids + section.reference_material_ids)
    assert section.knowledge_point_ids == ["basic operation"]
    assert result.book_plan_changed is True


def test_missing_scope_with_authorized_evidence_uses_one_bounded_replan() -> None:
    plan = _plan(section=_section(title="Foundations", primary=["chunk-1"], complete=True), title="Book")
    chunks = [
        _chunk("chunk-1", "Foundations"),
        _chunk("chunk-2", "Advanced operation", "Advanced operation has a distinct procedure and quality condition."),
    ]
    candidate = deepcopy(plan)
    candidate.chapters.append(
        BookChapterPlan(
            chapter_id="chapter-02",
            chapter_no=2,
            title="Advanced operation",
            learning_goals=["Advanced operation outcome"],
            sections=[_section("section-02", "Advanced operation", primary=["chunk-2"], complete=True)],
            primary_material_ids=["chunk-2"],
        )
    )
    calls = []

    def replanner(current, issues):
        calls.append((current, issues))
        return candidate

    result = optimize_book_plan_completeness(
        plan,
        chunks,
        domain_config=_config("Foundations", "Advanced operation"),
        replanner=replanner,
        max_replan_attempts=9,
        allow_replan=True,
    )

    assert len(calls) == 1
    assert result.replan_attempts == 1
    assert result.final_report.status == "PASS"
    assert [chapter.chapter_id for chapter in result.book_plan.chapters] == ["chapter-01", "chapter-02"]
    assert result.book_plan.chapters[0].title == "Book"


def test_no_authorized_support_is_a_genuine_source_gap() -> None:
    plan = _plan(section=_section(title="Foundations", primary=[], complete=False), title="Book")
    report = diagnose_book_plan_completeness(
        plan,
        [_chunk("chunk-unrelated", "Unrelated topic")],
        domain_config=_config("Missing curriculum topic"),
    )

    assert report.status == "BLOCKED"
    assert report.counts[GENUINE_SOURCE_GAP] > 0
    assert all(issue.issue_type != REPLANNABLE_PLAN_GAP for issue in report.issues)


def test_frozen_book_plan_is_diagnosed_but_never_modified() -> None:
    plan = _plan(section=_section(primary=["chunk-1"]))
    before = deepcopy(plan)
    result = optimize_book_plan_completeness(
        plan,
        [_chunk("chunk-1", "Basic operation")],
        domain_config=_config("Basic operation"),
        allow_replan=False,
    )

    assert result.book_plan_changed is False
    assert book_plan_deep_equal(result.book_plan, before)
    assert result.final_report.status == "REPLAN_REQUIRED"


def test_shared_primary_is_allowed_without_losing_evidence() -> None:
    first = _section("section-01", "Basic operation", primary=["chunk-1"], complete=True)
    second = _section("section-02", "Basic operation conditions", primary=["chunk-1"], complete=True)
    plan = BookPlan(
        book_id="book-1",
        title="Book",
        planning_strategy="rule",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter-01",
                chapter_no=1,
                title="Book",
                learning_goals=["Goal"],
                sections=[first, second],
                primary_material_ids=["chunk-1"],
            )
        ],
    )
    result = optimize_book_plan_completeness(
        plan,
        [_chunk("chunk-1", "Basic operation")],
        domain_config=_config("Book"),
        allow_replan=True,
    )
    second_after = result.book_plan.chapters[0].sections[1]

    assert second_after.primary_material_ids == ["chunk-1"]
    assert second_after.reference_material_ids == []
    assert result.final_report.status == "PASS"
    assert result.final_report.counts.get(EVIDENCE_BINDING_GAP, 0) == 0


def test_replan_metadata_without_authorized_evidence_is_not_accepted() -> None:
    plan = _plan(section=_section(primary=["chunk-1"]), title="Foundations")
    candidate = deepcopy(plan)
    candidate.chapters[0].sections[0].primary_material_ids = []
    candidate.chapters[0].sections[0].section_purpose = "Ungrounded planner claim"
    candidate.chapters.append(
        BookChapterPlan(
            chapter_id="chapter-02",
            chapter_no=2,
            title="Missing topic",
            learning_goals=["Missing topic outcome"],
            sections=[_section("section-02", "Missing topic", primary=["chunk-2"])],
            primary_material_ids=["chunk-2"],
        )
    )

    result = optimize_book_plan_completeness(
        plan,
        [_chunk("chunk-1", "Foundations"), _chunk("chunk-2", "Missing topic")],
        domain_config=_config("Foundations", "Missing topic"),
        replanner=lambda _current, _issues: candidate,
        max_replan_attempts=1,
        allow_replan=True,
    )

    # The existing section proposal has no evidence binding, so its planner
    # text cannot silently replace the evidence-bounded deterministic result.
    assert result.final_report.status == "REPLAN_REQUIRED"
    assert result.book_plan.chapters[0].sections[0].section_purpose != "Ungrounded planner claim"


def test_book_plan_payload_parses_completeness_fields_and_module_intent() -> None:
    plan = book_plan_from_dict(
        {
            "book_id": "book-1",
            "title": "Book",
            "chapters": [
                {
                    "chapter_id": "chapter-01",
                    "chapter_no": 1,
                    "title": "Foundations",
                    "learning_goals": ["Explain the foundation."],
                    "sections": [
                        {
                            "section_id": "section-01",
                            "section_no": "1.1",
                            "title": "Foundation",
                            "knowledge_points": ["foundation"],
                            "primary_material_ids": ["chunk-1"],
                            "reference_material_ids": ["chunk-2"],
                            "section_purpose": "Build a foundation.",
                            "expected_learning_outcome": "Explain the foundation.",
                            "current_task_action": "Inspect the foundation.",
                            "knowledge_scope": ["foundation"],
                            "module_intent": {
                                "case_purpose": "Apply the foundation.",
                                "exercise_purpose": "Practice the foundation.",
                                "assessment_purpose": "Assess the foundation.",
                                "activity_purpose": "Observe the foundation.",
                            },
                        }
                    ],
                }
            ],
        },
        title="Book",
    )
    section = plan.chapters[0].sections[0]

    assert section.reference_material_ids == ["chunk-2"]
    assert section.section_purpose == "Build a foundation."
    assert section.case_purpose == "Apply the foundation."
    assert section.activity_purpose == "Observe the foundation."
