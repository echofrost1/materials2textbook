from __future__ import annotations

from pathlib import Path

from materials2textbook.schemas import DigitalBook, DigitalBookBlock, ReviewIssue


class DigitalBookReviewerAgent:
    """Review whether the front-end digital-book package is structurally usable."""

    REQUIRED_BLOCK_TYPES = {
        "scenario": "缺少情境导入。",
        "learning_nav": "缺少学习导航。",
        "implementation": "缺少任务实施正文。",
        "video": "缺少视频资源块。",
        "assessment": "缺少任务评价。",
        "exercises": "缺少思考与练习。",
    }

    def run(
        self,
        book: DigitalBook,
        known_chunk_ids: set[str],
        package_dir: Path,
    ) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if not book.projects:
            issues.append(ReviewIssue("high", book.book_id, "电子教材缺少项目。", "至少生成一个项目和一个任务。"))
            return issues

        for project in book.projects:
            if not project.learning_goals:
                issues.append(
                    ReviewIssue("medium", project.project_id, "项目缺少学习目标。", "为项目补充可评价的学习目标。")
                )
            if not project.ability_map:
                issues.append(
                    ReviewIssue("low", project.project_id, "项目缺少能力图谱。", "补充能力图谱，说明知识、技能和素养要求。")
                )
            if not project.tasks:
                issues.append(ReviewIssue("high", project.project_id, "项目缺少任务。", "至少生成一个任务。"))
                continue

            for task in project.tasks:
                block_types = {block.type for block in task.blocks}
                for required_type, message in self.REQUIRED_BLOCK_TYPES.items():
                    if required_type not in block_types:
                        issues.append(
                            ReviewIssue(
                                "medium" if required_type != "implementation" else "high",
                                task.task_id,
                                message,
                                "补齐项目化教材的任务结构后再交付前端阅读器。",
                            )
                        )
                if not task.knowledge_points:
                    issues.append(ReviewIssue("medium", task.task_id, "任务缺少知识点。", "从章节计划补充知识点列表。"))
                if not task.evidence_chunk_ids:
                    issues.append(
                        ReviewIssue("high", task.task_id, "任务缺少证据引用。", "保留进入该任务的 evidence chunk_id。")
                    )
                for chunk_id in task.evidence_chunk_ids:
                    if chunk_id not in known_chunk_ids:
                        issues.append(
                            ReviewIssue(
                                "high",
                                chunk_id,
                                "任务引用了不存在的证据片段。",
                                "删除该引用，或从上游补齐对应 EvidenceChunk。",
                            )
                        )
                for block in task.blocks:
                    issues.extend(self._review_block(block, known_chunk_ids, package_dir))
        return issues

    def _review_block(
        self,
        block: DigitalBookBlock,
        known_chunk_ids: set[str],
        package_dir: Path,
    ) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        if block.type in {"scenario", "implementation"} and not block.markdown.strip():
            issues.append(ReviewIssue("medium", block.block_id, f"{block.title} 缺少正文。", "补充可阅读的 Markdown 正文。"))
        if block.type in {"learning_nav", "assessment", "exercises"} and not block.items:
            issues.append(ReviewIssue("medium", block.block_id, f"{block.title} 缺少条目。", "补充列表条目。"))
        if block.type == "video":
            if not block.src:
                issues.append(ReviewIssue("high", block.block_id, "视频块缺少 src。", "绑定可播放的 mp4 视频资源。"))
            else:
                video_path = package_dir / block.src
                if not video_path.exists():
                    issues.append(
                        ReviewIssue("high", block.block_id, "视频文件不存在。", f"确认资源已复制到 {block.src}。")
                    )
            if not block.poster:
                issues.append(ReviewIssue("low", block.block_id, "视频块缺少封面图。", "优先绑定关键帧作为 poster。"))
        if not block.evidence_chunk_ids:
            issues.append(
                ReviewIssue("medium", block.block_id, "内容块缺少证据引用。", "为内容块保留至少一个 evidence chunk_id。")
            )
        for chunk_id in block.evidence_chunk_ids:
            if chunk_id not in known_chunk_ids:
                issues.append(
                    ReviewIssue("high", chunk_id, "内容块引用了不存在的证据片段。", "修正证据引用或补齐证据库。")
                )
        return issues


def render_digital_book_review_markdown(title: str, issues: list[ReviewIssue]) -> str:
    lines = [
        f"# {title} 电子教材结构审核",
        "",
        "## 总览",
        "",
        f"- 问题数量：{len(issues)}",
        f"- 高风险：{sum(1 for issue in issues if issue.severity == 'high')}",
        f"- 中风险：{sum(1 for issue in issues if issue.severity == 'medium')}",
        f"- 低风险：{sum(1 for issue in issues if issue.severity == 'low')}",
        "",
        "## 问题清单",
        "",
    ]
    if not issues:
        lines.append("- 暂未发现电子教材结构问题。")
    else:
        for issue in issues:
            lines.append(f"- [{issue.severity}] `{issue.location}` {issue.message}")
            lines.append(f"  建议：{issue.suggestion}")
    return "\n".join(lines).rstrip() + "\n"
