from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from materials2textbook.outline.formatter import format_number
from materials2textbook.outline.models import Outline, ValidationReport


INTERNAL_LEAK_PATTERNS = [
    re.compile(r"\bPPT\b", re.IGNORECASE),
    re.compile(r"\bchunk_id\b", re.IGNORECASE),
    re.compile(r"\bsource_id\b", re.IGNORECASE),
    re.compile(r"\bPending\b", re.IGNORECASE),
    re.compile(r"\bC\d{3,}\b"),
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"/(?:ai|root|home|tmp|mnt|data)/"),
]

TITLE_QUALITY_PATTERNS = [
    "视频片段",
    "课堂应用示例",
    "章节概述",
]

GENERIC_STRUCTURAL_TITLES = {
    "情境导入",
    "项目导学",
    "学习目标",
    "任务目标",
    "知识准备",
    "任务实施",
    "任务评价",
    "项目小结",
    "任务小结",
    "拓展学习",
}


def validate_outline(outline: Any, book_json: Any) -> ValidationReport:
    outline_data = _as_mapping(outline)
    book_data = _as_mapping(book_json)
    report = ValidationReport()

    source_index = _build_source_index(book_data)
    seen_display_numbers: set[str] = set()
    seen_source_ids: set[str] = set()

    projects = _as_list(outline_data.get("projects"))
    book_projects = _as_list(book_data.get("projects"))
    if len(projects) != len(book_projects):
        report.add_error(
            "project_count_mismatch",
            "projects",
            f"大纲项目数 {len(projects)} 与正文项目数 {len(book_projects)} 不一致。",
            "以 canonical book JSON 的 projects 数组重新生成标准大纲。",
        )

    for project_index, project in enumerate(projects, start=1):
        project_map = _as_mapping(project)
        project_location = f"projects[{project_index}]"
        _validate_node(
            report=report,
            node=project_map,
            expected_path=[project_index],
            level="project",
            location=project_location,
            source_index=source_index,
            seen_display_numbers=seen_display_numbers,
            seen_source_ids=seen_source_ids,
        )

        source_project = _book_project_by_position(book_projects, project_index)
        tasks = _as_list(project_map.get("tasks"))
        book_tasks = _as_list(source_project.get("tasks")) if source_project else []
        if len(tasks) != len(book_tasks):
            report.add_error(
                "task_count_mismatch",
                f"{project_location}.tasks",
                f"大纲任务数 {len(tasks)} 与正文任务数 {len(book_tasks)} 不一致。",
                "以 canonical book JSON 的 tasks 数组重新生成标准大纲。",
                str(project_map.get("source_node_id") or ""),
            )

        for task_index, task in enumerate(tasks, start=1):
            task_map = _as_mapping(task)
            task_location = f"{project_location}.tasks[{task_index}]"
            _validate_node(
                report=report,
                node=task_map,
                expected_path=[project_index, task_index],
                level="task",
                location=task_location,
                source_index=source_index,
                seen_display_numbers=seen_display_numbers,
                seen_source_ids=seen_source_ids,
            )

            source_task = _book_task_by_position(book_tasks, task_index)
            units = _as_list(task_map.get("learning_units"))
            book_units = _as_list(source_task.get("blocks")) if source_task else []
            if len(units) != len(book_units):
                report.add_error(
                    "learning_unit_count_mismatch",
                    f"{task_location}.learning_units",
                    f"大纲学习单元数 {len(units)} 与正文块数 {len(book_units)} 不一致。",
                    "以 canonical book JSON 的 blocks 数组重新生成标准大纲。",
                    str(task_map.get("source_node_id") or ""),
                )

            for unit_index, unit in enumerate(units, start=1):
                _validate_node(
                    report=report,
                    node=_as_mapping(unit),
                    expected_path=[project_index, task_index, unit_index],
                    level="learning_unit",
                    location=f"{task_location}.learning_units[{unit_index}]",
                    source_index=source_index,
                    seen_display_numbers=seen_display_numbers,
                    seen_source_ids=seen_source_ids,
                )

    _validate_duplicate_specific_titles(report, projects)
    _validate_parent_child_title_distinctness(report, projects)
    return report


