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
