from __future__ import annotations

from dataclasses import replace

from materials2textbook.exporters.digital_book import build_digital_book
from materials2textbook.knowledge_map.models import LearningRole
from materials2textbook.knowledge_map.rendered_conformance import wrap_rendered_occurrence
from materials2textbook.knowledge_map.semantic_book_conformance import build_semantic_book_conformance_report
from materials2textbook.knowledge_map.writing_briefs import (
    FallbackOccurrence,
    OccurrenceWritingBrief,
    WritingBriefCoverage,
    build_writing_brief_coverage_from_payload,
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


def _brief(index: int, role: str, *, teach: list[str] | None = None, extensions: list[str] | None = None) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=f"occ:{index}",
        source_knowledge_point_id=f"section_01:kp:{index:02d}",
        canonical_knowledge_id="kp:welding_current",
        source_title="Welding current direction" if index == 1 else "Welding current",
        canonical_title="Welding current direction" if index == 1 else "Welding current",
        chapter_id="chapter_01",
        section_id="section_01",
        role=role,
        already_available_facets=[] if index == 1 else ["EXPLAIN"],
        required_facets=[] if index == 1 else ["EXPLAIN"],
        must_teach_facets=teach or [],
        must_not_reteach_facets=[] if index == 1 else ["EXPLAIN"],
        extension_keys=extensions or [],
        repeated_aspects_to_avoid=[] if index < 3 else ["definition", "adjustment method"],
        prerequisite_context=[],
        contribution_goal="fixture contribution",
        source_chunk_ids=["C001"],
        writing_contract="fixture",
        task_ordinal=index,
        occurrence_ordinal=index,
        allowed_content=["fixture"],
        forbidden_content=["definition"] if index >= 3 else [],
        max_recap_sentences=2 if index == 5 else 1,
        must_include_points=[],
        must_avoid_patterns=["definition", "parameter/method rule"] if index in {3, 5} else [],
    )


def _fallback() -> FallbackOccurrence:
    return FallbackOccurrence(
        occurrence_id="occ:6",
        source_knowledge_point_id="section_01:kp:06",
        canonical_knowledge_id="kp:unresolved",
        source_title="Unresolved current context",
        chapter_id="chapter_01",
        section_id="section_01",
        task_ordinal=6,
        occurrence_ordinal=6,
        source_chunk_ids=["C001"],
        reason="untrusted_semantic_plan",
    )


def test_phase2b_coverage_routes_untrusted_occurrence_to_explicit_fallback() -> None:
    payload = {
        "semantic_deltas": [
            {"occurrence_id": "occ:trusted", "repeated_aspects": [], "evidence_chunk_ids": ["C001"]},
            {"occurrence_id": "occ:untrusted", "repeated_aspects": [], "evidence_chunk_ids": ["C001"], "confidence": 0.2},
        ],
        "knowledge_map": {
            "source_knowledge_points": [
                {"source_knowledge_point_id": "source:1", "title": "Current", "chapter_id": "chapter_01", "section_id": "section_01", "source_chunk_ids": ["C001"]},
                {"source_knowledge_point_id": "source:2", "title": "Unknown", "chapter_id": "chapter_01", "section_id": "section_01", "source_chunk_ids": ["C001"]},
            ],
            "knowledge_points": [{"knowledge_id": "kp:current", "title": "Current"}],
            "availability_snapshots": [
                {"occurrence_id": "occ:trusted", "before": {"availability_by_knowledge": {}}},
                {"occurrence_id": "occ:untrusted", "before": {"availability_by_knowledge": {}}},
            ],
            "planned_occurrences": [
                {
                    "occurrence_id": "occ:trusted", "knowledge_id": "kp:current", "source_knowledge_point_id": "source:1",
                    "chapter_id": "chapter_01", "section_id": "section_01", "position": {"task_ordinal": 1, "occurrence_ordinal": 1},
                    "role": "INTRO", "required_self_facets": [], "required_prerequisites": [], "intended_grants": ["ORIENTED"],
                    "intended_extension_keys": [], "intended_contribution": "Introduce.", "source_chunk_ids": ["C001"], "trusted_for_state": True,
                },
                {
                    "occurrence_id": "occ:untrusted", "knowledge_id": "kp:current", "source_knowledge_point_id": "source:2",
                    "chapter_id": "chapter_01", "section_id": "section_01", "position": {"task_ordinal": 2, "occurrence_ordinal": 2},
                    "source_chunk_ids": ["C001"], "trusted_for_state": False, "planning_confidence": 0.2,
                },
            ],
        },
    }

    coverage = build_writing_brief_coverage_from_payload(payload)

    assert [item.occurrence_id for item in coverage.briefs] == ["occ:trusted"]
    assert [(item.occurrence_id, item.reason) for item in coverage.fallback_occurrences] == [("occ:untrusted", "low_confidence_semantic_plan")]


