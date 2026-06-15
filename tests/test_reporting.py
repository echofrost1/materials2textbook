from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore, ReviewIssue, ReviewReport, WorkflowSummary
from materials2textbook.workflow.reporting import render_evidence_markdown, render_review_markdown


def test_render_review_markdown_contains_summary_and_issues() -> None:
    summary = WorkflowSummary(
        title="样章",
        source_records=2,
        evidence_chunks=1,
        skipped_chunks=1,
        chapters=1,
        knowledge_points=1,
        fact_issue_count=1,
        pedagogy_issue_count=0,
        high_issue_count=0,
        medium_issue_count=1,
        low_issue_count=0,
        review_status_counts={"Pending": 1},
        material_block_counts={"钨极氩弧焊": 1},
    )
    markdown = render_review_markdown(
        [
            ReviewReport(
                chapter_id="chapter_01",
                chapter_title="基本操作",
                fact_issues=[
                    ReviewIssue("medium", "C1", "证据片段待复核。", "人工确认时间码。")
                ],
            )
        ],
        summary,
    )
    assert "# 样章 审核报告" in markdown
    assert "证据片段待复核" in markdown


def test_render_evidence_markdown_contains_traceability() -> None:
    markdown = render_evidence_markdown(
        [
            EvidenceChunk(
                chunk_id="C1",
                asset_id="A1",
                title="送丝",
                content="证据",
                summary="送丝摘要",
                keywords=["送丝"],
                subject="焊接技术",
                material_block="钨极氩弧焊",
                material_block_code="tig_welding",
                recommended_chapter="基本操作",
                locator=EvidenceLocator(original_path="raw/demo.mp4", keyframe_paths=["frame.jpg"]),
                score=EvidenceScore(teaching_value=0.8),
                review_status="approved",
                metadata={"source_video": "demo.mp4", "start_time": "00:00:01", "end_time": "00:00:03"},
            )
        ],
        "样章",
    )
    assert "# 样章 证据索引" in markdown
    assert "C1" in markdown
    assert "raw/demo.mp4" in markdown
