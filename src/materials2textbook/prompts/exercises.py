from __future__ import annotations

import json


def build_exercises_messages(
    *,
    task_title: str,
    knowledge_points: list[dict],
    evidence_summary: str,
    fill_blank_count: int = 5,
    thinking_count: int = 2,
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    """Build a prompt for generating fill-in-the-blank and thinking exercises.

    The agent must output a strict JSON object with two string lists. The caller
    is responsible for adding the ``N. 【填空】`` / ``N. 【思考】`` numbering and
    tags, so the model only returns the raw question text.
    """

    payload = {
        "task_title": task_title,
        "knowledge_points": knowledge_points,
        "evidence": evidence_summary[:max_chars],
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]

    system = (
        "你是职业教育数字教材的练习设计 Agent。"
        "你的任务是根据任务的知识点和教学证据，设计学生端的填空题和思考题。"
        "所有题目必须严格基于提供的证据内容，不得编造、扩展或引入证据之外的专业知识。"
        "如果证据不足以支撑某类题目，宁缺毋滥，减少数量也不要凑数。"
        "只输出一个 JSON 对象，不要 Markdown 代码块，不要解释，不要思考过程。"
    )

    user = "\n".join(
        [
            "请生成符合以下 schema 的练习题：",
            "",
            "{",
            '  "fill_blank": ["填空题正文1", "填空题正文2", ...],',
            '  "thinking": ["思考题正文1", "思考题正文2", ...]',
            "}",
            "",
            "## 填空题要求（generate up to " + str(fill_blank_count) + " items）",
            "1. 每道题是一个完整的陈述句，用 \"______\"（六个下划线）标记唯一的空缺位置。",
            "2. 空缺必须是证据中明确出现的关键术语、数值、分类或工艺名称，不能是主观表述或整句。",
            "3. 句末必须紧跟 \"（答案：XXX）\" 给出答案；多个并列答案用顿号分隔，如 \"（答案：熔化焊、压力焊）\"。",
            "4. 答案要简洁（通常 2-8 字），与证据原文表述一致，不要同义改写。",
            "5. 不要在题干里写编号、\"【填空】\" 标签或题号，系统会自动添加。",
            "6. 示范：\"焊接是通过加热或加压，使两个或多个工件达到______结合的加工工艺。（答案：原子级）\"",
            "",
            "## 思考题要求（generate up to " + str(thinking_count) + " items）",
            "1. 每道题要求结合证据进行原理分析、对比辨析或迁移应用，避免简单复述概念。",
            "2. 思考题不要给答案，以问号 \"？\" 结尾。",
            "3. 题干要具体、可作答，避免空泛的 \"说明关键内容\" 之类套话。",
            "4. 不要写编号或 \"【思考】\" 标签。",
            "5. 示范：\"对比熔化焊和压力焊的工作原理，说明二者在结合机理上的本质区别。\"",
            "",
            "## 通用要求",
            "- 专业术语必须准确（焊接、钨极、坡口、电弧、熔池等），不要写错别字。",
            "- 语言符合中等职业教育学生的认知水平，句子通顺。",
            "- 每道题独立成句，题目之间不要重复知识点。",
            "- 严禁输出证据中不存在的参数、数值或工艺条件。",
            "",
            "## 任务知识点与教学证据",
            clipped,
        ]
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
