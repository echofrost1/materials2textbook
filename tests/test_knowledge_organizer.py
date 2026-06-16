from materials2textbook.agents.knowledge_organizer import KnowledgeOrganizerAgent
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def make_chunk(
    chunk_id: str,
    title: str,
    *,
    start_ms: int = 0,
    knowledge_order: int | None = None,
    semantic_cluster: str | None = None,
) -> EvidenceChunk:
    metadata = {}
    if knowledge_order is not None:
        metadata["knowledge_order"] = knowledge_order
    if semantic_cluster is not None:
        metadata["semantic_cluster"] = semantic_cluster
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
        recommended_chapter="基本操作",
        locator=EvidenceLocator(start_ms=start_ms),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
        metadata=metadata,
    )


def test_knowledge_organizer_builds_learning_path_and_prerequisites() -> None:
    plans = KnowledgeOrganizerAgent().run(
        [
            make_chunk("C2", "送丝操作", start_ms=20_000),
            make_chunk("C1", "焊接基本原理", start_ms=1_000),
            make_chunk("C3", "适用范围", start_ms=40_000),
        ]
    )

    plan = plans[0]
    assert [point.title for point in plan.knowledge_points] == ["焊接基本原理", "送丝操作", "适用范围"]
    assert plan.learning_path == ["kp_01_01", "kp_01_02", "kp_01_03"]
    assert plan.knowledge_points[0].difficulty_level == "basic"
    assert plan.knowledge_points[1].difficulty_level == "practice"
    assert plan.knowledge_points[2].difficulty_level == "advanced"
    assert plan.knowledge_points[1].prerequisite_ids == ["kp_01_01"]
    assert plan.knowledge_points[2].prerequisite_ids == ["kp_01_01", "kp_01_02"]
    assert plan.knowledge_points[0].cluster_id == "concept"


def test_knowledge_organizer_honors_explicit_knowledge_order() -> None:
    plans = KnowledgeOrganizerAgent().run(
        [
            make_chunk("C1", "安全准备", knowledge_order=2),
            make_chunk("C2", "基本原理", knowledge_order=1),
        ]
    )

    assert [point.title for point in plans[0].knowledge_points] == ["基本原理", "安全准备"]


def test_knowledge_organizer_uses_explicit_semantic_cluster() -> None:
    plans = KnowledgeOrganizerAgent().run(
        [
            make_chunk("C1", "送丝操作", semantic_cluster="wire_feeding"),
            make_chunk("C2", "送丝操作", semantic_cluster="wire_feeding"),
        ]
    )

    assert plans[0].knowledge_points[0].cluster_id == "wire_feeding"
