from __future__ import annotations

import re
from textwrap import shorten

from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.textbook_writer import build_textbook_writer_messages
from materials2textbook.schemas import ChapterPlan, EvidenceChunk


class TextbookWriterAgent:
    """Draft a Markdown textbook from chapter plans and evidence chunks."""

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        use_llm: bool = False,
        domain_config: DomainConfig | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm
        self.domain_config = domain_config or default_domain_config()
        self.last_generation_mode = "rule"
        self.last_generation_warning = ""

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk], title: str) -> str:
        if self.use_llm:
            if self.llm_provider is not None:
                try:
                    messages = build_textbook_writer_messages(plans, chunks, title, domain_config=self.domain_config)
                    llm_markdown = self.llm_provider.generate(messages).rstrip()
                    if _llm_markdown_is_usable(llm_markdown, plans, chunks, title):
                        self.last_generation_mode = "llm"
                        self.last_generation_warning = ""
                        return llm_markdown + "\n"
                    self.last_generation_warning = "LLM output was empty, too short, missing titles, or missing evidence citations."
                except Exception as exc:  # pragma: no cover - exact provider failures vary by backend.
                    self.last_generation_warning = f"LLM generation failed: {exc}"
            else:
                self.last_generation_warning = "LLM generation requested but no provider was configured."

            self.last_generation_mode = "rule_fallback"
            return self._run_rule_writer(plans, chunks, title)

        self.last_generation_mode = "rule"
        self.last_generation_warning = ""
        return self._run_rule_writer(plans, chunks, title)

    def _run_rule_writer(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk], title: str) -> str:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        lines = [
            f"# {title}",
            "",
            "> 这是基于章节素材包生成的教材样稿，段落中的证据编号用于教师追溯和后续核验。",
            "",
        ]

        for plan in plans:
            lines.extend([f"## {plan.title}", ""])
            lines.append("### 学习目标")
            for goal in plan.learning_goals:
                lines.append(f"- {goal}")
            lines.append("")

            chapter_citations = _format_citations(plan.evidence_chunk_ids[:3])
            intro = (
                f"本章围绕“{plan.title}”展开，重点把资料中的原理说明、操作示范、图文资料和质量判断要求"
                f"组织成可学习、可观察、可练习的教材内容。学习时应先理解核心概念，再结合视频或图文证据"
                f"观察关键动作，最后用练习题完成迁移判断。{chapter_citations}"
            )
            lines.extend(["### 章节导入", intro, ""])

            lines.append("### 教材正文")
            for point in plan.knowledge_points:
                evidence_items = [chunk_map[chunk_id] for chunk_id in point.chunk_ids if chunk_id in chunk_map]
                lines.extend(_render_point_section(point, evidence_items))
            lines.append("")

            not_ready_points = [
                point.title
                for point in plan.knowledge_points
                if not any(chunk_id in chunk_map for chunk_id in point.chunk_ids)
            ]
            if not_ready_points:
                lines.extend(
                    [
                        "### 证据不足的知识点",
                        "以下知识点在当前章节素材包中缺少可用于展开写作的证据，暂不扩写完整操作步骤："
                        + "、".join(not_ready_points)
                        + "。",
                        "",
                    ]
                )

            if plan.case_examples:
                lines.append("### 案例示例")
                for example in plan.case_examples:
                    lines.extend([f"#### {example.title}", ""])
                    lines.append(f"**例题：** {example.prompt}")
                    lines.append("")
                    lines.append(f"**参考分析：** {example.reference_answer}")
                    if example.evidence_chunk_ids:
                        lines.append(_format_citations(example.evidence_chunk_ids))
                    lines.append("")

            lines.append("### 学习活动")
            if plan.activity_items:
                for activity in plan.activity_items:
                    lines.append(f"#### {activity.type}")
                    lines.append(f"{activity.prompt}")
                    if activity.evidence_chunk_ids:
                        lines.append(_format_citations(activity.evidence_chunk_ids[:3]))
                    if activity.rubric:
                        lines.append("评价要点：")
                        for item in activity.rubric:
                            lines.append(f"- {item}")
                    lines.append("")
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


def _render_point_section(point, evidence_items: list[EvidenceChunk]) -> list[str]:
    lines: list[str] = [f"#### {point.order_index or ''} {point.title}".strip(), ""]
    if not evidence_items:
        lines.extend(
            [
                "当前素材包中缺少足够证据，本节只保留目录位置，暂不展开完整教材正文。",
                "",
            ]
        )
        return lines

    primary_chunks = _select_representative_chunks(evidence_items, limit=3)
    video_chunks = [chunk for chunk in evidence_items if _is_video_chunk(chunk)]
    document_chunks = [chunk for chunk in evidence_items if not _is_video_chunk(chunk)]
    citations = _format_citations([chunk.chunk_id for chunk in primary_chunks])

    lines.extend(
        [
            "##### 学习目标",
            f"学习本知识点后，应能说明“{point.title}”的基本含义、适用场景和现场观察重点，"
            f"并能把教材中的证据转化为操作判断。{citations}",
            "",
        ]
    )

    concept_text = _compose_concept_text(point.title, primary_chunks, point.summary)
    lines.extend(["##### 知识讲解", concept_text + citations, ""])

    operation_text = _compose_operation_text(point.title, video_chunks or primary_chunks)
    lines.extend(["##### 操作与观察任务", operation_text + _format_citations([chunk.chunk_id for chunk in (video_chunks or primary_chunks)[:2]]), ""])

    quality_text = _compose_quality_text(point.title, document_chunks or primary_chunks)
    lines.extend(["##### 工艺要点与常见错误", quality_text + _format_citations([chunk.chunk_id for chunk in (document_chunks or primary_chunks)[:2]]), ""])

    exercise_citations = _format_citations([chunk.chunk_id for chunk in primary_chunks[:2]])
    lines.extend(
        [
            "##### 小结与练习",
            f"本节小结：学习“{point.title}”时，应把概念理解、动作观察和质量判断连起来，"
            f"避免只记术语而不能解释现场现象。{exercise_citations}",
            "",
            f"练习题：结合本节资料，说明“{point.title}”在实际焊接任务中的关键判断点；"
            "回答时至少引用一个观察现象，并说明该现象可能对应的操作原因或质量风险。"
            + exercise_citations,
            "",
            "本节证据覆盖："
            + "、".join(chunk.chunk_id for chunk in evidence_items if chunk.chunk_id)
            + "。",
            "",
        ]
    )
    return lines


