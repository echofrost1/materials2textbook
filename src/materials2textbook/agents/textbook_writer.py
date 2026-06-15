from __future__ import annotations

from textwrap import shorten

from materials2textbook.schemas import ChapterPlan, EvidenceChunk


class TextbookWriterAgent:
    """Draft a Markdown textbook from chapter plans and evidence chunks."""

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk], title: str) -> str:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        lines = [
            f"# {title}",
            "",
            "> 这是多智能体编排框架生成的教材草稿，内容需要结合人工审核结果继续修订。",
            "",
        ]

        for plan in plans:
            lines.extend([f"## {plan.title}", ""])
            lines.append("### 学习目标")
            for goal in plan.learning_goals:
                lines.append(f"- {goal}")
            lines.append("")

            lines.append("### 知识点")
            for point in plan.knowledge_points:
                lines.extend([f"#### {point.title}", ""])
                evidence_items = [chunk_map[chunk_id] for chunk_id in point.chunk_ids if chunk_id in chunk_map]
                if point.summary:
                    lines.append(point.summary)
                    lines.append("")
                for chunk in evidence_items:
                    snippet = shorten(" ".join(chunk.content.split()), width=180, placeholder="...")
                    locator = self._format_locator(chunk)
                    lines.append(f"- 证据 `{chunk.chunk_id}`：{snippet}")
                    lines.append(f"  来源：{locator}")
                    if chunk.review_status and "approved" not in chunk.review_status.lower():
                        lines.append(f"  状态：{chunk.review_status}，建议人工复核后再作为正式教材依据。")
                lines.append("")

            lines.append("### 学习活动")
            for activity in plan.activities:
                lines.append(f"- {activity}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _format_locator(self, chunk: EvidenceChunk) -> str:
        start = chunk.metadata.get("start_time", "")
        end = chunk.metadata.get("end_time", "")
        source = chunk.metadata.get("source_video", "") or chunk.locator.original_path
        if start or end:
            return f"{source} [{start}-{end}]"
        return source or chunk.locator.path or chunk.locator.original_path
