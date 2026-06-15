from __future__ import annotations

from materials2textbook.schemas import ReviewReport


class RevisionAgent:
    """Create a review-aware final draft placeholder."""

    def run(self, draft_markdown: str, reports: list[ReviewReport]) -> str:
        lines = [draft_markdown.rstrip(), "", "## 审核后修订提示", ""]
        for report in reports:
            issue_count = len(report.fact_issues) + len(report.pedagogy_issues)
            lines.append(f"### {report.chapter_title}")
            if issue_count == 0:
                lines.append("- 暂未发现结构化审核问题。")
            else:
                for suggestion in report.revision_suggestions:
                    lines.append(f"- {suggestion}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
