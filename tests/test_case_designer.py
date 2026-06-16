from materials2textbook.agents.case_designer import CaseDesignerAgent
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def make_chunk(chunk_id: str, title: str, status: str = "approved") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="A1",
        title=title,
        content=f"{title} evidence text",
        summary=f"{title} summary",
        keywords=[title],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        review_status=status,
    )


def test_case_designer_creates_evidence_grounded_case_examples() -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本操作"],
        knowledge_points=[
            KnowledgePoint("kp_01", "基本原理", ["C1"], difficulty_level="basic", cluster_id="concept"),
            KnowledgePoint("kp_02", "送丝", ["C2"], difficulty_level="practice", cluster_id="operation"),
        ],
        evidence_chunk_ids=["C1", "C2"],
        learning_path=["kp_01", "kp_02"],
    )

    enriched = CaseDesignerAgent().run([plan], [make_chunk("C1", "基本原理"), make_chunk("C2", "送丝", "Pending")])[0]

    assert len(enriched.case_examples) == 1
    example = enriched.case_examples[0]
    assert example.title == "送丝证据分析示例"
    assert example.evidence_chunk_ids == ["C2"]
    assert "新手学生" in example.prompt
    assert "迁移" in example.prompt
    assert "待复核" in example.reference_answer
    assert "C2" in example.reference_answer


def test_case_designer_keeps_existing_examples() -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=[],
        knowledge_points=[],
        evidence_chunk_ids=[],
    )
    enriched = CaseDesignerAgent().run([plan], [])

    assert enriched[0].case_examples == []
