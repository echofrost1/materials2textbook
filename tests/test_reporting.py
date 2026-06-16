from materials2textbook.schemas import (
    CaseExample,
    ChapterPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
    KnowledgePoint,
    LearningActivity,
    ReviewIssue,
    ReviewReport,
    WorkflowSummary,
)
from materials2textbook.workflow.reporting import calculate_quality_metrics, render_evidence_markdown, render_review_markdown


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
        evidence_coverage_rate=1.0,
        citation_coverage_rate=1.0,
        paragraph_support_rate=0.5,
        claim_support_rate=0.25,
        approved_evidence_rate=0.0,
        pedagogy_completeness_rate=0.67,
        activity_quality_rate=0.5,
        case_quality_rate=0.75,
        overall_quality_score=0.684,
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
    assert "证据覆盖率：100.0%" in markdown
    assert "段落事实支撑率：50.0%" in markdown
    assert "断言事实支撑率：25.0%" in markdown
    assert "活动质量评分：50.0%" in markdown
    assert "案例质量评分：75.0%" in markdown
    assert "综合质量评分：0.6840" in markdown


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


def test_calculate_quality_metrics_rates() -> None:
    chunk = EvidenceChunk(
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
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C1"])],
        evidence_chunk_ids=["C1"],
        activities=["观察视频"],
        learning_path=["kp_01"],
        activity_items=[
            LearningActivity(
                "act_01",
                "observation",
                "basic",
                "观察证据。",
                target_knowledge_point_ids=["kp_01"],
                evidence_chunk_ids=["C1"],
                rubric=["能定位证据。"],
            ),
            LearningActivity(
                "act_02",
                "explanation",
                "practice",
                "解释证据。",
                target_knowledge_point_ids=["kp_01"],
                evidence_chunk_ids=["C1"],
                rubric=["能说明要点。"],
            ),
        ],
        case_examples=[
            CaseExample(
                "case_01",
                "送丝迁移案例",
                "课堂实训中，新手学生需要把送丝判断迁移到同类现场任务。",
                "参考 C1 说明观察点，并说明同类项目中的迁移判断。",
                target_knowledge_point_ids=["kp_01"],
                evidence_chunk_ids=["C1"],
            )
        ],
    )

    metrics = calculate_quality_metrics([chunk], [plan], "送丝操作需要保持稳定，并结合熔池状态调整动作。证据：C1")

    assert metrics["evidence_coverage_rate"] == 1.0
    assert metrics["citation_coverage_rate"] == 1.0
    assert metrics["paragraph_support_rate"] == 1.0
    assert metrics["claim_support_rate"] == 1.0
    assert metrics["approved_evidence_rate"] == 1.0
    assert metrics["pedagogy_completeness_rate"] == 0.6667
    assert metrics["activity_quality_rate"] == 1.0
    assert metrics["case_quality_rate"] == 1.0
    assert metrics["overall_quality_score"] == 0.9667
