from __future__ import annotations


def build_digital_book_polisher_messages(
    *,
    title: str,
    fallback_markdown: str,
    max_chars: int = 2400,
) -> list[dict[str, str]]:
    """Build a compact prompt for polishing one student-facing knowledge point."""

    clipped = fallback_markdown[:max_chars].strip()
    system = (
        "你是面向中职/高职学生的数字教材正文润色 Agent。"
        "你只能改写用户提供的已清洗内容，不得新增没有依据的事实、参数或步骤。"
        "输出必须是学生端可直接阅读的 Markdown。"
        "禁止输出证据编号、chunk_id、文件名、来源、时间码、review_status、待人工复核、agent 等内部信息。"
    )
    user = "\n".join(
        [
            f"请将以下“{title}”知识点内容润色为正式数字教材正文：",
            "",
            "写作要求：",
            "1. 保留已有的“概念说明 / 操作步骤 / 注意事项 / 常见问题”结构；没有内容的栏目不要硬补。",
            "2. 每条内容改成通顺、完整、适合学生阅读的句子。",
            "3. 删除 PPT 页标题、图注、目录式短语和重复表达。",
            "4. 不要出现素材管理、证据追踪、审核流程或文件路径信息。",
            "5. 输出 Markdown 正文，不要解释你的改写过程。",
            "",
            "待润色内容：",
            clipped,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
