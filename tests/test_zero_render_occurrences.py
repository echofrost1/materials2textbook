from __future__ import annotations

from pathlib import Path

from materials2textbook.exporters.digital_book import build_digital_book
from materials2textbook.knowledge_map.models import (
    BookPosition,
    KnowledgeKind,
    LearningRole,
    PlannedOccurrence,
    SemanticDelta,
)
from materials2textbook.knowledge_map.publication_quality import (
    PublicationQualityCode,
    evaluate_publication_quality,
)
from materials2textbook.knowledge_map.materialization import (
    OccurrenceFinalStatus,
    materialize_full_book,
)
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    check_rendered_conformance,
    wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.semantic_book_conformance import build_semantic_book_conformance_report
from materials2textbook.knowledge_map.writing_briefs import (
    OccurrenceWritingBrief,
    WritingBriefCoverage,
    decide_zero_render_occurrence,
)
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    ChapterPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
    KnowledgePoint,
)


def _empty_occurrence() -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id="occ:generic:no-value",
        knowledge_id="kp:generic",
        source_knowledge_point_id="source:generic",
        position=BookPosition(1, 1, 1),
        chapter_id="chapter_01",
        section_id="section_01",
        context_title="generic task",
        source_chunk_ids=["C001"],
        role=LearningRole.APPLY,
        trusted_for_state=True,
    )


def _empty_delta() -> SemanticDelta:
    return SemanticDelta(
        occurrence_id="occ:generic:no-value",
        repeats_prior_explanation=True,
        uses_prior_knowledge=False,
        recall_needed=False,
        required_self_facets=[],
        required_self_extension_keys=[],
        cross_prerequisite_uses=[],
        new_facets=[],
        new_extension_keys=[],
        new_context="",
        repeated_aspects=["previous explanation"],
        contribution_summary="",
        confidence=0.95,
        rationale="no current teaching, task-use, or recall responsibility",
        evidence_chunk_ids=["C001"],
    )


def _brief() -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id="occ:expected-render",
        source_knowledge_point_id="source:render",
        canonical_knowledge_id="kp:render",
        source_title="generic concept",
        canonical_title="generic concept",
        chapter_id="chapter_01",
        section_id="section_01",
        role=LearningRole.TEACH,
        already_available_facets=[],
        required_facets=[],
        must_teach_facets=["EXPLAIN"],
        must_not_reteach_facets=[],
        extension_keys=[],
        repeated_aspects_to_avoid=[],
        prerequisite_context=[],
        contribution_goal="teach generic concept",
        source_chunk_ids=["C001"],
        writing_contract="fixture",
    )


def _book_plan() -> BookPlan:
    return BookPlan(
        "book",
        "Fixture",
        "fixture",
        [BookChapterPlan(
            "chapter_01", 1, "Generic chapter", [],
            [BookSectionPlan("section_01", "1.1", "Generic task", ["generic concept"], ["C001"])],
        )],
    )


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        "C001", "asset", "generic concept", "generic evidence", "generic evidence", [], "approved",
        "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore(),
    )


def test_explicit_zero_render_preserves_occurrence_audit_and_outline_projection(tmp_path: Path) -> None:
    occurrence = _empty_occurrence()
    zero = decide_zero_render_occurrence(occurrence=occurrence, delta=_empty_delta())
    assert zero is not None
    assert zero.outline_node_id == "section_01"
    assert zero.non_render_reason == "NO_CURRENT_TEACHING_OR_TASK_USE_VALUE"
    coverage = WritingBriefCoverage(zero_render_occurrences=[zero])

    markdown = "# Fixture\n\n## Generic task\n"
    markdown_report = check_rendered_conformance([], markdown, zero_render_occurrences=[zero])
    [result] = markdown_report.results
    assert markdown_report.expected_rendered_occurrences == 0
    assert markdown_report.explicit_zero_render_occurrences == 1
    assert result.overall == ConformanceStatus.NOT_APPLICABLE
    assert result.anchor_present is False
    assert result.body_present is False

    plan = _book_plan()
    book = build_digital_book(
        title="Fixture",
        plans=[ChapterPlan("chapter_01", "Generic chapter", [], [KnowledgePoint("kp1", "generic concept", ["C001"])], ["C001"])],
        chunks=[_chunk()],
        output_dir=tmp_path,
        copy_media_assets=False,
        book_plan=plan,
        zero_render_occurrences=[zero],
        semantic_book_mode=True,
    )
    assert len(book.projects) == 1
    assert len(book.projects[0].tasks) == 1  # Zero render cannot remove the frozen task/section projection.
    assert book.metadata["semantic_zero_render_occurrences"][0]["occurrence_id"] == zero.occurrence_id
    assert zero.occurrence_id not in book.metadata["semantic_occurrence_roles"]
    assert all(
        block.metadata.get("semantic_occurrence", {}).get("occurrence_id") != zero.occurrence_id
        for task in book.projects[0].tasks
        for block in task.blocks
    )

    report = build_semantic_book_conformance_report(
        coverage=coverage,
        markdown=markdown,
        digital_book_metadata=book.metadata,
    )
    assert report.occurrence_alignment["expected_rendered_occurrences"] == 0
    assert report.occurrence_alignment["explicit_zero_render_occurrences"] == 1
    assert report.occurrence_alignment["alignment_rate"] == 1.0
    assert not report.occurrence_alignment["zero_render_audit_missing_from_digital_book"]
    assert not report.occurrence_alignment["zero_render_unexpected_markdown_bodies"]
    assert not report.occurrence_alignment["zero_render_unexpected_digital_book_bodies"]

    quality = evaluate_publication_quality(
        markdown=markdown,
        digital_book=book,
        coverage=coverage,
        chunks=[_chunk()],
        semantic_closed_loop_passed=True,
    )
    assert not [
        issue for issue in quality.issues
        if issue.occurrence_id == zero.occurrence_id and issue.code == PublicationQualityCode.MISSING_RENDER_ANCHOR
    ]

    materialized = materialize_full_book(
        markdown=markdown,
        digital_book=book,
        coverage=coverage,
        outline_signature="outline:fixture",
        expected_outline_signature="outline:fixture",
        semantic_objects={"occurrences": [zero.occurrence_id]},
        evidence_chunks=[_chunk()],
        source_book_plan_snapshot=plan,
        final_reference_book_plan=plan,
    )
    assert materialized.publication_gate.markdown_digital_alignment == 1.0
    assert materialized.final_states[0].status == OccurrenceFinalStatus.ZERO_RENDERED
    assert materialized.publication_quality is not None
    assert not [
        issue for issue in materialized.publication_quality.issues
        if issue.occurrence_id == zero.occurrence_id and issue.code == PublicationQualityCode.MISSING_RENDER_ANCHOR
    ]


def test_missing_expected_render_body_is_not_zero_render() -> None:
    brief = _brief()
    markdown = wrap_rendered_occurrence(brief, "")

    [result] = check_rendered_conformance([brief], markdown).results

    assert result.render_decision == "RENDER"
    assert result.anchor_present is True
    assert result.body_present is False
    assert result.overall == ConformanceStatus.VIOLATION

    [missing] = check_rendered_conformance([brief], "# Fixture\n").results
    assert missing.render_decision == "RENDER"
    assert missing.anchor_present is False
    assert missing.body_present is False
    assert missing.overall == ConformanceStatus.VIOLATION
