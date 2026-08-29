from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from materials2textbook.outline.formatter import (
    format_learning_unit_number,
    format_project_number,
    format_task_number,
)
from materials2textbook.outline.models import (
    LearningUnitOutlineNode,
    Outline,
    ProjectOutlineNode,
    TaskOutlineNode,
)


def generate_standard_outline(book_json: Any) -> Outline:
    """Build a deterministic project-task-learning-unit outline from canonical book JSON.

    The generator does not rewrite titles and does not trust existing numbering.
    Array order in the canonical book is the only source for display order.
    """
    book = _as_mapping(book_json)
    projects: list[ProjectOutlineNode] = []
    for project_index, project in enumerate(_as_list(book.get("projects")), start=1):
        project_map = _as_mapping(project)
        project_path = [project_index]
        tasks: list[TaskOutlineNode] = []
        for task_index, task in enumerate(_as_list(project_map.get("tasks")), start=1):
            task_map = _as_mapping(task)
            task_path = [project_index, task_index]
            learning_units: list[LearningUnitOutlineNode] = []
            for unit_index, block in enumerate(_as_list(task_map.get("blocks")), start=1):
                block_map = _as_mapping(block)
                unit_path = [project_index, task_index, unit_index]
                learning_units.append(
                    LearningUnitOutlineNode(
                        source_node_id=_source_id(block_map, "block_id", unit_path),
                        number_path=unit_path,
                        display_number=format_learning_unit_number(unit_path),
                        title=_title(block_map),
                    )
                )
            tasks.append(
                TaskOutlineNode(
                    source_node_id=_source_id(task_map, "task_id", task_path),
                    number_path=task_path,
                    display_number=format_task_number(task_path),
                    title=_title(task_map),
                    learning_units=learning_units,
                )
            )
        projects.append(
            ProjectOutlineNode(
                source_node_id=_source_id(project_map, "project_id", project_path),
                number_path=project_path,
                display_number=format_project_number(project_path),
                title=_title(project_map),
                tasks=tasks,
            )
        )

    return Outline(title=str(book.get("title") or ""), projects=projects)


def _as_mapping(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _title(node: dict[str, Any]) -> str:
    return str(node.get("title") or "").strip()


def _source_id(node: dict[str, Any], preferred_key: str, number_path: list[int]) -> str:
    raw_id = node.get(preferred_key) or node.get("id")
    if raw_id:
        return str(raw_id)
    return f"generated_{'-'.join(str(part) for part in number_path)}"
