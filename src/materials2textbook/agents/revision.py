from __future__ import annotations

import difflib

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.revision import build_revision_messages
from materials2textbook.schemas import ReviewReport


class RevisionAgent:
    """Create a review-aware final draft placeholder."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm

    def run(self, draft_markdown: str, reports: list[ReviewReport]) -> str:
        if self.use_llm:
            if self.llm_provider is None:
                raise RuntimeError("RevisionAgent was asked to use LLM, but no provider was configured.")
            messages = build_revision_messages(draft_markdown, reports)
            return self.llm_provider.generate(messages).rstrip() + "\n"

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


def render_revision_diff_markdown(
    *,
    title: str,
    draft_markdown: str,
    final_markdown: str,
    reports: list[ReviewReport],
) -> str:
    """Render a teacher-facing diff and confirmation checklist."""

    lines = [
        f"# {title} 修订差异与人工确认清单",
        "",
        "## 待确认项",
        "",
    ]
    checklist = _confirmation_items(reports)
    if checklist:
        for item in checklist:
            lines.append(f"- [ ] {item}")
    else:
        lines.append("- [ ] 暂未发现结构化审核问题，人工抽查证据引用和视频可播放性。")

    diff = list(
        difflib.unified_diff(
            draft_markdown.splitlines(),
            final_markdown.splitlines(),
            fromfile="textbook_draft.md",
            tofile="textbook_final.md",
            lineterm="",
        )
    )
    lines.extend(["", "## Markdown 差异", "", "```diff"])
    if diff:
        lines.extend(diff)
    else:
        lines.append("# 草稿与最终稿暂无文本差异。")
    lines.extend(["```", ""])
    return "\n".join(lines).rstrip() + "\n"


def _confirmation_items(reports: list[ReviewReport]) -> list[str]:
    items: list[str] = []
    for report in reports:
        issues = report.fact_issues + report.pedagogy_issues
        for issue in issues:
            items.append(f"[{issue.severity}] {report.chapter_title} / {issue.location}：{issue.suggestion}")
    return items
