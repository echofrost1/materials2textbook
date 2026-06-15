from __future__ import annotations

from collections import Counter

from materials2textbook.schemas import ChapterPlan, EvidenceChunk, ReviewIssue, ReviewReport, WorkflowSummary


def build_workflow_summary(
    *,
    title: str,
    source_records: int,
    evidence_chunks: list[EvidenceChunk],
    skipped_chunks: int,
    plans: list[ChapterPlan],
    reports: list[ReviewReport],
) -> WorkflowSummary:
    issues = collect_issues(reports)
    severities = Counter(issue.severity for issue in issues)
    return WorkflowSummary(
        title=title,
        source_records=source_records,
        evidence_chunks=len(evidence_chunks),
        skipped_chunks=skipped_chunks,
        chapters=len(plans),
        knowledge_points=sum(len(plan.knowledge_points) for plan in plans),
        fact_issue_count=sum(len(report.fact_issues) for report in reports),
        pedagogy_issue_count=sum(len(report.pedagogy_issues) for report in reports),
        high_issue_count=severities.get("high", 0),
        medium_issue_count=severities.get("medium", 0),
        low_issue_count=severities.get("low", 0),
        review_status_counts=dict(Counter(chunk.review_status or "unknown" for chunk in evidence_chunks)),
        material_block_counts=dict(Counter(chunk.material_block or "unknown" for chunk in evidence_chunks)),
    )


def collect_issues(reports: list[ReviewReport]) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for report in reports:
        issues.extend(report.fact_issues)
        issues.extend(report.pedagogy_issues)
    return issues


def render_review_markdown(reports: list[ReviewReport], summary: WorkflowSummary) -> str:
    lines = [
        f"# {summary.title} 审核报告",
        "",
        "## 总览",
        "",
        f"- 来源记录数：{summary.source_records}",
        f"- 进入编排证据片段：{summary.evidence_chunks}",
        f"- 被过滤片段：{summary.skipped_chunks}",
        f"- 章节数：{summary.chapters}",
        f"- 知识点数：{summary.knowledge_points}",
        f"- 事实/证据问题：{summary.fact_issue_count}",
        f"- 教学结构问题：{summary.pedagogy_issue_count}",
        f"- 高/中/低风险问题：{summary.high_issue_count}/{summary.medium_issue_count}/{summary.low_issue_count}",
        "",
        "## 片段状态分布",
        "",
    ]

    for status, count in sorted(summary.review_status_counts.items()):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "## 素材大块分布", ""])
    for block, count in sorted(summary.material_block_counts.items()):
        lines.append(f"- {block}: {count}")

    lines.extend(["", "## 章节问题", ""])
    for report in reports:
        lines.extend([f"### {report.chapter_title}", ""])
        issues = report.fact_issues + report.pedagogy_issues
        if not issues:
            lines.extend(["- 暂未发现结构化审核问题。", ""])
            continue
        for issue in issues:
            lines.append(f"- [{issue.severity}] `{issue.location}` {issue.message}")
            lines.append(f"  建议：{issue.suggestion}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_evidence_markdown(chunks: list[EvidenceChunk], title: str) -> str:
    lines = [f"# {title} 证据索引", ""]
    if not chunks:
        lines.append("> 当前没有进入编排的证据片段。")
        return "\n".join(lines).rstrip() + "\n"

    grouped: dict[str, dict[str, list[EvidenceChunk]]] = {}
    for chunk in chunks:
        chapter = chunk.recommended_chapter or "待规划章节"
        point = chunk.title or "未命名知识点"
        grouped.setdefault(chapter, {}).setdefault(point, []).append(chunk)

    for chapter_title, points in grouped.items():
        lines.extend([f"## {chapter_title}", ""])
        for point_title, point_chunks in points.items():
            lines.extend([f"### {point_title}", ""])
            for chunk in point_chunks:
                source = chunk.metadata.get("source_video", "") or chunk.locator.original_path or chunk.locator.path
                start = chunk.metadata.get("start_time", "")
                end = chunk.metadata.get("end_time", "")
                keyframes = ", ".join(chunk.locator.keyframe_paths) if chunk.locator.keyframe_paths else "无"
                lines.extend(
                    [
                        f"#### {chunk.chunk_id}",
                        "",
                        f"- 素材：{source}",
                        f"- 时间码：{start} - {end}",
                        f"- 原始路径：{chunk.locator.original_path or '无'}",
                        f"- 片段状态：{chunk.review_status or 'unknown'}",
                        f"- 教学价值评分：{chunk.score.teaching_value}",
                        f"- 关键帧：{keyframes}",
                        f"- 摘要：{chunk.summary or '无'}",
                        "",
                    ]
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
