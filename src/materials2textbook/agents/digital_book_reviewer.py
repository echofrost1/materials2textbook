from __future__ import annotations

from pathlib import Path
import re

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
            issues.extend(self._review_project_student_text(project.project_id, project.project_intro, project.ability_map + project.learning_goals))
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
                issues.extend(self._review_task_video_duplicates(task.task_id, task.blocks))
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
        if block.type in {"scenario", "implementation", "case_example"}:
            issues.extend(self._review_student_markdown(block))
        if block.type in {"learning_nav", "assessment", "exercises"} and not block.items:
            issues.append(ReviewIssue("medium", block.block_id, f"{block.title} 缺少条目。", "补充列表条目。"))
        if block.items:
            issues.extend(self._review_student_items(block))
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

    def _review_project_student_text(self, project_id: str, intro: str, items: list[str]) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        combined = "\n".join([intro, *items])
        forbidden = _student_forbidden_terms(combined)
        if forbidden:
            issues.append(
                ReviewIssue(
                    "high",
                    project_id,
                    "项目导学信息包含内部证据或素材处理痕迹。",
                    f"改写项目简介、学习目标或能力图谱，移除：{', '.join(forbidden[:5])}。",
                )
            )
        return issues

    def _review_task_video_duplicates(self, task_id: str, blocks: list[DigitalBookBlock]) -> list[ReviewIssue]:
        video_sources = [block.src for block in blocks if block.type == "video" and block.src]
        duplicate_sources = sorted({src for src in video_sources if video_sources.count(src) > 1})
        if not duplicate_sources:
            return []
        return [
            ReviewIssue(
                "medium",
                task_id,
                "同一任务中存在重复视频资源。",
                f"按知识点去重或减少重复展示：{', '.join(duplicate_sources[:3])}。",
            )
        ]

    def _review_student_markdown(self, block: DigitalBookBlock) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        markdown = block.markdown.strip()
        if not markdown:
            return issues
        forbidden = _student_forbidden_terms(markdown)
        if forbidden:
            issues.append(
                ReviewIssue(
                    "high",
                    block.block_id,
                    "学生端正文包含内部证据或素材处理痕迹。",
                    f"从学生正文移除这些内容，仅保留教师侧 metadata 追溯：{', '.join(forbidden[:5])}。",
                )
            )
        if block.type == "implementation":
            plain = re.sub(r"[#>*_\-\d.、：:\s]", "", markdown)
            if len(plain) < 24:
                issues.append(
                    ReviewIssue(
                        "medium",
                        block.block_id,
                        "知识点正文过短，难以支撑学生自学。",
                        "补充概念说明、操作要点、注意事项或配套视频观察提示。",
                    )
                )
            if "围绕“" in markdown and "观察示范视频" in markdown and len(plain) < 60:
                issues.append(
                    ReviewIssue(
                        "low",
                        block.block_id,
                        "知识点正文主要是泛化占位提示。",
                        "优先从文档片段或 LLM 润色结果补入本知识点的具体内容。",
                    )
                )
        return issues

    def _review_student_items(self, block: DigitalBookBlock) -> list[ReviewIssue]:
        combined = "\n".join(block.items)
        forbidden = _student_forbidden_terms(combined)
        if not forbidden:
            return []
        return [
            ReviewIssue(
                "high",
                block.block_id,
                "学生端列表条目包含内部证据或素材处理痕迹。",
                f"改写列表条目，移除：{', '.join(forbidden[:5])}。",
            )
        ]


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


def _student_forbidden_terms(text: str) -> list[str]:
    terms = [
        "chunk_id",
        "证据：",
        "来源：",
        "review_status",
        "Pending_",
        "待人工",
        "人工复核",
        "时间码",
        "kp_",
        "agent",
        "PPT_",
        "C000",
        "证据编号",
        "证据定位",
        "素材处理",
        "处理的教学素材",
    ]
    hits = [term for term in terms if term.lower() in text.lower()]
    if re.search(r"\.(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)\b", text, flags=re.IGNORECASE):
        hits.append("素材文件名")
    if re.search(r"`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?", text):
        hits.append("内部编号")
    if re.search(r"(?:难度|层级)\s*[：:]\s*(?:basic|practice|advanced)\b", text, flags=re.IGNORECASE):
        hits.append("内部难度枚举")
    if re.search(r"\b(?:basic|practice|advanced)/(?:observation|explanation|analysis)\b", text, flags=re.IGNORECASE):
        hits.append("内部活动枚举")
    return hits
