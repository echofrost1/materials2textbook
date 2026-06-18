from __future__ import annotations

from textwrap import shorten

from materials2textbook.schemas import ChapterPlan, EvidenceChunk


def build_textbook_writer_messages(
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    title: str,
    max_chunk_chars: int = 1200,
) -> list[dict[str, str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    evidence_blocks: list[str] = []
    for plan in plans:
        evidence_blocks.append(f"Chapter: {plan.title}")
        for point in plan.knowledge_points:
            prerequisites = ", ".join(point.prerequisite_ids) if point.prerequisite_ids else "none"
            evidence_blocks.append(
                f"Knowledge point: {point.order_index}. {point.title}; "
                f"difficulty={point.difficulty_level}; cluster={point.cluster_id}; prerequisites={prerequisites}"
            )
            for chunk_id in point.chunk_ids:
                chunk = chunk_map.get(chunk_id)
                if not chunk:
                    continue
                content = shorten(" ".join(chunk.content.split()), width=max_chunk_chars, placeholder="...")
                start = chunk.metadata.get("start_time", "")
                end = chunk.metadata.get("end_time", "")
                source = chunk.metadata.get("source_video", "") or chunk.locator.original_path or chunk.locator.path
                keyframes = ";".join(chunk.locator.keyframe_paths)
                evidence_blocks.append(
                    "\n".join(
                        [
                            f"- chunk_id: {chunk.chunk_id}",
                            f"  source_type: {chunk.source_type}",
                            f"  source: {source} [{start}-{end}]",
                            f"  keyframes: {keyframes}",
                            f"  review_status: {chunk.review_status}",
                            f"  summary: {chunk.summary}",
                            f"  evidence: {content}",
                        ]
                    )
                )
        for case in plan.case_examples:
            evidence_blocks.append(
                "\n".join(
                    [
                        f"Case example: {case.title}",
                        f"  prompt: {case.prompt}",
                        f"  reference_answer: {case.reference_answer}",
                        f"  evidence_chunk_ids: {', '.join(case.evidence_chunk_ids)}",
                    ]
                )
            )

    system = (
        "你是面向中职/高职学生的焊接数字教材编写 Agent。"
        "你的任务不是写素材摘要，而是把已经筛选好的证据扩写成可教学的章节正文。"
        "必须严格依据用户提供的素材片段写作，不得补充素材中没有的新章节或事实、参数或工艺结论。"
        "输出 Markdown。每个核心知识点都要保留 chunk_id 证据引用。"
        "如果片段 review_status 不是 approved 或 Agent_Keep，需要标注“待人工复核”，不要把它写成最终定论。"
    )
    user = "\n".join(
        [
            f"请根据以下证据片段生成教材章节正文：{title}",
            "",
            "写作要求：",
            "1. 面向中职/高职学生，语言清楚、步骤化、可用于课堂教学。",
            "2. 保留章、节、知识点结构，不要只写概述。",
            "3. 每个知识点按“学习目标、知识讲解、操作步骤、工艺要点、常见错误、图/视频观察任务、小结、练习题”展开；如果某项证据不足，可以写“本节证据不足，暂不展开”。",
            "4. 每个知识点至少引用 2 条证据；证据不足时必须说明缺口，不要编造。",
            "5. 证据引用格式必须写成：`证据：C000001` 或 `证据：PPT_A000001_S001`。",
            "6. 视频、图片、PPT 页要转化为观察任务，例如“观看某片段时重点观察焊枪角度、送丝动作、收弧方式”。",
            "7. 片段 ASR 质量明显差、时间码待确认或 review_status 为 Pending 时，必须写成待复核表述，不要强行断言。",
            "8. 如果章节计划提供案例示例，需要保留例题、参考分析和 evidence_chunk_ids。",
            "9. 每章正文目标不少于 3000 字；如果证据不足导致无法达到，先保证证据可靠，并在章末列出缺口。",
            "10. 不要输出内部字段解释，不要写“作为 AI”之类的话。",
            "",
            "证据片段：",
            "\n\n".join(evidence_blocks),
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
