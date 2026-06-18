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
            "1. 改写成 2-4 个正式教材自然段，不要保留“概念说明 / 操作步骤 / 注意事项 / 常见问题”栏目。",
            "2. 禁止大面积项目符号列表；只有素材明确是连续操作流程时，才可使用简短编号或“第一、第二、第三”的句式。",
            "3. 保留事实边界，不新增未提供的参数、标准、结论或操作步骤。",
            "4. 删除 PPT 页标题、图注、目录式短语和重复表达。",
            "5. 不要出现素材管理、证据追踪、审核流程或文件路径信息。",
            "6. 输出 Markdown 正文，不要解释你的改写过程。",
            "",
            "待润色内容：",
            clipped,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
