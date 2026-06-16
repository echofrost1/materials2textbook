from materials2textbook.agents.activity_designer import ActivityDesignerAgent
from materials2textbook.schemas import ChapterPlan, KnowledgePoint


def make_plan() -> ChapterPlan:
    return ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本操作"],
        knowledge_points=[
            KnowledgePoint("kp_01", "基本原理", ["C1"], difficulty_level="basic", cluster_id="concept"),
            KnowledgePoint(
                "kp_02",
                "送丝操作",
                ["C2"],
                difficulty_level="practice",
                prerequisite_ids=["kp_01"],
                cluster_id="operation",
            ),
            KnowledgePoint(
                "kp_03",
                "适用范围",
                ["C3"],
                difficulty_level="advanced",
                prerequisite_ids=["kp_01", "kp_02"],
                cluster_id="extension",
            ),
        ],
        evidence_chunk_ids=["C1", "C2", "C3"],
        learning_path=["kp_01", "kp_02", "kp_03"],
    )


def test_activity_designer_generates_tiered_traceable_activities() -> None:
    plan = ActivityDesignerAgent().run([make_plan()])[0]

    assert [activity.difficulty_level for activity in plan.activity_items] == ["basic", "practice", "advanced"]
    assert {activity.type for activity in plan.activity_items} == {"observation", "explanation", "analysis"}
    assert all(activity.evidence_chunk_ids for activity in plan.activity_items)
    assert all(activity.rubric for activity in plan.activity_items)
    assert len(plan.activities) == 3
    assert "观看示范视频" in plan.activities[0]
    assert "[基础·观察任务]" in plan.activities[0]
    assert "basic/observation" not in " ".join(plan.activities)
    assert "chunk_id" not in " ".join(plan.activities)
    assert "证据：C1" not in " ".join(plan.activities)


def test_activity_designer_keeps_existing_structured_activities() -> None:
    plan = make_plan()
    enriched = ActivityDesignerAgent().run([plan])[0]
    second = ActivityDesignerAgent().run([enriched])[0]

    assert second.activity_items == enriched.activity_items
