from __future__ import annotations

from collections import defaultdict

from materials2textbook.schemas import EvidenceChunk, OutlineSection, OutlineTopic, TextbookOutline


class OutlinePlannerAgent:
    """Build a strict three-level outline from upstream evidence chunks."""

    def run(self, chunks: list[EvidenceChunk]) -> list[TextbookOutline]:
        by_chapter: dict[str, list[EvidenceChunk]] = defaultdict(list)
        for chunk in chunks:
            by_chapter[chunk.recommended_chapter or "待规划章节"].append(chunk)

        outlines: list[TextbookOutline] = []
        for chapter_index, (chapter_title, chapter_chunks) in enumerate(by_chapter.items(), start=1):
            by_section: dict[str, list[EvidenceChunk]] = defaultdict(list)
            for chunk in chapter_chunks:
                section_title = chunk.material_block or chunk.subject or "未分类素材"
                by_section[section_title].append(chunk)

            sections: list[OutlineSection] = []
            for section_index, (section_title, section_chunks) in enumerate(by_section.items(), start=1):
                by_topic: dict[str, list[EvidenceChunk]] = defaultdict(list)
                for chunk in section_chunks:
                    by_topic[chunk.title].append(chunk)

                topics = [
                    OutlineTopic(
                        topic_id=f"topic_{chapter_index:02d}_{section_index:02d}_{topic_index:02d}",
                        title=topic_title,
                        chunk_ids=[chunk.chunk_id for chunk in topic_chunks],
                        summary=topic_chunks[0].summary,
                    )
                    for topic_index, (topic_title, topic_chunks) in enumerate(by_topic.items(), start=1)
                ]
                sections.append(
                    OutlineSection(
                        section_id=f"section_{chapter_index:02d}_{section_index:02d}",
                        title=section_title,
                        topics=topics,
                    )
                )

            outlines.append(
                TextbookOutline(
                    chapter_id=f"chapter_{chapter_index:02d}",
                    title=chapter_title,
                    sections=sections,
                )
            )
        return outlines


def render_outline_markdown(outlines: list[TextbookOutline], title: str) -> str:
    lines = [f"# {title} 教材目录", ""]
    if not outlines:
        lines.extend(["> 当前没有可用于生成目录的素材片段。", ""])
        return "\n".join(lines)

    for chapter_index, chapter in enumerate(outlines, start=1):
        lines.extend([f"## 第{chapter_index}章 {chapter.title}", ""])
        for section_index, section in enumerate(chapter.sections, start=1):
            lines.extend([f"### {chapter_index}.{section_index} {section.title}", ""])
            for topic_index, topic in enumerate(section.topics, start=1):
                lines.append(f"#### {chapter_index}.{section_index}.{topic_index} {topic.title}")
                if topic.chunk_ids:
                    lines.append(f"- 证据片段：{', '.join(topic.chunk_ids)}")
                if topic.summary:
                    lines.append(f"- 素材摘要：{topic.summary}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"
