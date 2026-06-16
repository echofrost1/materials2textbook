from __future__ import annotations

from collections import Counter
import re

from materials2textbook.agents.fact_support import analyze_claim_support, analyze_paragraph_support, claim_support_rate, paragraph_support_rate
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, ReviewIssue, ReviewReport, WorkflowSummary


def build_workflow_summary(
    *,
    title: str,
    source_records: int,
    evidence_chunks: list[EvidenceChunk],
    skipped_chunks: int,
    plans: list[ChapterPlan],
    reports: list[ReviewReport],
    draft_markdown: str = "",
) -> WorkflowSummary:
    issues = collect_issues(reports)
    severities = Counter(issue.severity for issue in issues)
    quality = calculate_quality_metrics(evidence_chunks, plans, draft_markdown)
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
        evidence_coverage_rate=quality["evidence_coverage_rate"],
        citation_coverage_rate=quality["citation_coverage_rate"],
        paragraph_support_rate=quality["paragraph_support_rate"],
        claim_support_rate=quality["claim_support_rate"],
        approved_evidence_rate=quality["approved_evidence_rate"],
        pedagogy_completeness_rate=quality["pedagogy_completeness_rate"],
        activity_quality_rate=quality["activity_quality_rate"],
        case_quality_rate=quality["case_quality_rate"],
        overall_quality_score=quality["overall_quality_score"],
        review_status_counts=dict(Counter(chunk.review_status or "unknown" for chunk in evidence_chunks)),
        material_block_counts=dict(Counter(chunk.material_block or "unknown" for chunk in evidence_chunks)),
    )


def calculate_quality_metrics(
    evidence_chunks: list[EvidenceChunk],
    plans: list[ChapterPlan],
    draft_markdown: str = "",
) -> dict[str, float]:
    required_point_count = sum(len(plan.knowledge_points) for plan in plans)
    covered_point_count = sum(1 for plan in plans for point in plan.knowledge_points if point.chunk_ids)
    evidence_coverage_rate = _safe_ratio(covered_point_count, required_point_count)

    expected_chunk_ids = {chunk_id for plan in plans for point in plan.knowledge_points for chunk_id in point.chunk_ids}
    cited_chunk_ids = _extract_cited_chunk_ids(draft_markdown, expected_chunk_ids)
    citation_coverage_rate = _safe_ratio(len(cited_chunk_ids), len(expected_chunk_ids))
    paragraph_support = analyze_paragraph_support(draft_markdown, evidence_chunks)
    paragraph_rate = paragraph_support_rate(paragraph_support)
    claim_support = analyze_claim_support(draft_markdown, evidence_chunks)
    claim_rate = claim_support_rate(claim_support)

    approved_count = sum(1 for chunk in evidence_chunks if "approved" in chunk.review_status.lower())
    approved_evidence_rate = _safe_ratio(approved_count, len(evidence_chunks))

    expected_pedagogy_items = len(plans) * 3
    present_pedagogy_items = 0
    for plan in plans:
        if plan.learning_goals:
            present_pedagogy_items += 1
        if len(plan.knowledge_points) >= 2:
            present_pedagogy_items += 1
        if plan.activities:
            present_pedagogy_items += 1
    pedagogy_completeness_rate = _safe_ratio(present_pedagogy_items, expected_pedagogy_items)
    activity_quality_rate = _calculate_activity_quality_rate(plans)
    case_quality_rate = _calculate_case_quality_rate(plans)

    overall_quality_score = round(
        (
            evidence_coverage_rate * 0.25
            + citation_coverage_rate * 0.18
            + paragraph_rate * 0.17
            + approved_evidence_rate * 0.20
            + pedagogy_completeness_rate * 0.10
            + activity_quality_rate * 0.10
        ),
        4,
    )
    return {
        "evidence_coverage_rate": evidence_coverage_rate,
        "citation_coverage_rate": citation_coverage_rate,
        "paragraph_support_rate": paragraph_rate,
        "claim_support_rate": claim_rate,
        "approved_evidence_rate": approved_evidence_rate,
        "pedagogy_completeness_rate": pedagogy_completeness_rate,
        "activity_quality_rate": activity_quality_rate,
        "case_quality_rate": case_quality_rate,
        "overall_quality_score": overall_quality_score,
    }


def collect_issues(reports: list[ReviewReport]) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for report in reports:
        issues.extend(report.fact_issues)
        issues.extend(report.pedagogy_issues)
    return issues