def _validate_duplicate_specific_titles(report: ValidationReport, projects: list[Any]) -> None:
    _warn_duplicate_title_group(
        report,
        [(f"projects[{index}]", _as_mapping(project)) for index, project in enumerate(projects, start=1)],
    )
    for project_index, project in enumerate(projects, start=1):
        project_map = _as_mapping(project)
        tasks = _as_list(project_map.get("tasks"))
        _warn_duplicate_title_group(
            report,
            [(f"projects[{project_index}].tasks[{task_index}]", _as_mapping(task)) for task_index, task in enumerate(tasks, start=1)],
        )
        for task_index, task in enumerate(tasks, start=1):
            task_map = _as_mapping(task)
            units = _as_list(task_map.get("learning_units"))
            _warn_duplicate_title_group(
                report,
                [
                    (f"projects[{project_index}].tasks[{task_index}].learning_units[{unit_index}]", _as_mapping(unit))
                    for unit_index, unit in enumerate(units, start=1)
                ],
            )


def _warn_duplicate_title_group(report: ValidationReport, nodes: list[tuple[str, dict[str, Any]]]) -> None:
    title_locations: dict[str, list[tuple[str, str, str]]] = {}
    for location, node in nodes:
        title = str(node.get("title") or "").strip()
        normalized = _normalize_title_for_duplicate_check(title)
        if not normalized or normalized in GENERIC_STRUCTURAL_TITLES:
            continue
        source_node_id = str(node.get("source_node_id") or "")
        title_locations.setdefault(normalized, []).append((location, title, source_node_id))

    for matches in title_locations.values():
        if len(matches) <= 1:
            continue
        locations = "、".join(location for location, _, _ in matches)
        for location, title, source_node_id in matches:
            report.add_warning(
                "duplicate_sibling_specific_title",
                location,
                f"同一父级、同一层级下具体标题重复：`{title}`；重复位置：{locations}。",
                "除结构性标题外，同一父级下的具体教学标题应根据实际内容区分；如内容实质相同，应进入合并复核。",
                source_node_id,
            )


def _normalize_title_for_duplicate_check(title: str) -> str:
    normalized = str(title or "").strip()
    normalized = re.sub(r"^\d+(?:[.．-]\d+)*\s*", "", normalized)
    for term in TITLE_QUALITY_PATTERNS:
        normalized = normalized.replace(term, "")
    return re.sub(r"\s+", "", normalized).strip(" ：:，,。；;、")

def _validate_parent_child_title_distinctness(report: ValidationReport, projects: list[Any]) -> None:
    for project_index, project in enumerate(projects, start=1):
        project_map = _as_mapping(project)
        project_title = _normalize_title_for_duplicate_check(str(project_map.get("title") or ""))
        for task_index, task in enumerate(_as_list(project_map.get("tasks")), start=1):
            task_map = _as_mapping(task)
            task_location = f"projects[{project_index}].tasks[{task_index}]"
            task_title = _normalize_title_for_duplicate_check(str(task_map.get("title") or ""))
            if project_title and task_title and project_title == task_title and task_title not in GENERIC_STRUCTURAL_TITLES:
                report.add_warning(
                    "parent_child_same_title",
                    task_location,
                    f"父子层级标题相同：`{task_map.get('title') or ''}`。",
                    "父节点应表达总体主题，子节点应体现更细的学习内容或学习动作。",
                    str(task_map.get("source_node_id") or ""),
                )
            for unit_index, unit in enumerate(_as_list(task_map.get("learning_units")), start=1):
                unit_map = _as_mapping(unit)
                unit_title = _normalize_title_for_duplicate_check(str(unit_map.get("title") or ""))
                if task_title and unit_title and task_title == unit_title and unit_title not in GENERIC_STRUCTURAL_TITLES:
                    report.add_warning(
                        "parent_child_same_title",
                        f"{task_location}.learning_units[{unit_index}]",
                        f"父子层级标题相同：`{unit_map.get('title') or ''}`。",
                        "任务标题应表达总体主题，学习单元标题应体现该主题下的具体拆分。",
                        str(unit_map.get("source_node_id") or ""),
                    )


