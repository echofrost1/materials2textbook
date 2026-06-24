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
            "2. ability 列要概括真实能力目标，例如「能规范完成送丝操作」「能判断焊接安全风险」。",
            "3. knowledge 列来自任务知识点，可适度合并同义项。",
            "4. content 列来自具体学习内容（正文段落、示范视频、案例示例），不要输出评价题或练习题。",
            "5. **content 节点的 label 必须与 knowledge 节点不同**。content 应描述具体学习材料，例如「焊接原理动画演示」「坡口制备操作视频」「安全防护案例分析」，而不是重复知识点名称。",
            "6. 连线只能连接相邻层级：project -> task -> ability -> knowledge -> content。",
            "7. 每个非 project 节点只能有一个上游父节点，避免多个能力汇聚到同一知识点或多个知识点汇聚到同一内容。",
            "8. 节点数量要克制：ability 不超过 10 个，knowledge 不超过 12 个，content 不超过 12 个。",
            "",
            "教材结构：",
            clipped,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
