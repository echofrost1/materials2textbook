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
        evidence_ids = plan.evidence_chunk_ids

        if knowledge_points:
            first_points = knowledge_points[: min(3, len(knowledge_points))]
            activities.append(
                LearningActivity(
                    activity_id=f"{plan.chapter_id}_act_01",
                    type="observation",
                    difficulty_level="basic",
                    prompt=(
                        "观察本任务的素材片段，定位每个知识点对应的证据编号、来源和时间码，"
                        "标出仍需人工复核的片段。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id for point in first_points],
                    evidence_chunk_ids=[chunk_id for point in first_points for chunk_id in point.chunk_ids],
                    rubric=[
                        "能准确写出至少两个 chunk_id。",
                        "能说明证据来自视频还是文档。",
                        "能识别待人工复核片段。",
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
                        "选择一个实践类知识点，用自己的话复述操作要点，并用证据片段说明"
                        "该要点为什么成立。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id for point in explanation_points[:2]],
                    evidence_chunk_ids=[chunk_id for point in explanation_points[:2] for chunk_id in point.chunk_ids],
                    rubric=[
                        "表述包含动作、条件和注意事项。",
                        "至少引用一个有效 chunk_id。",
                        "没有把待复核证据写成确定事实。",
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
                        "沿学习路径比较前后两个知识点的关系：前一个知识点如何支撑后一个操作"
                        "或判断？写出你的分析依据。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id for point in knowledge_points[-2:]],
                    evidence_chunk_ids=evidence_ids,
                    rubric=[
                        "能说明先修关系或知识迁移关系。",
                        "能引用相关证据片段支撑分析。",
                        "能提出一个需要教师确认的问题。",
                    ],
                )
            )

        activity_labels = [_activity_to_label(activity) for activity in activities] or plan.activities
        return replace(plan, activities=activity_labels, activity_items=activities)


def _activity_to_label(activity: LearningActivity) -> str:
    evidence = ", ".join(activity.evidence_chunk_ids[:3])
    if len(activity.evidence_chunk_ids) > 3:
        evidence += "..."
    return f"[{activity.difficulty_level}/{activity.type}] {activity.prompt} 证据：{evidence or '无'}"
