from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore
from materials2textbook.workflow.token_budget import (
    apply_evidence_token_budget,
    estimate_chunk_tokens,
    trim_chunk_to_token_limit,
)


def make_chunk(
    chunk_id: str,
    title: str,
    content: str,
    *,
    teaching_value: float = 0.5,
    relevance: float = 0.5,
    confidence: float = 0.5,
    review_status: str = "Pending",
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=f"A{chunk_id}",
        title=title,
        content=content,
        summary=f"{title} summary",
        keywords=[title],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(
            relevance=relevance,
            teaching_value=teaching_value,
            confidence=confidence,
        ),
        review_status=review_status,
    )


def test_disabled_token_budget_keeps_chunks_unchanged() -> None:
    chunks = [make_chunk("C1", "送丝", "短证据")]

    kept, report = apply_evidence_token_budget(chunks, max_input_tokens=0)

    assert kept == chunks
    assert not report.enabled
    assert report.dropped_chunks == 0


def test_trim_chunk_to_token_limit_shortens_large_content() -> None:
    chunk = make_chunk("C1", "送丝", "证据" * 300)

    trimmed = trim_chunk_to_token_limit(chunk, 80)

    assert len(trimmed.content) < len(chunk.content)
    assert trimmed.content.endswith("...")
    assert trimmed.metadata["token_budget_truncated"] is True
    assert estimate_chunk_tokens(trimmed) <= 120


def test_token_budget_keeps_diverse_high_value_chunks() -> None:
    chunks = [
        make_chunk("C1", "送丝", "高价值证据 " * 30, teaching_value=0.95, review_status="approved"),
        make_chunk("C2", "送丝", "低价值证据 " * 30, teaching_value=0.1),
        make_chunk("C3", "收弧", "另一个知识点证据 " * 30, teaching_value=0.9, review_status="approved"),
        make_chunk("C4", "收弧", "低价值证据 " * 30, teaching_value=0.1),
    ]

    kept, report = apply_evidence_token_budget(
        chunks,
        max_input_tokens=260,
        max_tokens_per_evidence_chunk=90,
    )

    kept_ids = {chunk.chunk_id for chunk in kept}
    assert "C1" in kept_ids
    assert "C3" in kept_ids
    assert report.enabled
    assert report.dropped_chunks > 0
    assert report.kept_estimated_tokens <= report.max_input_tokens


def test_token_budget_can_summarize_dropped_chunks() -> None:
    chunks = [
        make_chunk("C1", "送丝", "高价值送丝证据 " * 40, teaching_value=0.95, review_status="approved"),
        make_chunk("C2", "送丝", "低价值送丝证据 " * 40, teaching_value=0.1),
        make_chunk("C3", "送丝", "另一条送丝补充证据 " * 40, teaching_value=0.1),
        make_chunk("C4", "收弧", "收弧补充证据 " * 40, teaching_value=0.1),
    ]

    kept, report = apply_evidence_token_budget(
        chunks,
        max_input_tokens=420,
        max_tokens_per_evidence_chunk=90,
        summarize_over_budget=True,
        summary_token_reserve_ratio=0.5,
        max_tokens_per_summary_chunk=120,
        max_summary_source_chunks=3,
    )

    summaries = [chunk for chunk in kept if chunk.source_type == "summary_evidence"]
    assert summaries
    assert report.summary_chunks == len(summaries)
    assert report.summarized_source_chunks >= 1
    assert summaries[0].metadata["summary_evidence"] is True
    assert summaries[0].metadata["source_chunk_ids"]
    assert "来源" in summaries[0].content


def test_token_budget_handles_duplicate_chunk_ids_and_zero_summary_batch_size() -> None:
    chunks = [
        make_chunk("C1", "送丝", "高价值证据 " * 30, teaching_value=0.95, review_status="approved"),
        make_chunk("C1", "送丝", "低价值证据 " * 30, teaching_value=0.1),
        make_chunk("C2", "收弧", "收弧证据 " * 30, teaching_value=0.1),
    ]

    kept, report = apply_evidence_token_budget(
        chunks,
        max_input_tokens=320,
        max_tokens_per_evidence_chunk=90,
        summarize_over_budget=True,
        summary_token_reserve_ratio=0.5,
        max_tokens_per_summary_chunk=120,
        max_summary_source_chunks=0,
    )

    summaries = [chunk for chunk in kept if chunk.source_type == "summary_evidence"]
    assert report.dropped_chunks >= report.summarized_source_chunks
    assert summaries


def test_extractive_summary_preserves_long_chinese_without_spaces() -> None:
    chunks = [
        make_chunk("C1", "送丝", "这是一个没有空格的中文长句用于模拟自动转写文本" * 20, teaching_value=0.1),
        make_chunk("C2", "送丝", "这是另一个没有空格的中文长句用于模拟自动转写文本" * 20, teaching_value=0.1),
    ]

    kept, _report = apply_evidence_token_budget(
        chunks,
        max_input_tokens=260,
        max_tokens_per_evidence_chunk=80,
        summarize_over_budget=True,
        summary_token_reserve_ratio=0.8,
        max_tokens_per_summary_chunk=120,
        max_summary_source_chunks=2,
    )

    summaries = [chunk for chunk in kept if chunk.source_type == "summary_evidence"]
    assert summaries
    assert "这是一个没有空格的中文长句" in summaries[0].content
