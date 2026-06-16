from __future__ import annotations

import re
from dataclasses import replace

from materials2textbook.schemas import CaseExample, ChapterPlan, EvidenceChunk


class CaseDesignerAgent:
    """Create evidence-grounded worked examples for textbook chapters."""

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk]) -> list[ChapterPlan]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        return [self._enrich_plan(plan, chunk_map) for plan in plans]

    def _enrich_plan(self, plan: ChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> ChapterPlan:
        if plan.case_examples:
            return plan

        examples: list[CaseExample] = []
        practice_points = [point for point in plan.knowledge_points if point.difficulty_level in {"practice", "advanced"}]
        selected_points = (practice_points or plan.knowledge_points)[:2]
        for index, point in enumerate(selected_points, start=1):
            evidence_ids = [chunk_id for chunk_id in point.chunk_ids if chunk_id in chunk_map]
            if not evidence_ids:
                continue
            evidence_summaries = _summarize_evidence(evidence_ids, chunk_map)
            examples.append(
                CaseExample(
                    case_id=f"{plan.chapter_id}_case_{index:02d}",
                    title=f"{point.title}课堂应用示例",
                    prompt=(
                        f"在课堂实训中，一名新手学生需要完成“{point.title}”相关任务。"
                        "请说明关键观察点、操作判断和同类现场任务中的处理方法。"
                    ),
                    reference_answer=(
                        f"{evidence_summaries}"
                        "分析时先说明学生能观察到的现象，再给出操作判断和同类任务中的迁移方法。"
                        "操作时应结合课堂示范确认关键动作、工件状态和安全要求。"
                    ),
                    target_knowledge_point_ids=[point.knowledge_point_id],
                    evidence_chunk_ids=evidence_ids,
                )
            )

        return replace(plan, case_examples=examples)


def _summarize_evidence(evidence_ids: list[str], chunk_map: dict[str, EvidenceChunk]) -> str:
    parts: list[str] = []
    for chunk_id in evidence_ids[:3]:
        chunk = chunk_map[chunk_id]
        if _is_unapproved_video_transcript(chunk):
            continue
        summary = chunk.summary or chunk.content
        summary = _clean_student_text(summary)
        if not summary or _looks_like_internal_review_text(summary) or _looks_like_low_quality_asr(summary):
            continue
        if len(summary) > 80:
            summary = summary[:77] + "..."
        review_note = ""
        if chunk.review_status and "approved" not in chunk.review_status.lower():
            review_note = "该要点需要结合课堂示范进一步确认。"
        parts.append(f"学习材料提示：{summary}。{review_note}")
    return "".join(parts) or "可先观察示范视频中的关键动作和工件状态，再结合学习要点完成判断。"


def _clean_student_text(text: str) -> str:
    normalized = " ".join(str(text).split())
    normalized = re.sub(r"\[\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\s*-->\s*\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\]", "", normalized)
    normalized = re.sub(r"`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?", "", normalized)
    normalized = re.sub(r"[\w\u4e00-\u9fff（）()、.-]+\s*\.\s*(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)", "", normalized, flags=re.IGNORECASE)
    return normalized.strip(" ：:-，。")


def _looks_like_internal_review_text(text: str) -> bool:
    internal_terms = ("候选片段", "待人工", "待 agent", "处理队列", "人工复核", "确认边界", "时间码")
    normalized = text.lower()
    return any(term.lower() in normalized for term in internal_terms)


def _looks_like_low_quality_asr(text: str) -> bool:
    bad_terms = ("無幾", "壓護", "夫妻娘", "罕", "隱", "鈉", "漢师", "龙磁")
    return any(term in text for term in bad_terms)


def _is_unapproved_video_transcript(chunk: EvidenceChunk) -> bool:
    is_video = chunk.source_type in {"video_segment", "video", "audio_segment"}
    return is_video and "approved" not in chunk.review_status.lower()
