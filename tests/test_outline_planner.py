from materials2textbook.agents.outline_planner import OutlinePlannerAgent, render_outline_markdown
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def make_chunk(chunk_id: str, chapter: str, block: str, topic: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="A1",
        title=topic,
        content="证据",
        summary="摘要",
        keywords=[topic],
        subject="焊接技术",
        material_block=block,
        material_block_code="tig_welding",
        recommended_chapter=chapter,
        locator=EvidenceLocator(),
        score=EvidenceScore(),
    )


def test_outline_planner_builds_three_level_outline() -> None:
    planner = OutlinePlannerAgent()
    outlines = planner.run(
        [
            make_chunk("C1", "基本操作", "钨极氩弧焊", "基本原理"),
            make_chunk("C2", "基本操作", "钨极氩弧焊", "送丝"),
        ]
    )

    assert outlines[0].title == "基本操作"
    assert outlines[0].sections[0].title == "钨极氩弧焊"
    assert [topic.title for topic in outlines[0].sections[0].topics] == ["基本原理", "送丝"]


def test_render_outline_markdown_uses_three_level_numbering() -> None:
    planner = OutlinePlannerAgent()
    outlines = planner.run([make_chunk("C1", "基本操作", "钨极氩弧焊", "基本原理")])
    markdown = render_outline_markdown(outlines, "样章")
    assert "## 第1章 基本操作" in markdown
    assert "### 1.1 钨极氩弧焊" in markdown
    assert "#### 1.1.1 基本原理" in markdown
