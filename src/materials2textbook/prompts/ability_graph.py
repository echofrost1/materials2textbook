from __future__ import annotations

import json


def build_ability_graph_messages(
    *,
    project_title: str,
    learning_goals: list[str],
    tasks: list[dict],
    fallback_graph: dict,
    max_chars: int = 6000,
) -> list[dict[str, str]]:
    """Build a prompt for generating a structured student-facing ability graph."""

    payload = {
        "project_title": project_title,
        "learning_goals": learning_goals,
        "tasks": tasks,
        "fallback_graph": fallback_graph,
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "你是职业教育数字教材的能力图谱设计 Agent。"
        "你需要根据章节目标、任务、知识点和学习内容，生成学生端可展示的结构化能力图谱 JSON。"
        "禁止输出证据编号、chunk_id、文件名、路径、时间码、review_status、待人工复核、agent 等内部信息。"
        "只输出 JSON 对象，不要 Markdown 代码块，不要解释。"
    )
    user = "\n".join(
        [
            "请生成符合以下 schema 的能力图谱：",
            "",
            "{",
            '  "schema": "materials2textbook.ability_graph.v1",',
            '  "columns": [{"id": "project|task|ability|knowledge|content", "title": "列名"}],',
            '  "nodes": [{"id": "稳定英文或拼音ID", "column": "project|task|ability|knowledge|content", "label": "学生可见标题"}],',
            '  "edges": [{"from": "源节点ID", "to": "目标节点ID"}]',
            "}",
            "",
            "生成要求：",
            "1. 必须包含五列：project、task、ability、knowledge、content。",
            "2. ability 列不要机械复用模板，要概括本章真实能力，例如“识读数字化设备组成”“分析焊接参数影响”。",
            "3. knowledge 列来自任务知识点，可适度合并同义项。",
            "4. content 列来自学习内容/案例/视频，不要输出评价题或练习题。",
            "5. 连线应表达从项目到任务、能力、知识点、学习内容的依赖关系。",
            "6. 每列节点数量要克制：ability 不超过 6 个，knowledge 不超过 10 个，content 不超过 12 个。",
            "",
            "教材结构：",
            clipped,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
