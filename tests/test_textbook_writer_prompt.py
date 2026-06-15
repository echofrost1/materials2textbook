from materials2textbook.prompts.textbook_writer import build_textbook_writer_messages
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def test_textbook_writer_prompt_requires_strict_evidence() -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝",
        content="送丝操作证据",
        summary="送丝摘要",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(),
        review_status="Pending_Manual_Timecode",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=[],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C1"])],
        evidence_chunk_ids=["C1"],
    )

    messages = build_textbook_writer_messages([plan], [chunk], "样章")
    combined = "\n".join(message["content"] for message in messages)
    assert "不得补充素材中没有的新章节或事实" in combined
    assert "chunk_id: C1" in combined
    assert "待人工复核" in combined
