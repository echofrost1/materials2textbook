from __future__ import annotations

import json


def build_general_preface_messages(
    *,
    book_title: str,
    project_titles: list[str],
    material_summary: str = "",
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    """Build a prompt for generating the general preface (大总序) of the textbook."""

    payload = {
        "book_title": book_title,
        "project_titles": project_titles,
        "material_summary": material_summary[:max_chars],
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "你是职业教育数字教材的总序撰写专家。"
        "你需要为整本教材撰写一篇总序（大总序），介绍教材的编写背景、适用对象、课程定位与学习建议。"
        "语言面向学生，正式、流畅、有感染力。"
        "禁止输出证据编号、chunk_id、文件名、路径、时间码、review_status、待人工复核、agent 等内部信息。"
        "只输出纯文本 Markdown（可含段落和小标题），不要代码块，不要解释。"
        "控制在 600–1000 字。"
    )
    user = "\n".join(
        [
            "请为以下数字教材撰写总序：",
            "",
            clipped,
            "",
            "撰写要求：",
            "1. 第一段点明教材所属行业领域与时代背景。",
            "2. 第二段说明教材的编写理念与特色（数字化、项目化、理实一体）。",
            "3. 第三段概述教材涵盖的项目内容与学习路径。",
            "4. 第四段给出学习建议与期望。",
            "5. 语气正式、积极向上，适合中职/高职学生阅读。",
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_preface_messages(
    *,
    book_title: str,
    project_titles: list[str],
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    """Build a prompt for generating the preface (前言) of the textbook."""

    payload = {
        "book_title": book_title,
        "project_titles": project_titles,
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "你是职业教育数字教材的前言撰写专家。"
        "你需要为整本教材撰写一篇前言，介绍教材的使用方法、结构编排与配套资源。"
        "语言面向学生，简洁明了、实用。"
        "禁止输出证据编号、chunk_id、文件名、路径、时间码、review_status、待人工复核、agent 等内部信息。"
        "只输出纯文本 Markdown（可含段落和小标题），不要代码块，不要解释。"
        "控制在 400–600 字。"
    )
    user = "\n".join(
        [
            "请为以下数字教材撰写前言：",
            "",
            clipped,
            "",
            "撰写要求：",
            "1. 说明教材的项目化结构（项目→任务）与数字资源特点。",
            "2. 介绍能力图谱、示范视频、互动问答等数字功能的使用方法。",
            "3. 提示学生如何结合视频、知识点和任务进行高效学习。",
            "4. 结尾给出鼓励性语句。",
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_project_intro_messages(
    *,
    project_title: str,
    learning_goals: list[str],
    task_titles: list[str],
    material_summary: str = "",
    max_chars: int = 3000,
) -> list[dict[str, str]]:
    """Build a prompt for generating the project introduction (项目导学)."""

    payload = {
        "project_title": project_title,
        "learning_goals": learning_goals,
        "task_titles": task_titles,
        "material_summary": material_summary[:max_chars],
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "你是职业教育数字教材的项目导学撰写专家。"
        "你需要为单个项目撰写导学文字，帮助学生快速了解本项目学什么、为什么学、怎么学。"
        "语言面向学生，简明、有引导性。"
        "禁止输出证据编号、chunk_id、文件名、路径、时间码、review_status、待人工复核、agent 等内部信息。"
        "只输出纯文本（2-3 个自然段），不要 Markdown 标题，不要代码块，不要解释。"
        "控制在 200–400 字。"
    )
    user = "\n".join(
        [
            "请为以下项目撰写导学：",
            "",
            clipped,
            "",
            "撰写要求：",
            "1. 第一段说明本项目的实际工作场景与重要性。",
            "2. 第二段概述项目涉及的主要任务与知识点。",
            "3. 第三段提示学习方法建议（结合视频与操作实践）。",
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_project_summary_messages(
    *,
    project_title: str,
    learning_goals: list[str],
    task_titles: list[str],
    knowledge_points: list[str],
    max_chars: int = 3000,
) -> list[dict[str, str]]:
    """Build a prompt for generating the project summary (项目小结)."""

    payload = {
        "project_title": project_title,
        "learning_goals": learning_goals,
        "task_titles": task_titles,
        "knowledge_points": knowledge_points,
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "你是职业教育数字教材的项目小结撰写专家。"
        "你需要为单个项目撰写小结，帮助学生回顾本项目学到的核心能力、知识与操作要点。"
        "语言面向学生，精炼、有条理。"
        "禁止输出证据编号、chunk_id、文件名、路径、时间码、review_status、待人工复核、agent 等内部信息。"
        "只输出纯文本 Markdown（可含要点列表），不要代码块，不要解释。"
        "控制在 200–400 字。"
    )
    user = "\n".join(
        [
            "请为以下项目撰写项目小结：",
            "",
            clipped,
            "",
            "撰写要求：",
            "1. 用一段话概括本项目的核心能力目标。",
            "2. 用要点列表梳理关键知识点与操作注意事项。",
            "3. 给出拓展学习建议。",
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
