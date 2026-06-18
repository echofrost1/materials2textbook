from materials2textbook.agents.revision import render_revision_diff_markdown
from materials2textbook.prompts.revision import build_revision_messages
from materials2textbook.schemas import ReviewIssue, ReviewReport


def test_revision_prompt_preserves_evidence_and_forbids_new_facts() -> None:
    reports = [
        ReviewReport(
            chapter_id="chapter_01",
            chapter_title="基本操作",
            fact_issues=[
                ReviewIssue(
                    severity="medium",
                    location="C1",
                    message="片段仍处于待人工复核状态。",
                    suggestion="标注待复核，不要写成确定事实。",
                )
            ],
        )
    ]
    messages = build_revision_messages("# 样章\n\n证据：C1", reports)
    combined = "\n".join(message["content"] for message in messages)
    assert "不得新增素材中没有的事实" in combined
    assert "证据：C1" in combined
    assert "待人工复核" in combined


def test_render_revision_diff_markdown_contains_checklist_and_diff() -> None:
    reports = [
        ReviewReport(
            chapter_id="chapter_01",
            chapter_title="基本操作",
            fact_issues=[
                ReviewIssue(
                    severity="medium",
                    location="C1",
                    message="证据片段仍待复核。",
                    suggestion="确认时间码后再发布。",
                )
            ],
        )
    ]

    markdown = render_revision_diff_markdown(
        title="样章",
        draft_markdown="# 样章\n\n原文",
        final_markdown="# 样章\n\n修订后正文",
        reports=reports,
    )

    assert "# 样章 修订差异与人工确认清单" in markdown
    assert "- [ ] [medium] 基本操作 / C1：确认时间码后再发布。" in markdown
    assert "```diff" in markdown
    assert "-原文" in markdown
    assert "+修订后正文" in markdown
