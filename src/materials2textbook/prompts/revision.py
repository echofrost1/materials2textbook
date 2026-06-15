from __future__ import annotations

from textwrap import shorten

from materials2textbook.schemas import ReviewReport


def build_revision_messages(
    draft_markdown: str,
    reports: list[ReviewReport],
    max_draft_chars: int = 14000,
) -> list[dict[str, str]]:
    issue_blocks: list[str] = []
    for report in reports:
        issues = report.fact_issues + report.pedagogy_issues
        if not issues:
            issue_blocks.append(f"章节：{report.chapter_title}\n- 暂无结构化审核问题。")
            continue
        lines = [f"章节：{report.chapter_title}"]
        for issue in issues:
            lines.append(
                "\n".join(
                    [
                        f"- severity: {issue.severity}",
                        f"  location: {issue.location}",
                        f"  issue: {issue.message}",
                        f"  suggestion: {issue.suggestion}",
                    ]
                )
            )
        issue_blocks.append("\n".join(lines))

    system = (
        "你是教材修订 Agent，负责根据审核报告修订面向中职/高职学生的教材草稿。"
        "必须遵守证据约束：不得新增素材中没有的事实、章节或知识点。"
        "必须保留原文中的证据编号，例如 `C000001` 或 `证据：C000001`。"
        "如果审核指出片段待人工复核，只能标注待复核，不能把它写成已确认事实。"
        "输出完整 Markdown 最终稿。"
    )
    user = "\n".join(
        [
            "请根据审核报告修订下面的教材草稿。",
            "",
            "修订要求：",
            "1. 保留原有 Markdown 层级结构。",
            "2. 面向中职/高职学生，表达清楚、简洁、步骤化。",
            "3. 对待复核片段添加明确提示。",
            "4. 不新增素材外的章节、事实或操作参数。",
            "5. 不要只列审核意见，要输出修订后的完整教材。",
            "",
            "审核报告：",
            "\n\n".join(issue_blocks),
            "",
            "教材草稿：",
            shorten(draft_markdown, width=max_draft_chars, placeholder="\n\n...草稿过长已截断...\n\n"),
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