def test_phase2b_digital_book_uses_brief_roles_and_skips_legacy_deduplication(tmp_path) -> None:
    briefs = [
        _brief(1, LearningRole.INTRO, teach=["ORIENTED"]),
        _brief(2, LearningRole.TEACH, teach=["EXPLAIN"]),
        _brief(3, LearningRole.APPLY),
        _brief(4, LearningRole.EXTEND, extensions=["constraint:thin_plate_current_limit"]),
        _brief(5, LearningRole.TEACH),
    ]
    fallback = _fallback()
    chunk = EvidenceChunk("C001", "A1", "Current", "Current direction and thin plate constraint.", "Current direction and thin plate constraint.", [], "approved", "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore())
    plan = ChapterPlan("chapter_01", "Welding basics", [], [KnowledgePoint("kp1", "Current", ["C001"])], ["C001"])
    book_plan = BookPlan("book", "Fixture", "fixture", [BookChapterPlan("chapter_01", 1, "Current", [], [BookSectionPlan("section_01", "1.1", "Current", ["Current"], ["C001"])])])

    book = build_digital_book(
        title="Fixture",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path,
        copy_media_assets=False,
        book_plan=book_plan,
        occurrence_writing_briefs=briefs,
        fallback_occurrences=[fallback],
        semantic_book_mode=True,
    )
    roles = book.metadata["semantic_occurrence_roles"]

    assert roles == {**{item.occurrence_id: item.role for item in briefs}, "occ:6": "FALLBACK"}
    assert book.metadata["content_deduplication"][0]["action"] == "not_called"
    assert book.metadata["semantic_rendered_conformance"]["anchor_coverage"] == 1.0

    bodies = {
        "occ:1": "Observe the current direction before later theory.",
        "occ:2": "Explain welding current and its effect.",
        "occ:3": "Apply the known welding current method in the current task.",
        "occ:4": "Use the known method with a thin plate current limit to avoid burn-through.",
        "occ:5": "Use the already taught welding current knowledge in this task.",
    }
    markdown = "\n".join(wrap_rendered_occurrence(item, bodies[item.occurrence_id]) for item in briefs)
    markdown += wrap_rendered_occurrence(fallback, "Use the supplied material in the current task.")
    report = build_semantic_book_conformance_report(
        coverage=WritingBriefCoverage(briefs=briefs, fallback_occurrences=[fallback]),
        markdown=markdown,
        digital_book_metadata=book.metadata,
    )

    assert report.total_occurrences == 6
    assert report.markdown_anchor_coverage == 1.0
    assert report.digital_book_anchor_coverage == 1.0
    assert report.occurrence_alignment["alignment_rate"] == 1.0
    assert not report.occurrence_alignment["role_mismatches"]
    assert report.legacy_deduplication["called"] is False


def test_phase2b_digital_book_preserves_duplicate_display_title_sections_for_occurrence_mapping(tmp_path) -> None:
    first = _brief(1, LearningRole.TEACH, teach=["EXPLAIN"])
    second = replace(_brief(2, LearningRole.APPLY), section_id="section_02", source_knowledge_point_id="section_02:kp:01")
    chunk = EvidenceChunk("C001", "A1", "Current", "Current direction.", "Current direction.", [], "approved", "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore())
    plan = ChapterPlan("chapter_01", "Welding basics", [], [KnowledgePoint("kp1", "Current", ["C001"])], ["C001"])
    book_plan = BookPlan("book", "Fixture", "fixture", [BookChapterPlan(
        "chapter_01", 1, "Welding basics", [], [
            BookSectionPlan("section_01", "1.1", "Current", ["Current"], ["C001"]),
            BookSectionPlan("section_02", "1.2", "Current", ["Current"], ["C001"]),
        ],
    )])

    book = build_digital_book(
        title="Fixture", plans=[plan], chunks=[chunk], output_dir=tmp_path, copy_media_assets=False,
        book_plan=book_plan, occurrence_writing_briefs=[first, second], semantic_book_mode=True,
    )

    assert book.metadata["semantic_occurrence_roles"] == {first.occurrence_id: "TEACH", second.occurrence_id: "APPLY"}
    assert book.metadata["semantic_rendered_conformance"]["anchor_coverage"] == 1.0


def test_phase2b_semantic_book_preserves_project_title_section_for_occurrence_mapping(tmp_path) -> None:
    first = _brief(1, LearningRole.TEACH, teach=["EXPLAIN"])
    second = replace(
        _brief(2, LearningRole.TEACH, teach=["EXPLAIN"]),
        section_id="section_02",
        source_knowledge_point_id="section_02:kp:01",
    )
    chunk = EvidenceChunk("C001", "A1", "设备", "设备安全内容。", "设备安全内容。", [], "approved", "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore())
    plan = ChapterPlan("chapter_01", "项目一 设备与安全", [], [
        KnowledgePoint("kp1", "设备识别", ["C001"]),
        KnowledgePoint("kp2", "项目一 设备与安全", ["C001"]),
    ], ["C001"])
    book_plan = BookPlan("book", "Fixture", "fixture", [BookChapterPlan(
        "chapter_01", 1, "项目一 设备与安全", [], [
            BookSectionPlan("section_01", "1.1", "设备识别", ["设备识别"], ["C001"]),
            BookSectionPlan("section_02", "1.2", "项目一 设备与安全", ["项目一 设备与安全"], ["C001"]),
        ],
    )])

    book = build_digital_book(
        title="Fixture", plans=[plan], chunks=[chunk], output_dir=tmp_path, copy_media_assets=False,
        book_plan=book_plan, occurrence_writing_briefs=[first, second], semantic_book_mode=True,
    )

    assert [task.metadata["section_id"] for task in book.projects[0].tasks] == ["section_01", "section_02"]
    assert book.metadata["semantic_occurrence_roles"] == {first.occurrence_id: "TEACH", second.occurrence_id: "TEACH"}
    assert book.metadata["semantic_rendered_conformance"]["anchor_coverage"] == 1.0