def _calculate_activity_quality_rate(plans: list[ChapterPlan]) -> float:
    if not plans:
        return 0.0
    plan_scores: list[float] = []
    for plan in plans:
        if not plan.activity_items:
            plan_scores.append(0.0)
            continue
        levels = {activity.difficulty_level for activity in plan.activity_items}
        required_levels = {"basic", "practice"}
        if len(plan.knowledge_points) >= 2:
            required_levels.add("advanced")
        level_score = _safe_ratio(len(levels.intersection(required_levels)), len(required_levels))

        known_point_ids = {point.knowledge_point_id for point in plan.knowledge_points}
        covered_points = {
            point_id
            for activity in plan.activity_items
            for point_id in activity.target_knowledge_point_ids
            if point_id in known_point_ids
        }
        coverage_score = _safe_ratio(len(covered_points), len(known_point_ids))

        rubric_score = _safe_ratio(
            sum(1 for activity in plan.activity_items if activity.rubric),
            len(plan.activity_items),
        )
        evidence_score = _safe_ratio(
            sum(1 for activity in plan.activity_items if set(activity.evidence_chunk_ids).intersection(plan.evidence_chunk_ids)),
            len(plan.activity_items),
        )
        plan_scores.append(round(level_score * 0.35 + coverage_score * 0.25 + rubric_score * 0.20 + evidence_score * 0.20, 4))
    return round(sum(plan_scores) / len(plan_scores), 4)


def _calculate_case_quality_rate(plans: list[ChapterPlan]) -> float:
    if not plans:
        return 0.0
    plan_scores: list[float] = []
    for plan in plans:
        if not plan.case_examples:
            needs_case = any(point.difficulty_level in {"practice", "advanced"} for point in plan.knowledge_points)
            plan_scores.append(0.0 if needs_case else 1.0)
            continue

        known_point_ids = {point.knowledge_point_id for point in plan.knowledge_points}
        known_chunk_ids = set(plan.evidence_chunk_ids)
        case_scores: list[float] = []
        for example in plan.case_examples:
            evidence_score = 1.0 if set(example.evidence_chunk_ids).intersection(known_chunk_ids) else 0.0
            target_score = 1.0 if set(example.target_knowledge_point_ids).intersection(known_point_ids) else 0.0
            combined_text = f"{example.prompt} {example.reference_answer}"
            migration_score = 1.0 if _contains_any(combined_text, ("迁移", "同类", "现场", "项目", "判断", "应用")) else 0.0
            learner_score = 1.0 if _contains_any(combined_text, ("学生", "新手", "学员", "课堂", "实训", "岗位")) else 0.0
            case_scores.append(round(evidence_score * 0.35 + target_score * 0.25 + migration_score * 0.20 + learner_score * 0.20, 4))
        plan_scores.append(round(sum(case_scores) / len(case_scores), 4))
    return round(sum(plan_scores) / len(plan_scores), 4)


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
        f"- 证据覆盖率：{_format_percent(summary.evidence_coverage_rate)}",
        f"- 引用覆盖率：{_format_percent(summary.citation_coverage_rate)}",
        f"- 段落事实支撑率：{_format_percent(summary.paragraph_support_rate)}",
        f"- 断言事实支撑率：{_format_percent(summary.claim_support_rate)}",
        f"- 可正式使用证据率：{_format_percent(summary.approved_evidence_rate)}",
        f"- 教学结构完整度：{_format_percent(summary.pedagogy_completeness_rate)}",
        f"- 活动质量评分：{_format_percent(summary.activity_quality_rate)}",
        f"- 案例质量评分：{_format_percent(summary.case_quality_rate)}",
        f"- 综合质量评分：{summary.overall_quality_score:.4f}",
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


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _extract_cited_chunk_ids(markdown: str, expected_chunk_ids: set[str]) -> set[str]:
    if not markdown:
        return set()
    direct_matches = {chunk_id for chunk_id in expected_chunk_ids if chunk_id and chunk_id in markdown}
    pattern_matches = set(re.findall(r"`?([A-Za-z]\d{1,}|[A-Za-z][A-Za-z0-9_-]{1,})`?", markdown))
    return {chunk_id for chunk_id in expected_chunk_ids if chunk_id in direct_matches or chunk_id in pattern_matches}


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


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
