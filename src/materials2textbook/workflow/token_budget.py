from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from math import ceil

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.schemas import EvidenceChunk


@dataclass(frozen=True)
class TokenBudgetReport:
    enabled: bool
    max_input_tokens: int
    max_tokens_per_evidence_chunk: int
    original_chunks: int
    kept_chunks: int
    kept_source_chunks: int
    dropped_chunks: int
    original_estimated_tokens: int
    kept_estimated_tokens: int
    truncated_chunks: int
    summary_chunks: int = 0
    summarized_source_chunks: int = 0
    uncovered_dropped_chunks: int = 0


def apply_evidence_token_budget(
    chunks: list[EvidenceChunk],
    *,
    max_input_tokens: int = 0,
    max_tokens_per_evidence_chunk: int = 1200,
    summarize_over_budget: bool = False,
    summary_token_reserve_ratio: float = 0.3,
    max_tokens_per_summary_chunk: int = 500,
    max_summary_source_chunks: int = 8,
    llm_provider: LLMProvider | None = None,
    use_llm: bool = False,
    chars_per_token: float = 1.8,
) -> tuple[list[EvidenceChunk], TokenBudgetReport]:
    original_tokens = estimate_chunks_tokens(chunks, chars_per_token=chars_per_token)
    if max_input_tokens <= 0:
        return chunks, TokenBudgetReport(
            enabled=False,
            max_input_tokens=0,
            max_tokens_per_evidence_chunk=max_tokens_per_evidence_chunk,
            original_chunks=len(chunks),
            kept_chunks=len(chunks),
            kept_source_chunks=len(chunks),
            dropped_chunks=0,
            original_estimated_tokens=original_tokens,
            kept_estimated_tokens=original_tokens,
            truncated_chunks=0,
        )

    chunk_limit = max(1, max_tokens_per_evidence_chunk)
    summary_chunk_limit = max(1, max_tokens_per_summary_chunk)
    summary_source_limit = max(1, max_summary_source_chunks)
    trimmed_chunks = []
    truncated_count = 0
    for chunk in chunks:
        trimmed = trim_chunk_to_token_limit(chunk, chunk_limit, chars_per_token=chars_per_token)
        if trimmed.content != chunk.content:
            truncated_count += 1
        trimmed_chunks.append(trimmed)

    original_budget = max_input_tokens
    if summarize_over_budget:
        reserve_ratio = min(0.8, max(0.0, summary_token_reserve_ratio))
        original_budget = max(1, int(max_input_tokens * (1.0 - reserve_ratio)))

    selected = _select_diverse_chunks(
        trimmed_chunks,
        max_input_tokens=original_budget,
        chars_per_token=chars_per_token,
    )
    selected_object_ids = {id(chunk) for chunk in selected}
    dropped = [chunk for chunk in trimmed_chunks if id(chunk) not in selected_object_ids]
    summary_chunks: list[EvidenceChunk] = []
    summarized_source_chunks = 0
    if summarize_over_budget and dropped:
        remaining_budget = max_input_tokens - estimate_chunks_tokens(selected, chars_per_token=chars_per_token)
        summary_chunks = _build_summary_chunks(
            dropped,
            max_input_tokens=remaining_budget,
            max_tokens_per_summary_chunk=summary_chunk_limit,
            max_summary_source_chunks=summary_source_limit,
            llm_provider=llm_provider,
            use_llm=use_llm,
            chars_per_token=chars_per_token,
        )
        summarized_source_chunks = sum(int(chunk.metadata.get("source_count", 0)) for chunk in summary_chunks)
    selected.extend(summary_chunks)
    kept_tokens = estimate_chunks_tokens(selected, chars_per_token=chars_per_token)
    dropped_source_chunks = len(dropped)
    return selected, TokenBudgetReport(
        enabled=True,
        max_input_tokens=max_input_tokens,
        max_tokens_per_evidence_chunk=chunk_limit,
        original_chunks=len(chunks),
        kept_chunks=len(selected),
        kept_source_chunks=len(selected_object_ids),
        dropped_chunks=dropped_source_chunks,
        original_estimated_tokens=original_tokens,
        kept_estimated_tokens=kept_tokens,
        truncated_chunks=truncated_count,
        summary_chunks=len(summary_chunks),
        summarized_source_chunks=summarized_source_chunks,
        uncovered_dropped_chunks=max(0, dropped_source_chunks - summarized_source_chunks),
    )


