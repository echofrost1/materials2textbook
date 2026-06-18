from materials2textbook.agents.outline_planner import OutlinePlannerAgent
from materials2textbook.agents.title_polisher import TitlePolisherAgent
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def make_chunk(chunk_id: str, title: str, chapter: str = "基本操作") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="A1",
        title=title,
        content=f"{title} evidence",
        summary=f"{title} summary",
        keywords=[title],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter=chapter,
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )


def test_title_polisher_polishes_plan_titles_without_changing_scope() -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本操作的核心概念"],
        knowledge_points=[
            KnowledgePoint("kp_01", "基本原理", ["C1"], difficulty_level="basic"),
            KnowledgePoint("kp_02", "送丝", ["C2"], difficulty_level="practice"),
            KnowledgePoint("kp_03", "适用范围", ["C3"], difficulty_level="advanced"),
        ],
        evidence_chunk_ids=["C1", "C2", "C3"],
        learning_path=["kp_01", "kp_02", "kp_03"],
    )

    enriched = TitlePolisherAgent().run(
        [plan],
        [make_chunk("C1", "基本原理"), make_chunk("C2", "送丝"), make_chunk("C3", "适用范围")],
    )[0]

    assert enriched.title == "钨极氩弧焊基本操作"
    assert [point.title for point in enriched.knowledge_points] == ["基本原理", "送丝操作要点", "适用范围"]
    assert enriched.knowledge_points[1].knowledge_point_id == "kp_02"
    assert enriched.knowledge_points[1].chunk_ids == ["C2"]
    assert enriched.learning_path == ["kp_01", "kp_02", "kp_03"]
    assert enriched.learning_goals == ["理解钨极氩弧焊基本操作的核心概念"]


def test_title_polisher_polishes_outline_titles() -> None:
    chunks = [make_chunk("C1", "送丝"), make_chunk("C2", "适用范围")]
    outlines = OutlinePlannerAgent().run(chunks)

    enriched = TitlePolisherAgent().run_outlines(outlines, chunks)[0]

    assert enriched.title == "钨极氩弧焊基本操作"
    assert enriched.sections[0].title == "钨极氩弧焊"
    assert [topic.title for topic in enriched.sections[0].topics] == ["送丝操作要点", "适用范围"]


def test_title_polisher_keeps_basic_principle_topic_title() -> None:
    chunks = [make_chunk("C1", "钨极氩弧焊基本原理")]
    outlines = OutlinePlannerAgent().run(chunks)

    enriched = TitlePolisherAgent().run_outlines(outlines, chunks)[0]

    assert enriched.sections[0].topics[0].title == "钨极氩弧焊基本原理"


def test_title_polisher_merges_duplicate_points_with_chapter_prefix() -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊基本操作",
        learning_goals=["理解钨极氩弧焊基本操作"],
        knowledge_points=[
            KnowledgePoint("kp_01", "基本原理", ["C1"], difficulty_level="basic"),
            KnowledgePoint("kp_02", "钨极氩弧焊基本原理", ["C2"], difficulty_level="basic"),
            KnowledgePoint("kp_03", "特点和适用范围", ["C3"], difficulty_level="advanced"),
            KnowledgePoint("kp_04", "钨极氩弧焊特点和适用范围", ["C4"], difficulty_level="advanced"),
        ],
        evidence_chunk_ids=["C1", "C2", "C3", "C4"],
        learning_path=["kp_01", "kp_02", "kp_03", "kp_04"],
    )

    enriched = TitlePolisherAgent().run(
        [plan],
        [
            make_chunk("C1", "基本原理", "钨极氩弧焊基本操作"),
            make_chunk("C2", "钨极氩弧焊基本原理", "钨极氩弧焊基本操作"),
            make_chunk("C3", "特点和适用范围", "钨极氩弧焊基本操作"),
            make_chunk("C4", "钨极氩弧焊特点和适用范围", "钨极氩弧焊基本操作"),
        ],
    )[0]

    titles = [point.title for point in enriched.knowledge_points]
    assert titles == ["钨极氩弧焊基本原理", "钨极氩弧焊特点和适用范围"]
    assert enriched.knowledge_points[0].chunk_ids == ["C1", "C2"]
    assert enriched.knowledge_points[1].chunk_ids == ["C3", "C4"]
    assert enriched.learning_path == ["kp_01", "kp_03"]
