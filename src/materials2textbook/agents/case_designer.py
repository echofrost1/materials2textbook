from __future__ import annotations

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
                    title=f"{point.title}证据分析示例",
                    prompt=(
                        f"在课堂实训中，一名新手学生需要完成“{point.title}”相关任务。"
                        "请结合证据片段，说明关键观察点或操作判断，并迁移到同类现场任务中应如何处理。"
                    ),
                    reference_answer=(
                        f"可先定位证据 {', '.join(evidence_ids[:3])}。"
                        f"{evidence_summaries}"
                        "分析时先面向新手学生解释可观察证据，再说明同类项目或现场任务中的迁移判断。"
                        "回答时应保留 chunk_id，并对待复核片段使用谨慎表述。"
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
        summary = chunk.summary or chunk.content
        summary = " ".join(summary.split())
        if len(summary) > 80:
            summary = summary[:77] + "..."
        review_note = ""
        if chunk.review_status and "approved" not in chunk.review_status.lower():
            review_note = "该片段仍需人工复核，不能写成已确认事实。"
        parts.append(f" {chunk_id} 显示：{summary}。{review_note}")
    return "".join(parts)