def estimate_chunks_tokens(chunks: list[EvidenceChunk], *, chars_per_token: float = 1.8) -> int:
    return sum(estimate_chunk_tokens(chunk, chars_per_token=chars_per_token) for chunk in chunks)


def estimate_chunk_tokens(chunk: EvidenceChunk, *, chars_per_token: float = 1.8) -> int:
    text = " ".join(
        [
            chunk.chunk_id,
            chunk.asset_id,
            chunk.title,
            chunk.summary,
            chunk.content,
            " ".join(chunk.keywords),
            chunk.subject,
            chunk.material_block,
            chunk.recommended_chapter,
            chunk.review_status,
        ]
    )
    return max(1, ceil(len(text) / max(chars_per_token, 0.1)) + 24)


def trim_chunk_to_token_limit(
    chunk: EvidenceChunk,
    max_tokens: int,
    *,
    chars_per_token: float = 1.8,
) -> EvidenceChunk:
    current_tokens = estimate_chunk_tokens(chunk, chars_per_token=chars_per_token)
    if current_tokens <= max_tokens:
        return chunk

    non_content_tokens = estimate_chunk_tokens(replace(chunk, content=""), chars_per_token=chars_per_token)
    available_content_tokens = max(16, max_tokens - non_content_tokens)
    max_chars = max(32, int(available_content_tokens * chars_per_token))
    content = " ".join(chunk.content.split())
    if len(content) <= max_chars:
        return chunk
    metadata = dict(chunk.metadata)
    metadata["token_budget_truncated"] = True
    metadata["token_budget_original_chars"] = len(chunk.content)
    return replace(chunk, content=content[: max_chars - 3].rstrip() + "...", metadata=metadata)


def _select_diverse_chunks(
    chunks: list[EvidenceChunk],
    *,
    max_input_tokens: int,
    chars_per_token: float,
) -> list[EvidenceChunk]:
    grouped: dict[tuple[str, str], list[EvidenceChunk]] = defaultdict(list)
    for chunk in chunks:
        grouped[(chunk.recommended_chapter or "unknown", chunk.title or "unknown")].append(chunk)

    queues: deque[deque[EvidenceChunk]] = deque()
    for _group, group_chunks in sorted(grouped.items(), key=lambda item: item[0]):
        ordered = sorted(group_chunks, key=_chunk_priority, reverse=True)
        queues.append(deque(ordered))

    selected: list[EvidenceChunk] = []
    used_tokens = 0
    while queues:
        queue = queues.popleft()
        chunk = queue.popleft()
        chunk_tokens = estimate_chunk_tokens(chunk, chars_per_token=chars_per_token)
        if used_tokens + chunk_tokens <= max_input_tokens:
            selected.append(chunk)
            used_tokens += chunk_tokens
        if queue:
            queues.append(queue)
    return selected


def _build_summary_chunks(
    chunks: list[EvidenceChunk],
    *,
    max_input_tokens: int,
    max_tokens_per_summary_chunk: int,
    max_summary_source_chunks: int,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    chars_per_token: float,
) -> list[EvidenceChunk]:
    if max_input_tokens <= 0:
        return []

    summaries: list[EvidenceChunk] = []
    used_tokens = 0
    summary_index = 1
    for (chapter, point), group_chunks in _group_chunks_for_summary(chunks).items():
        ordered = sorted(group_chunks, key=_chunk_priority, reverse=True)
        for batch_start in range(0, len(ordered), max_summary_source_chunks):
            batch = ordered[batch_start : batch_start + max_summary_source_chunks]
            summary = _summarize_batch(
                batch,
                chapter=chapter,
                point=point,
                summary_index=summary_index,
                max_tokens=max_tokens_per_summary_chunk,
                llm_provider=llm_provider,
                use_llm=use_llm,
                chars_per_token=chars_per_token,
            )
            summary_tokens = estimate_chunk_tokens(summary, chars_per_token=chars_per_token)
            if used_tokens + summary_tokens > max_input_tokens:
                return summaries
            summaries.append(summary)
            used_tokens += summary_tokens
            summary_index += 1
    return summaries


