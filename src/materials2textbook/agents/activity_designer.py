from __future__ import annotations

from dataclasses import replace

from materials2textbook.schemas import ChapterPlan, LearningActivity


class ActivityDesignerAgent:
    """Create tiered activities and exercises from the chapter learning path."""

    def run(self, plans: list[ChapterPlan]) -> list[ChapterPlan]:
        return [self._enrich_plan(plan) for plan in plans]

    def _enrich_plan(self, plan: ChapterPlan) -> ChapterPlan:
        if plan.activity_items:
            return plan

        activities: list[LearningActivity] = []
        knowledge_points = plan.knowledge_points
        if knowledge_points:
            first_points = knowledge_points[: min(3, len(knowledge_points))]
            activities.append(
                LearningActivity(
                    activity_id=f"{plan.chapter_id}_act_01",
                    type="observation",
                    difficulty_level="basic",
                    prompt=(
                        "观看示范视频并阅读学习要点，记录本任务中最重要的概念、动作和安全注意事项。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id for point in first_points],
                    evidence_chunk_ids=[chunk_id for point in first_points for chunk_id in point.chunk_ids],
                    rubric=[
                        "能说出本任务的核心概念或操作目标。",
                        "能指出视频中需要重点观察的动作或工件状态。",
                        "能写出至少一条安全或质量注意事项。",
                    ],
                )
            )

        practice_points = [point for point in knowledge_points if point.difficulty_level in {"practice", "advanced"}]
        explanation_points = practice_points or knowledge_points[:1]
        if explanation_points:
            activities.append(
                LearningActivity(
                    activity_id=f"{plan.chapter_id}_act_02",
                    type="explanation",
                    difficulty_level="practice",
                    prompt=(
                        "选择一个实践类知识点，用自己的话说明操作步骤、判断依据和容易出错的地方。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id for point in explanation_points[:2]],
                    evidence_chunk_ids=[chunk_id for point in explanation_points[:2] for chunk_id in point.chunk_ids],
                    rubric=[
                        "表述包含动作、条件和注意事项。",
                        "能结合视频观察说明为什么要这样操作。",
                        "能提出一个需要教师现场确认的问题。",
                    ],
                )
            )

        if len(knowledge_points) >= 2:
            activities.append(
                LearningActivity(
                    activity_id=f"{plan.chapter_id}_act_03",
                    type="analysis",
                    difficulty_level="advanced",
                    prompt=(
                        "比较两个相关知识点：前一个知识点如何帮助你完成后一个操作或判断？"
                        "结合课堂实训场景写出你的分析。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id for point in knowledge_points[-2:]],
                    evidence_chunk_ids=plan.evidence_chunk_ids,
                    rubric=[
                        "能说明先修关系或知识迁移关系。",
                        "能把知识点迁移到同类工件或同类操作情境。",
                        "能说明自己的判断依据和改进建议。",
                    ],
                )
            )

        activity_labels = [_activity_to_label(activity) for activity in activities] or plan.activities
        return replace(plan, activities=activity_labels, activity_items=activities)


def _activity_to_label(activity: LearningActivity) -> str:
    return f"[{_difficulty_label(activity.difficulty_level)}·{_activity_type_label(activity.type)}] {activity.prompt}"


def _difficulty_label(level: str) -> str:
    return {"basic": "基础", "practice": "实操", "advanced": "拓展"}.get(level, level)


def _activity_type_label(activity_type: str) -> str:
    return {"observation": "观察任务", "explanation": "解释任务", "analysis": "迁移任务"}.get(activity_type, activity_type)
