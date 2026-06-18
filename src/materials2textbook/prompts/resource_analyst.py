from __future__ import annotations

from textwrap import shorten

from materials2textbook.schemas import EvidenceChunk


def build_resource_analyst_messages(
    chunk: EvidenceChunk,
    max_content_chars: int = 3000,
) -> list[dict[str, str]]:
    """Build a constrained prompt for one evidence-chunk normalization pass."""

    source = chunk.metadata.get("source_video", "") or chunk.locator.original_path or chunk.locator.path
    start = chunk.metadata.get("start_time", "")
    end = chunk.metadata.get("end_time", "")
    content = shorten(" ".join(chunk.content.split()), width=max_content_chars, placeholder="...")

    system = (
        "你是资料分析 Agent，负责把教学素材片段整理成可追溯的证据片段。"
        "必须严格依据给定片段内容处理，不得新增片段之外的事实、参数、结论或章节。"
        "如果 ASR 明显错误，只能做保守纠错；无法确认时保留原意并标注 uncertain。"
        "只输出 JSON 对象，不要输出 Markdown。"
    )
    user = "\n".join(
        [
            "请规范化下面的证据片段。",
            "",
            "输出 JSON 字段：",
            '- "summary": 一句话素材摘要，不能为空。',
            '- "keywords": 3 到 8 个关键词数组。',
            '- "normalized_content": 保守清理后的证据文本；不得扩写素材外事实。',
            '- "quality_notes": 对 ASR、时间码、证据可用性的简短说明。',
            '- "uncertain": true 或 false，表示该片段是否需要人工复核。',
            "",
            "片段信息：",
            f"- chunk_id: {chunk.chunk_id}",
            f"- title: {chunk.title}",
            f"- source: {source} [{start}-{end}]",
            f"- review_status: {chunk.review_status}",
            f"- transcript_status: {chunk.metadata.get('transcript_status', '')}",
            f"- original_summary: {chunk.summary}",
            f"- original_keywords: {', '.join(chunk.keywords)}",
            "",
            "证据文本：",
            content,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