def _llm_markdown_is_usable(
    markdown: str,
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    title: str,
) -> bool:
    text = markdown.strip()
    if len(text) < 500:
        return False
    if any(marker in text for marker in ("作为AI", "作为 AI", "无法生成", "无法完成")):
        return False

    expected_titles = [title]
    expected_titles.extend(plan.title for plan in plans)
    expected_titles.extend(point.title for plan in plans for point in plan.knowledge_points)
    if not any(expected_title and expected_title in text for expected_title in expected_titles):
        return False

    chunk_ids = [chunk.chunk_id for chunk in chunks if chunk.chunk_id]
    if chunk_ids:
        required_citation_count = min(3, len(set(chunk_ids)))
        if _count_distinct_referenced_chunks(text, chunk_ids) < required_citation_count:
            return False
    return True


def _count_distinct_referenced_chunks(text: str, chunk_ids: list[str]) -> int:
    matched = set()
    for chunk_id in chunk_ids:
        if chunk_id and chunk_id in text:
            matched.add(chunk_id)
    return len(matched)

def _compose_concept_text(title: str, chunks: list[EvidenceChunk], point_summary: str = "") -> str:
    snippets = _snippets(chunks, width=150, limit=2)
    if point_summary:
        base = _clean_sentence(point_summary)
    elif snippets:
        base = "；".join(snippets)
    else:
        base = f"{title}是本章需要掌握的核心内容。"
    return (
        f"{title}的学习首先要建立清晰的概念边界。根据当前素材，{base}。"
        "教材讲解时应把术语、设备条件、工艺现象和质量要求放在同一任务情境中理解，"
        "使学生能够知道“是什么”、也能说明“为什么这样做”。"
    )


def _compose_operation_text(title: str, chunks: list[EvidenceChunk]) -> str:
    snippets = _snippets(chunks, width=120, limit=2)
    if snippets:
        observed = "；".join(snippets)
        return (
            f"学习{title}时，应把素材中的示范片段或图文说明转化为观察任务。"
            f"观看或阅读时，先记录能够直接看到的动作、设备状态和工件状态，例如：{observed}。"
            "随后再判断这些现象与焊接姿态、热输入、保护效果或焊缝成形之间的关系。"
        )
    return (
        f"学习{title}时，应先观察教师示范中的动作顺序、工具位置和工件状态，"
        "再用自己的话说明这些动作对焊接质量的影响。"
    )


def _compose_quality_text(title: str, chunks: list[EvidenceChunk]) -> str:
    snippets = _snippets(chunks, width=120, limit=2)
    detail = "；".join(snippets) if snippets else "素材中尚未提供足够的缺陷或质量判断细节"
    return (
        f"{title}的质量控制不能只看动作是否完成，还要观察动作是否稳定、参数是否匹配、"
        f"焊接区域是否受到有效保护以及焊缝成形是否符合要求。当前可用证据提示：{detail}。"
        "若证据中存在待复核状态，课堂使用时应把这些内容作为训练提示，而不是未经确认的最终结论。"
    )


def _select_representative_chunks(chunks: list[EvidenceChunk], limit: int) -> list[EvidenceChunk]:
    def key(chunk: EvidenceChunk) -> tuple[float, float, int]:
        status = chunk.review_status.lower()
        status_score = 2 if "approved" in status or "agent_keep" in status else 1 if "pending" in status else 0
        return (status_score, chunk.score.teaching_value, len(chunk.content))

    return sorted(chunks, key=key, reverse=True)[:limit]


def _is_video_chunk(chunk: EvidenceChunk) -> bool:
    value = chunk.source_type.lower()
    return value in {"video", "video_segment", "audio", "audio_segment"} or bool(chunk.metadata.get("source_video"))


def _snippets(chunks: list[EvidenceChunk], *, width: int, limit: int) -> list[str]:
    snippets: list[str] = []
    for chunk in chunks:
        text = _clean_sentence(chunk.summary or chunk.content or chunk.title)
        if not text:
            continue
        snippets.append(shorten(text, width=width, placeholder="..."))
        if len(snippets) >= limit:
            break
    return snippets


def _clean_sentence(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.strip("。；;，,")
    return text


def _format_citations(chunk_ids: list[str]) -> str:
    unique = []
    for chunk_id in chunk_ids:
        if chunk_id and chunk_id not in unique:
            unique.append(chunk_id)
    if not unique:
        return ""
    return " 证据：" + "、".join(unique) + "。"
