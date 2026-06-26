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
    """Build a prompt for generating a structured student-facing ability graph."""

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
        "You are a vocational digital textbook ability graph (能力图谱) design agent. "
        "Generate a student-facing structured JSON graph from the chapter goals, tasks, knowledge points, and content. "
        "Do not output internal evidence IDs, chunk IDs, file names, paths, timecodes, review status, or agent notes. "
        "Return only one JSON object. Do not use Markdown fences."
    )
    user = "\n".join(
        [
            "Generate an ability graph matching this schema:",
            "",
            "{",
            '  "schema": "materials2textbook.ability_graph.v1",',
            '  "columns": [{"id": "project|task|ability|knowledge|content", "title": "column title"}],',
            '  "nodes": [{"id": "stable_ascii_id", "column": "project|task|ability|knowledge|content", "label": "student-visible label"}],',
            '  "edges": [{"from": "source_node_id", "to": "target_node_id"}]',
            "}",
            "",
            "Requirements:",
            "1. Include exactly these five layers: project, task, ability, knowledge, content.",
            "2. Ability labels must describe real target abilities for this domain, using the supplied domain and operation terms.",
            "3. Knowledge labels must come from the tasks and may merge close synonyms.",
            "4. Content labels must describe concrete learning content. They must not repeat the knowledge label verbatim.",
            "5. Edges may only connect adjacent layers: project -> task -> ability -> knowledge -> content.",
            "6. Every non-project node should have one upstream parent.",
            "7. Keep the graph concise: no more than 10 ability nodes, 12 knowledge nodes, and 12 content nodes.",
            "",
            "Textbook structure:",
            clipped,
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
