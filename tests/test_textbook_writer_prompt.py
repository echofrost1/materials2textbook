from materials2textbook.prompts.textbook_writer import build_textbook_writer_messages
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
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


def test_textbook_writer_prompt_treats_writing_brief_as_immutable_constraint() -> None:
    plan = ChapterPlan("chapter_01", "basic operation", [], [KnowledgePoint("kp_01", "brake inspection", ["C1"])], ["C1"])
    chunk = EvidenceChunk("C1", "A1", "brake inspection", "evidence", "summary", [], "auto", "brake", "brake", "basic", EvidenceLocator(), EvidenceScore())
    brief = OccurrenceWritingBrief(
        occurrence_id="occ:1", source_knowledge_point_id="source:1", canonical_knowledge_id="kp:brake",
        source_title="brake inspection", canonical_title="brake inspection", chapter_id="chapter_01", section_id="section_01",
        role="APPLY", already_available_facets=["EXPLAIN"], required_facets=["EXPLAIN"], must_teach_facets=[],
        must_not_reteach_facets=["EXPLAIN"], extension_keys=[], repeated_aspects_to_avoid=["definition"],
        prerequisite_context=["self: EXPLAIN"], contribution_goal="Apply the taught inspection method to the current task.",
        source_chunk_ids=["C1"], writing_contract="Assume already-available facets and apply them directly.",
    )
    messages = build_textbook_writer_messages([plan], [chunk], "Sample", occurrence_writing_briefs=[brief])
    combined = "\n".join(message["content"] for message in messages)

    assert "OccurrenceWritingBriefs (authoritative)" in combined
    assert "role: APPLY" in combined
    assert "must_not_reteach_facets: EXPLAIN" in combined
    assert "Do not reclassify their role" in combined
