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
    assert example.title == "送丝课堂应用示例"
    assert example.evidence_chunk_ids == ["C2"]
    assert "新手学生" in example.prompt
    assert "同类现场任务" in example.prompt
    assert "迁移" in example.reference_answer
    assert "示范视频" in example.reference_answer
    assert "C2" not in example.reference_answer
    assert "chunk_id" not in example.reference_answer
    assert "Pending" not in example.reference_answer
    assert "教师应" not in example.reference_answer
    assert "结合课堂示范" in example.reference_answer


def test_case_designer_hides_internal_review_text_from_student_case() -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本操作"],
        knowledge_points=[
            KnowledgePoint("kp_01", "送丝", ["C1"], difficulty_level="practice", cluster_id="operation"),
        ],
        evidence_chunk_ids=["C1"],
        learning_path=["kp_01"],
    )
    chunk = make_chunk("C1", "送丝", "Pending")
    chunk.summary = "送丝候选片段 1，由处理队列自动生成，待 agent/人工复核。"
    chunk.content = "第二回饭,採用左手送司法,一座漢师向前移动,送入龙磁。"

    enriched = CaseDesignerAgent().run([plan], [chunk])[0]
    answer = enriched.case_examples[0].reference_answer

    assert "候选片段" not in answer
    assert "agent" not in answer
    assert "人工复核" not in answer
    assert "教师应" not in answer
    assert "漢师" not in answer
    assert "示范视频" in answer


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
