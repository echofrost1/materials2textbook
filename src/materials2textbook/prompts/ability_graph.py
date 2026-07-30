from __future__ import annotations

import json

from materials2textbook.domain_config import DomainConfig, default_domain_config


def build_ability_graph_messages(
    *,
    project_title: str,
    learning_goals: list[str],
    tasks: list[dict],
    fallback_graph: dict,
    max_chars: int = 6000,
    domain_config: DomainConfig | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for generating a student-facing competency matrix."""

    config = domain_config or default_domain_config()
    payload = {
        "project_title": project_title,
        "learning_goals": learning_goals,
        "tasks": tasks,
        "fallback_graph": fallback_graph,
        "domain_config": config.to_dict(),
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "You are a vocational digital textbook ability graph design agent. "
        "Generate a student-facing competency matrix from the project goals, tasks, key actions, and assessment evidence. "
        "Do not output internal evidence IDs, chunk IDs, file names, paths, timecodes, review status, or agent notes. "
        "Return only one JSON object. Do not use Markdown fences."
    )
    user = "\n".join(
        [
            "Generate an ability graph matching this schema:",
            "",
            "{",
            '  "schema": "materials2textbook.ability_graph.v1",',
            '  "columns": [{"id": "project|ability_domain|task|action|assessment", "title": "column title"}],',
            '  "nodes": [{"id": "stable_ascii_id", "column": "project|ability_domain|task|action|assessment", "label": "student-visible label"}],',
            '  "edges": [{"from": "source_node_id", "to": "target_node_id"}]',
            "}",
            "",
            "Requirements:",
            "1. Include exactly these five layers: project, ability_domain, task, action, assessment.",
            "2. Ability-domain labels should be broad vocational capacities, such as safety preparation, process parameter judgment, operation control, and quality improvement.",
            "3. Task labels should be concise versions of supplied project tasks, not raw material titles.",
            "4. Action labels must be observable job actions students can perform.",
            "5. Assessment labels must state concrete evidence such as parameter records, operation observation, workpiece result, inspection record, or defect correction plan.",
            "6. Edges may only connect adjacent layers: project -> ability_domain -> task -> action -> assessment.",
            "7. Every non-project node should have one upstream parent.",
            "8. Keep the graph concise: 4 to 5 ability domains and one action/assessment path per task.",
            "",
            "Textbook structure:",
            clipped,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
