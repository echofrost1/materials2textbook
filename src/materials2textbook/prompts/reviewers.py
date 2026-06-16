from __future__ import annotations

from textwrap import shorten

from materials2textbook.schemas import ChapterPlan, EvidenceChunk


def build_evidence_review_messages(
    plan: ChapterPlan,
    chunks: list[EvidenceChunk],
    draft_markdown: str,
    max_chunk_chars: int = 700,
    max_draft_chars: int = 6000,
) -> list[dict[str, str]]:
    chunk_blocks = []
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    for chunk_id in plan.evidence_chunk_ids:
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        source = chunk.metadata.get("source_video", "") or chunk.locator.original_path or chunk.locator.path
        start = chunk.metadata.get("start_time", "")
        end = chunk.metadata.get("end_time", "")
        content = shorten(" ".join(chunk.content.split()), width=max_chunk_chars, placeholder="...")
        chunk_blocks.append(
            "\n".join(
                [
                    f"- chunk_id: {chunk.chunk_id}",
                    f"  source: {source} [{start}-{end}]",
                    f"  review_status: {chunk.review_status}",
                    f"  summary: {chunk.summary}",
                    f"  evidence: {content}",
                ]
            )
        )

    system = (
        "你是教材事实与证据审核 Agent。"
        "只根据给定教材草稿和证据片段判断，不得引入外部知识。"
        "重点检查：正文断言是否被证据支持、引用是否覆盖核心知识点、是否把待复核片段写成确定事实。"
        "只输出 JSON 数组，不要输出 Markdown。"
    )
    user = "\n".join(
        [
            f"请审核章节：{plan.title}",
            "",
            "输出 JSON 数组，每个元素必须包含：",
            '- "severity": "high" | "medium" | "low"',
            '- "location": 相关 chunk_id、知识点或章节 id',
            '- "message": 问题描述',
            '- "suggestion": 修改建议',
            "",
            "如果没有问题，输出 []。",
            "",
            "章节计划：",
            _render_plan(plan),
            "",
            "证据片段：",
            "\n\n".join(chunk_blocks),
            "",
            "教材草稿：",
            shorten(draft_markdown, width=max_draft_chars, placeholder="\n\n...草稿过长已截断...\n\n"),
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_pedagogy_review_messages(
    plan: ChapterPlan,
    draft_markdown: str,
    max_draft_chars: int = 6000,
) -> list[dict[str, str]]:
    system = (
        "你是职业教育数字教材教学质量审核 Agent。"
        "只审核教学设计质量，不做素材外事实扩写。"
        "重点检查：学习目标是否可评价、知识点顺序是否合理、难度是否递进、任务活动和练习是否支撑目标。"
        "只输出 JSON 数组，不要输出 Markdown。"
    )
    user = "\n".join(
        [
            f"请审核章节教学设计：{plan.title}",
            "",
            "输出 JSON 数组，每个元素必须包含：",
            '- "severity": "high" | "medium" | "low"',
            '- "location": 章节 id、知识点 id 或任务位置',
            '- "message": 问题描述',
            '- "suggestion": 修改建议',
            "",
            "如果没有问题，输出 []。",
            "",
            "章节计划：",
            _render_plan(plan),
            "",
            "教材草稿：",
            shorten(draft_markdown, width=max_draft_chars, placeholder="\n\n...草稿过长已截断...\n\n"),
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _render_plan(plan: ChapterPlan) -> str:
    lines = [
        f"chapter_id: {plan.chapter_id}",
        f"title: {plan.title}",
        "learning_goals:",
        *[f"- {goal}" for goal in plan.learning_goals],
        "knowledge_points:",
    ]
    for point in plan.knowledge_points:
        prerequisites = ", ".join(point.prerequisite_ids) if point.prerequisite_ids else "none"
        lines.append(
            f"- {point.knowledge_point_id}: {point.order_index}. {point.title}; "
            f"difficulty={point.difficulty_level}; cluster={point.cluster_id}; "
            f"prerequisites={prerequisites}; chunks={', '.join(point.chunk_ids)}"
        )
    lines.append("learning_path:")
    lines.extend(f"- {point_id}" for point_id in plan.learning_path)
    lines.append("activities:")
    if plan.activity_items:
        for activity in plan.activity_items:
            lines.append(
                f"- {activity.activity_id}: type={activity.type}; difficulty={activity.difficulty_level}; "
                f"targets={', '.join(activity.target_knowledge_point_ids)}; evidence={', '.join(activity.evidence_chunk_ids)}"
            )
            lines.append(f"  prompt: {activity.prompt}")
            if activity.rubric:
                lines.append(f"  rubric: {' | '.join(activity.rubric)}")
    else:
        lines.extend(f"- {activity}" for activity in plan.activities)
    return "\n".join(lines)
