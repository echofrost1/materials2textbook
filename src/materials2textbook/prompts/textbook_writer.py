from __future__ import annotations

from textwrap import shorten

from materials2textbook.schemas import ChapterPlan, EvidenceChunk


def build_textbook_writer_messages(
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    title: str,
    max_chunk_chars: int = 900,
) -> list[dict[str, str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    evidence_blocks: list[str] = []
    for plan in plans:
        evidence_blocks.append(f"章：{plan.title}")
        for point in plan.knowledge_points:
            evidence_blocks.append(f"知识点：{point.title}")
            for chunk_id in point.chunk_ids:
                chunk = chunk_map.get(chunk_id)
                if not chunk:
                    continue
                content = shorten(" ".join(chunk.content.split()), width=max_chunk_chars, placeholder="...")
                start = chunk.metadata.get("start_time", "")
                end = chunk.metadata.get("end_time", "")
                source = chunk.metadata.get("source_video", "") or chunk.locator.original_path
                evidence_blocks.append(
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
        "你是面向中职/高职学生的数字教材编写 Agent。"
        "必须严格依据用户提供的素材片段写作，不得补充素材中没有的新章节或事实。"
        "输出 Markdown。每个核心知识点都要保留 chunk_id 证据引用。"
        "如果片段 review_status 不是 approved，需要明确标注“待人工复核”。"
    )
    user = "\n".join(
        [
            f"请根据以下证据片段生成教材草稿：{title}",
            "",
            "写作要求：",
            "1. 面向中职/高职学生，语言清楚、步骤化。",
            "2. 保留三级目录对应的章节结构。",
            "3. 不主动补充素材之外的新章节。",
            "4. 保留证据引用，格式如：`证据：C000001`。",
            "5. 片段 ASR 质量明显差时，写成待复核表述，不要强行断言。",
            "",
            "证据片段：",
            "\n\n".join(evidence_blocks),
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
