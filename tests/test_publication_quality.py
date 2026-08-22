from __future__ import annotations

from materials2textbook.knowledge_map.publication_quality import (
    PublicationQualityCode,
    evaluate_publication_quality,
    write_publication_quality_artifacts,
)
from materials2textbook.knowledge_map.writing_briefs import (
    DroppedOccurrenceGoal,
    OccurrenceWritingBrief,
    WritingBriefCoverage,
)
from materials2textbook.schemas import (
    DigitalBook,
    DigitalBookBlock,
    DigitalBookProject,
    DigitalBookTask,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _brief(*, occurrence_id: str = "occ:apply", role: str = "APPLY") -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id,
        source_knowledge_point_id="section_01:kp:01",
        canonical_knowledge_id="kp:arc",
        source_title="引弧方法",
        canonical_title="引弧方法",
        chapter_id="chapter_01",
        section_id="section_01",
        role=role,
        already_available_facets=["PERFORM"],
        required_facets=["PERFORM"],
        must_teach_facets=[] if role == "APPLY" else ["EXPLAIN"],
        must_not_reteach_facets=["PERFORM"],
        extension_keys=[],
        repeated_aspects_to_avoid=[],
        prerequisite_context=[],
        contribution_goal="在当前任务中使用引弧方法完成焊接起始。",
        source_chunk_ids=["C001"],
        writing_contract="fixture",
        task_ordinal=1,
        occurrence_ordinal=1,
    )


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        "C001", "A001", "引弧方法", "当前任务中使用引弧方法完成焊接起始。",
        "引弧方法用于焊接起始。", ["引弧"], "approved", "焊接", "WELD", "第一章",
        EvidenceLocator(), EvidenceScore(),
    )


def _book(*, occurrence_body: str, title: str = "焊接基础") -> DigitalBook:
    block = DigitalBookBlock(
        "b1", "implementation", "学习内容：引弧方法", occurrence_body,
        metadata={"semantic_occurrence": {"occurrence_id": "occ:apply", "section_id": "section_01"}},
    )
    task = DigitalBookTask("chapter_01_task_01", "任务1.1 引弧方法", [block], metadata={"section_id": "section_01"})
    project = DigitalBookProject("chapter_01", "第一章", "导入", [], [], [task])
    return DigitalBook("book", title, {}, [project], general_preface="前言内容", preface="使用说明")


def _anchored(body: str) -> str:
    return (
        "# 焊接基础\n\n"
        '<!-- occurrence:start id="occ:apply" chapter="chapter_01" section="section_01" task="chapter_01:task:1" -->\n'
        f"{body}\n"
        '<!-- occurrence:end id="occ:apply" -->\n'
    )


def test_publication_quality_fails_current_regression_defects(tmp_path):
    coverage = WritingBriefCoverage(
        briefs=[_brief()],
        dropped_occurrence_goals=[DroppedOccurrenceGoal("occ:dropped", "kp:dropped", "chapter_01", "section_02", "no evidence")],
    )
    markdown = _anchored("本任务使用之前的相关知识。汉箱应保持不动。")
    task = DigitalBookTask(
        "chapter_01_task_02", "任务1.2 INTRO: Bridge task",
        [
            DigitalBookBlock("nav", "learning_nav", "导航", items=["薄板焊接"]),
            DigitalBookBlock("assessment", "assessment", "评价", items=["完成评价"]),
            DigitalBookBlock("exercise", "exercises", "练习", items=["完成练习"]),
        ],
        knowledge_points=["dropped"], metadata={"section_id": "section_02"},
    )
    book = _book(occurrence_body="本任务使用之前的相关知识。汉箱应保持不动。", title="Phase 2B.5 multi-occurrence welding evaluation")
    book.projects[0].tasks.append(task)
    report = evaluate_publication_quality(
        markdown=markdown, digital_book=book, coverage=coverage, chunks=[_chunk()],
        semantic_closed_loop_passed=True, repair_attempts=[], materialization_audit=[], declared_rollback_count=1,
    )
    codes = {item.code for item in report.issues}
    assert report.semantic_closed_loop_status == "PASS"
    assert report.publication_quality_status == "FAIL"
    assert report.final_publication_status == "FAIL"
    assert PublicationQualityCode.INTERNAL_LABEL_LEAKAGE in codes
    assert PublicationQualityCode.CORRUPTED_TEXT in codes
    assert PublicationQualityCode.BROKEN_CRITICAL_SENTENCE in codes
    assert PublicationQualityCode.WEAK_APPLICATION_CONTRIBUTION in codes
    assert PublicationQualityCode.ASSESSMENT_WITHOUT_CONTENT_SUPPORT in codes
    assert PublicationQualityCode.EXERCISE_WITHOUT_CONTENT_SUPPORT in codes
    assert PublicationQualityCode.DROPPED_GOAL_STILL_REFERENCED in codes
    assert PublicationQualityCode.REPAIR_HISTORY_INCOMPLETE in codes

    paths = write_publication_quality_artifacts(report=report, output_dir=tmp_path)
    assert all(path.exists() for path in paths)
    assert "final_publication_status: FAIL" in paths[1].read_text(encoding="utf-8")


def test_publication_quality_accepts_aligned_supported_apply():
    body = "本任务使用已学习的引弧方法完成焊接起始，并观察起弧是否稳定。"
    report = evaluate_publication_quality(
        markdown=_anchored(body), digital_book=_book(occurrence_body=body),
        coverage=WritingBriefCoverage(briefs=[_brief()]), chunks=[_chunk()],
        semantic_closed_loop_passed=True,
    )
    codes = {item.code for item in report.issues}
    assert PublicationQualityCode.MISSING_RENDER_ANCHOR not in codes
    assert PublicationQualityCode.CROSS_OUTPUT_CONTENT_MISMATCH not in codes
    assert PublicationQualityCode.UNSUPPORTED_RENDERED_SOURCE_FACT not in codes
    assert PublicationQualityCode.WEAK_APPLICATION_CONTRIBUTION not in codes
    assert report.final_publication_status == "PASS"


def test_publication_quality_blocks_cross_output_mismatch_and_missing_evidence():
    report = evaluate_publication_quality(
        markdown=_anchored("本任务使用已学习的引弧方法完成焊接起始。Evidence: C999"),
        digital_book=_book(occurrence_body="本任务使用已学习的引弧方法完成焊接起始。"),
        coverage=WritingBriefCoverage(briefs=[_brief()]), chunks=[_chunk()],
        semantic_closed_loop_passed=True,
    )
    codes = {item.code for item in report.issues}
    assert PublicationQualityCode.CROSS_OUTPUT_CONTENT_MISMATCH in codes
    assert PublicationQualityCode.UNSUPPORTED_RENDERED_SOURCE_FACT in codes
    assert report.final_publication_status == "FAIL"
