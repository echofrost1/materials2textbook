from __future__ import annotations

from materials2textbook.schemas import ChapterPlan, EvidenceChunk, ReviewIssue, ReviewReport


class EvidenceReviewerAgent:
    """Check whether chapter evidence is usable enough for drafting."""

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk]) -> dict[str, list[ReviewIssue]]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        issues_by_chapter: dict[str, list[ReviewIssue]] = {}
        for plan in plans:
            issues: list[ReviewIssue] = []
            for chunk_id in plan.evidence_chunk_ids:
                chunk = chunk_map.get(chunk_id)
                if not chunk:
                    issues.append(
                        ReviewIssue("high", chunk_id, "章节计划引用了不存在的证据片段。", "重新生成章节计划或补齐证据库。")
                    )
                    continue
                if not chunk.content.strip():
                    issues.append(
                        ReviewIssue("high", chunk_id, "证据片段缺少正文或转写文本。", "补充转写文本后再进入教材写作。")
                    )
                if "pending" in chunk.review_status.lower():
                    issues.append(
                        ReviewIssue("medium", chunk_id, "证据片段仍处于待人工复核状态。", "人工确认时间码和片段边界。")
                    )
                if chunk.score.teaching_value < 0.5:
                    issues.append(
                        ReviewIssue("medium", chunk_id, "片段教学价值评分偏低或缺失。", "重新评分或从教材草稿中降级为补充材料。")
                    )
            issues_by_chapter[plan.chapter_id] = issues
        return issues_by_chapter


class PedagogyReviewerAgent:
    """Check chapter structure and teaching completeness."""

    def run(self, plans: list[ChapterPlan]) -> dict[str, list[ReviewIssue]]:
        issues_by_chapter: dict[str, list[ReviewIssue]] = {}
        for plan in plans:
            issues: list[ReviewIssue] = []
            if len(plan.knowledge_points) < 2:
                issues.append(
                    ReviewIssue("low", plan.chapter_id, "章节知识点数量较少。", "确认是否需要补充原理、操作、注意事项或案例。")
                )
            if not plan.activities:
                issues.append(
                    ReviewIssue("medium", plan.chapter_id, "章节缺少学习活动。", "至少补充观察任务、思考题或操作练习。")
                )
            if not plan.learning_goals:
                issues.append(
                    ReviewIssue("medium", plan.chapter_id, "章节缺少学习目标。", "补充可评价的学习目标。")
                )
            issues_by_chapter[plan.chapter_id] = issues
        return issues_by_chapter


class ReviewComposer:
    def run(
        self,
        plans: list[ChapterPlan],
        fact_issues: dict[str, list[ReviewIssue]],
        pedagogy_issues: dict[str, list[ReviewIssue]],
    ) -> list[ReviewReport]:
        reports: list[ReviewReport] = []
        for plan in plans:
            chapter_fact_issues = fact_issues.get(plan.chapter_id, [])
            chapter_pedagogy_issues = pedagogy_issues.get(plan.chapter_id, [])
            suggestions = [issue.suggestion for issue in chapter_fact_issues + chapter_pedagogy_issues]
            reports.append(
                ReviewReport(
                    chapter_id=plan.chapter_id,
                    chapter_title=plan.title,
                    fact_issues=chapter_fact_issues,
                    pedagogy_issues=chapter_pedagogy_issues,
                    revision_suggestions=suggestions,
                )
            )
        return reports
