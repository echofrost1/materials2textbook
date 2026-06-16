from materials2textbook.prompts.reviewers import build_evidence_review_messages, build_pedagogy_review_messages
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def make_plan() -> ChapterPlan:
    return ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝操作"],
        knowledge_points=[
            KnowledgePoint(
                "kp_01",
                "送丝",
                ["C1"],
                order_index=1,
                difficulty_level="practice",
                prerequisite_ids=["kp_00"],
                cluster_id="operation",
            )
        ],
        evidence_chunk_ids=["C1"],
        activities=["观察视频"],
        learning_path=["kp_01"],
    )


def make_chunk() -> EvidenceChunk:
    return EvidenceChunk(
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
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )


def test_evidence_review_prompt_requires_json_and_evidence_grounding() -> None:
    messages = build_evidence_review_messages(make_plan(), [make_chunk()], "证据：C1")
    combined = "\n".join(message["content"] for message in messages)

    assert "只输出 JSON 数组" in combined
    assert "不得引入外部知识" in combined
    assert "chunk_id: C1" in combined


def test_pedagogy_review_prompt_focuses_on_teaching_quality() -> None:
    messages = build_pedagogy_review_messages(make_plan(), "证据：C1")
    combined = "\n".join(message["content"] for message in messages)

    assert "教学质量审核" in combined
    assert "难度是否递进" in combined
    assert "difficulty=practice" in combined
    assert "prerequisites=kp_00" in combined
    assert "只输出 JSON 数组" in combined
