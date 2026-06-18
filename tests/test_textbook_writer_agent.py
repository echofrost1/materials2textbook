from __future__ import annotations

from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


class _StaticLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, _messages):
        return self.response


class _FailingLLM:
    def generate(self, _messages):
        raise RuntimeError("provider unavailable")


def test_rule_writer_outputs_textbook_body_instead_of_evidence_list() -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝操作",
        content=(
            "送丝操作应观察熔池形态，将焊丝端部稳定送入熔池。"
            "焊丝应保持在氩气保护区内，避免氧化。"
            "操作不当可能出现气孔、夹钨或熔合不良。"
        ),
        summary="送丝速度应与焊接电流、焊接速度和接头间隙相匹配。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="钨极氩弧焊",
        locator=EvidenceLocator(path="demo.mp4"),
        score=EvidenceScore(teaching_value=0.9),
        source_type="video",
        review_status="Agent_Keep",
        metadata={"source_video": "demo.mp4"},
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊",
        learning_goals=["理解送丝操作"],
        knowledge_points=[
            KnowledgePoint(
                knowledge_point_id="kp_01",
                title="送丝操作",
                chunk_ids=["C1"],
                order_index=1,
                difficulty_level="practice",
            )
        ],
        evidence_chunk_ids=["C1"],
    )

    markdown = TextbookWriterAgent().run([plan], [chunk], "样章")

    assert "### 学习路径" not in markdown
    assert "- 证据 `C1`" not in markdown
    assert "##### 知识讲解" in markdown
    assert "##### 操作与观察任务" in markdown
    assert "##### 工艺要点与常见错误" in markdown
    assert "##### 小结与练习" in markdown
    assert "证据：C1" in markdown
    assert "本节证据覆盖：C1" in markdown
    assert "送丝速度应与焊接电流" in markdown


def test_rule_writer_keeps_all_point_citations_without_raw_evidence_list() -> None:
    chunks = [
        EvidenceChunk(
            chunk_id=f"C{index}",
            asset_id=f"A{index}",
            title="送丝操作",
            content=f"第 {index} 条送丝证据说明熔池观察和焊丝保护。",
            summary="",
            keywords=["送丝"],
            subject="焊接技术",
            material_block="钨极氩弧焊",
            material_block_code="tig_welding",
            recommended_chapter="钨极氩弧焊",
            locator=EvidenceLocator(),
            score=EvidenceScore(teaching_value=0.8),
            review_status="Agent_Keep",
        )
        for index in range(1, 4)
    ]
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊",
        learning_goals=[],
        knowledge_points=[KnowledgePoint("kp_01", "送丝操作", ["C1", "C2", "C3"], order_index=1)],
        evidence_chunk_ids=["C1", "C2", "C3"],
    )

    markdown = TextbookWriterAgent().run([plan], chunks, "样章")

    assert "- 证据 `" not in markdown
    assert "本节证据覆盖：C1、C2、C3" in markdown


def test_rule_writer_lists_not_ready_points_without_expanding() -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊",
        learning_goals=["理解打底焊"],
        knowledge_points=[KnowledgePoint("kp_01", "打底焊", [])],
        evidence_chunk_ids=[],
    )

    markdown = TextbookWriterAgent().run([plan], [], "样章")

    assert "证据不足的知识点" in markdown
    assert "打底焊" in markdown
    assert "暂不展开完整教材正文" in markdown


def test_llm_writer_falls_back_when_response_is_too_short() -> None:
    chunk, plan = _sample_chunk_and_plan()
    writer = TextbookWriterAgent(llm_provider=_StaticLLM("短文本 C1"), use_llm=True)

    markdown = writer.run([plan], [chunk], "样章")

    assert writer.last_generation_mode == "rule_fallback"
    assert "##### 知识讲解" in markdown
    assert "本节证据覆盖：C1" in markdown
    assert writer.last_generation_warning


def test_llm_writer_falls_back_when_provider_raises() -> None:
    chunk, plan = _sample_chunk_and_plan()
    writer = TextbookWriterAgent(llm_provider=_FailingLLM(), use_llm=True)

    markdown = writer.run([plan], [chunk], "样章")

    assert writer.last_generation_mode == "rule_fallback"
    assert "##### 操作与观察任务" in markdown
    assert "provider unavailable" in writer.last_generation_warning


def test_llm_writer_keeps_usable_markdown() -> None:
    chunk, plan = _sample_chunk_and_plan()
    paragraph = (
        "样章围绕钨极氩弧焊和送丝操作展开。学生应结合证据 C1 理解焊丝进入熔池时的观察要点，"
        "并把熔池形态、焊丝端部位置和氩气保护状态联系起来判断操作质量。"
    )
    llm_markdown = "# 样章\n\n" + "\n\n".join([paragraph] * 8)
    writer = TextbookWriterAgent(llm_provider=_StaticLLM(llm_markdown), use_llm=True)

    markdown = writer.run([plan], [chunk], "样章")

    assert writer.last_generation_mode == "llm"
    assert markdown == llm_markdown.rstrip() + "\n"


def test_llm_writer_requires_multiple_citations_when_many_chunks_exist() -> None:
    chunk, plan = _sample_chunk_and_plan()
    extra_chunks = [
        EvidenceChunk(
            chunk_id=f"C{index}",
            asset_id=f"A{index}",
            title="送丝操作",
            content=f"第 {index} 条证据。",
            summary="",
            keywords=["送丝"],
            subject="焊接技术",
            material_block="钨极氩弧焊",
            material_block_code="tig_welding",
            recommended_chapter="钨极氩弧焊",
            locator=EvidenceLocator(path="demo.mp4"),
            score=EvidenceScore(teaching_value=0.8),
            source_type="video",
            review_status="Agent_Keep",
        )
        for index in range(2, 5)
    ]
    plan.knowledge_points[0].chunk_ids = ["C1", "C2", "C3", "C4"]
    plan.evidence_chunk_ids = ["C1", "C2", "C3", "C4"]
    paragraph = (
        "样章围绕钨极氩弧焊和送丝操作展开。学生应结合证据 C1 理解焊丝进入熔池时的观察要点，"
        "并把熔池形态、焊丝端部位置和氩气保护状态联系起来判断操作质量。"
    )
    llm_markdown = "# 样章\n\n" + "\n\n".join([paragraph] * 8)
    writer = TextbookWriterAgent(llm_provider=_StaticLLM(llm_markdown), use_llm=True)

    markdown = writer.run([plan], [chunk, *extra_chunks], "样章")

    assert writer.last_generation_mode == "rule_fallback"
    assert "本节证据覆盖：C1、C2、C3、C4" in markdown


def _sample_chunk_and_plan() -> tuple[EvidenceChunk, ChapterPlan]:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝操作",
        content="送丝操作应观察熔池形态，将焊丝端部稳定送入熔池。",
        summary="送丝速度应与焊接电流、焊接速度和接头间隙相匹配。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="钨极氩弧焊",
        locator=EvidenceLocator(path="demo.mp4"),
        score=EvidenceScore(teaching_value=0.9),
        source_type="video",
        review_status="Agent_Keep",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊",
        learning_goals=["理解送丝操作"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝操作", ["C1"], order_index=1)],
        evidence_chunk_ids=["C1"],
    )
    return chunk, plan
