from __future__ import annotations

from textwrap import shorten

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.textbook_writer import build_textbook_writer_messages
from materials2textbook.schemas import ChapterPlan, EvidenceChunk


class TextbookWriterAgent:
    """Draft a Markdown textbook from chapter plans and evidence chunks."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk], title: str) -> str:
        if self.use_llm:
            if self.llm_provider is None:
                raise RuntimeError("TextbookWriterAgent was asked to use LLM, but no provider was configured.")
            messages = build_textbook_writer_messages(plans, chunks, title)
            return self.llm_provider.generate(messages).rstrip() + "\n"

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

            lines.append("### 学习路径")
            for point in plan.knowledge_points:
                prerequisites = ", ".join(point.prerequisite_ids) if point.prerequisite_ids else "无"
                lines.append(
                    f"- {point.order_index}. {point.title}"
                    f"（难度：{point.difficulty_level}；聚类：{point.cluster_id}；先修：{prerequisites}）"
                )
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

            if plan.case_examples:
                lines.append("### 案例示例")
                for example in plan.case_examples:
                    lines.extend([f"#### {example.title}", ""])
                    lines.append(f"- 例题：{example.prompt}")
                    lines.append(f"- 参考分析：{example.reference_answer}")
                    if example.evidence_chunk_ids:
                        lines.append(f"- 证据：{', '.join(example.evidence_chunk_ids)}")
                    lines.append("")

            lines.append("### 学习活动")
            if plan.activity_items:
                for activity in plan.activity_items:
                    lines.append(f"#### {activity.difficulty_level} · {activity.type}")
                    lines.append(f"- 任务：{activity.prompt}")
                    if activity.evidence_chunk_ids:
                        lines.append(f"- 证据：{', '.join(activity.evidence_chunk_ids)}")
                    if activity.rubric:
                        lines.append("- 评价要点：")
                        for item in activity.rubric:
                            lines.append(f"  - {item}")
            else:
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