def _group_chunks_for_summary(chunks: list[EvidenceChunk]) -> dict[tuple[str, str], list[EvidenceChunk]]:
    grouped: dict[tuple[str, str], list[EvidenceChunk]] = defaultdict(list)
    for chunk in chunks:
        grouped[(chunk.recommended_chapter or "待规划章节", chunk.title or "未命名知识点")].append(chunk)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _summarize_batch(
    chunks: list[EvidenceChunk],
    *,
    chapter: str,
    point: str,
    summary_index: int,
    max_tokens: int,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    chars_per_token: float,
) -> EvidenceChunk:
    source_ids = [chunk.chunk_id for chunk in chunks]
    content = ""
    if use_llm and llm_provider is not None:
        content = _llm_summary(
            chunks,
            chapter=chapter,
            point=point,
            max_chars=max(300, int(max_tokens * chars_per_token)),
            llm_provider=llm_provider,
        )
    if not content:
        content = _extractive_summary(chunks, max_chars=max(300, int(max_tokens * chars_per_token)))

    prototype = chunks[0]
    summary_chunk = replace(
        prototype,
        chunk_id=f"SUM_{summary_index:04d}",
        asset_id=f"summary_{prototype.asset_id}",
        title=point,
        content=content,
        summary=f"{point} 聚合摘要，覆盖 {len(chunks)} 条超预算证据。",
        keywords=_dedupe([point, *[keyword for chunk in chunks for keyword in chunk.keywords]])[:8],
        recommended_chapter=chapter,
        source_type="summary_evidence",
        review_status=_summary_review_status(chunks),
        metadata={
            "summary_evidence": True,
            "source_chunk_ids": source_ids,
            "source_count": len(chunks),
            "summary_method": "llm" if use_llm and llm_provider is not None else "extractive",
            "source_review_statuses": sorted({chunk.review_status for chunk in chunks if chunk.review_status}),
        },
    )
    return trim_chunk_to_token_limit(summary_chunk, max_tokens, chars_per_token=chars_per_token)


def _llm_summary(
    chunks: list[EvidenceChunk],
    *,
    chapter: str,
    point: str,
    max_chars: int,
    llm_provider: LLMProvider,
) -> str:
    evidence_lines = []
    for chunk in chunks:
        text = _shorten_text(chunk.content, max_chars=500)
        evidence_lines.append(f"- {chunk.chunk_id} [{chunk.review_status}] {text}")
    messages = [
        {
            "role": "system",
            "content": (
                "你是教材证据压缩 Agent。只能依据给定证据写摘要，不得补充外部事实。"
                "输出 3 到 6 条要点，每条保留来源 chunk_id。"
            ),
        },
        {
            "role": "user",
            "content": "\n".join(
                [
                    f"章节：{chapter}",
                    f"知识点：{point}",
                    "请把这些超预算证据压缩成可追溯摘要：",
                    *evidence_lines,
                ]
            ),
        },
    ]
    try:
        return _shorten_text(llm_provider.generate(messages), max_chars=max_chars)
    except Exception:
        return ""


def _extractive_summary(chunks: list[EvidenceChunk], *, max_chars: int) -> str:
    lines = []
    for chunk in chunks:
        snippet = _shorten_text(chunk.content, max_chars=180)
        lines.append(f"- 来源 `{chunk.chunk_id}`：{snippet}")
    return _shorten_text("\n".join(lines), max_chars=max_chars)


def _shorten_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return "." * max(0, max_chars)
    return normalized[: max_chars - 3].rstrip() + "..."


def _summary_review_status(chunks: list[EvidenceChunk]) -> str:
    statuses = {chunk.review_status.lower() for chunk in chunks}
    if statuses and all("approved" in status for status in statuses):
        return "Summary_Approved_Evidence"
    return "Summary_Needs_Source_Review"


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result


def _chunk_priority(chunk: EvidenceChunk) -> tuple[float, float, int]:
    approved_bonus = 1.0 if "approved" in chunk.review_status.lower() else 0.0
    score = chunk.score.teaching_value * 0.6 + chunk.score.relevance * 0.3 + chunk.score.confidence * 0.1
    return approved_bonus, score, len(chunk.content)
