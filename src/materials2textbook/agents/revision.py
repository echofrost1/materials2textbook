from __future__ import annotations

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
