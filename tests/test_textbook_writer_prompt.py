from materials2textbook.prompts.textbook_writer import build_textbook_writer_messages
from materials2textbook.schemas import CaseExample, ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def test_textbook_writer_prompt_requires_strict_evidence() -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="brake inspection",
        content="Brake inspection evidence",
        summary="Brake inspection summary",
        keywords=["brake"],
        subject="automotive repair",
        material_block="brake system",
        material_block_code="brake",
        recommended_chapter="basic operation",
        locator=EvidenceLocator(),
        score=EvidenceScore(),
        review_status="Pending_Manual_Timecode",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="basic operation",
        learning_goals=[],
        knowledge_points=[KnowledgePoint("kp_01", "brake inspection", ["C1"])],
        evidence_chunk_ids=["C1"],
        case_examples=[
            CaseExample(
                "case_01",
                "Brake case",
                "How should a learner analyze brake inspection?",
                "Answer cautiously using C1.",
                evidence_chunk_ids=["C1"],
            )
        ],
    )

    messages = build_textbook_writer_messages([plan], [chunk], "Sample Chapter")
    combined = "\n".join(message["content"] for message in messages)

    assert "Use only the supplied evidence chunks" in combined
    assert "chunk_id: C1" in combined
    assert "Case example" in combined
    assert "Brake case" in combined
    assert "requiring review" in combined
    for required in ["项目导学", "能力图谱", "学习目标", "学习导航", "情境导入", "任务实施", "任务评价", "思考与练习", "项目小结", "本项目素材缺口"]:
        assert required in combined