def _validate_node(
    *,
    report: ValidationReport,
    node: dict[str, Any],
    expected_path: list[int],
    level: str,
    location: str,
    source_index: dict[str, dict[str, Any]],
    seen_display_numbers: set[str],
    seen_source_ids: set[str],
) -> None:
    source_node_id = str(node.get("source_node_id") or "")
    number_path = _coerce_number_path(node.get("number_path"))
    display_number = str(node.get("display_number") or "")
    title = str(node.get("title") or "")

    if not source_node_id:
        report.add_error("missing_source_node_id", location, "大纲节点缺少 source_node_id。", "重新生成标准大纲并绑定正文节点 ID。")
    elif source_node_id in seen_source_ids:
        report.add_error(
            "duplicate_source_node_id",
            location,
            f"source_node_id 重复：{source_node_id}。",
            "确认一个大纲节点只绑定一个正文节点。",
            source_node_id,
        )
    else:
        seen_source_ids.add(source_node_id)

    if number_path != expected_path:
        report.add_error(
            "number_path_not_continuous",
            location,
            f"number_path 应为 {expected_path}，实际为 {number_path}。",
            "编号必须由父级数组顺序重新生成，禁止继承旧编号或跳号。",
            source_node_id,
        )

    try:
        expected_display = format_number(level, expected_path)
    except ValueError as exc:
        report.add_error("invalid_expected_number", location, str(exc), "检查 formatter 编号规则。", source_node_id)
        expected_display = ""

    if display_number != expected_display:
        report.add_error(
            "display_number_mismatch",
            location,
            f"display_number 应为 {expected_display}，实际为 {display_number or '<空>'}。",
            "display_number 必须由 formatter 统一生成。",
            source_node_id,
        )
    elif display_number in seen_display_numbers:
        report.add_error(
            "duplicate_display_number",
            location,
            f"display_number 重复：{display_number}。",
            "重新生成编号并检查层级数组是否重复。",
            source_node_id,
        )
    else:
        seen_display_numbers.add(display_number)

    if source_node_id:
        source_node = source_index.get(source_node_id)
        if not source_node:
            report.add_error(
                "source_node_missing",
                location,
                f"source_node_id 在正文中不存在：{source_node_id}。",
                "大纲必须从当前 canonical book JSON 生成，不能使用旧大纲。",
                source_node_id,
            )
        else:
            source_title = str(source_node.get("title") or "")
            if title != source_title:
                report.add_error(
                    "title_mismatch",
                    location,
                    f"大纲标题与正文标题不一致：大纲 `{title}`，正文 `{source_title}`。",
                    "导出层不得改写标题；请在正文生成或 reviewer 阶段修正标题。",
                    source_node_id,
                )
            if source_node.get("level") != level:
                report.add_error(
                    "source_level_mismatch",
                    location,
                    f"大纲层级 {level} 与正文节点层级 {source_node.get('level')} 不一致。",
                    "检查 source_node_id 是否绑定到了正确层级。",
                    source_node_id,
                )

    _validate_text_quality(report, title, location, source_node_id)


def _validate_text_quality(report: ValidationReport, title: str, location: str, source_node_id: str) -> None:
    for pattern in INTERNAL_LEAK_PATTERNS:
        if pattern.search(title):
            report.add_error(
                "internal_leak",
                location,
                f"标题疑似包含内部研发信息：`{title}`。",
                "内部 ID、状态、绝对路径和研发标记必须在发布前清除。",
                source_node_id,
            )
            break

    for term in TITLE_QUALITY_PATTERNS:
        if term in title:
            report.add_warning(
                "title_quality",
                location,
                f"标题可能偏过程或素材标签：`{title}`。",
                "建议在正文生成或 reviewer 阶段改成稳定的教学内容标题。",
                source_node_id,
            )
            break


def _build_source_index(book_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for project in _as_list(book_data.get("projects")):
        project_map = _as_mapping(project)
        _add_source(index, project_map, "project", "project_id")
        for task in _as_list(project_map.get("tasks")):
            task_map = _as_mapping(task)
            _add_source(index, task_map, "task", "task_id")
            for block in _as_list(task_map.get("blocks")):
                _add_source(index, _as_mapping(block), "learning_unit", "block_id")
    return index


def _add_source(index: dict[str, dict[str, Any]], node: dict[str, Any], level: str, id_key: str) -> None:
    source_id = str(node.get(id_key) or node.get("id") or "")
    if not source_id:
        return
    index[source_id] = {"level": level, "title": str(node.get("title") or "")}


def _book_project_by_position(projects: list[Any], one_based_index: int) -> dict[str, Any]:
    if one_based_index > len(projects):
        return {}
    return _as_mapping(projects[one_based_index - 1])


def _book_task_by_position(tasks: list[Any], one_based_index: int) -> dict[str, Any]:
    if one_based_index > len(tasks):
        return {}
    return _as_mapping(tasks[one_based_index - 1])


def _coerce_number_path(value: Any) -> list[int]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    path: list[int] = []
    for item in value:
        if isinstance(item, int):
            path.append(item)
        else:
            return []
    return path


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Outline):
        return value.to_dict()
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []
