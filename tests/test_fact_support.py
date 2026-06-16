from materials2textbook.agents.fact_support import (
    analyze_claim_support,
    analyze_paragraph_support,
    claim_support_rate,
    detect_claim_consistency_issues,
    paragraph_support_rate,
)
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def make_chunk(chunk_id: str, status: str = "approved") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="A1",
        title="送丝",
        content="送丝证据",
        summary="送丝摘要",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        review_status=status,
    )


def test_analyze_paragraph_support_scores_supported_unknown_and_pending() -> None:
    paragraphs = analyze_paragraph_support(
        """
# 标题

送丝操作需要保持稳定，证据：C1。

这个段落没有任何证据引用，但包含事实判断。

引用不存在的证据：C999。

待复核片段说明了收弧动作，证据：C2。
""",
        [make_chunk("C1"), make_chunk("C2", "Pending_Manual_Timecode")],
    )

    assert [item.support_status for item in paragraphs] == [
        "supported",
        "unsupported",
        "unknown_citation",
        "pending_evidence",
    ]
    assert paragraph_support_rate(paragraphs) == 0.4


def test_analyze_paragraph_support_skips_headings_and_short_labels() -> None:
    paragraphs = analyze_paragraph_support(
        """
## 学习目标

- 证据：C1
""",
        [make_chunk("C1")],
    )

    assert paragraphs == []


def test_analyze_claim_support_scores_sentence_level_claims() -> None:
    claims = analyze_claim_support(
        """
送丝操作需要保持稳定，证据：C1。收弧操作需要人工复核，证据：C2。
这个断言没有证据但包含事实判断。
""",
        [make_chunk("C1"), make_chunk("C2", "Pending_Manual_Timecode")],
    )

    assert [claim.support_status for claim in claims] == ["supported", "pending_evidence", "unsupported"]
    assert claim_support_rate(claims) == 0.5333


def test_analyze_claim_support_inherits_block_citation() -> None:
    claims = analyze_claim_support(
        "送丝操作需要保持稳定。观察时要结合熔池状态。证据：C1。",
        [make_chunk("C1")],
    )

    assert [claim.support_status for claim in claims] == ["supported", "supported"]
    assert all(claim.cited_chunk_ids == ["C1"] for claim in claims)


def test_detect_claim_consistency_issues_for_same_evidence_topic() -> None:
    markdown = """
送丝操作需要保持稳定，证据：C1。
送丝操作不能保持稳定，证据：C1。
"""

    issues = detect_claim_consistency_issues(markdown, [make_chunk("C1")])

    assert len(issues) == 1
    assert issues[0].topic == "送丝"
    assert issues[0].cited_chunk_ids == ["C1"]
