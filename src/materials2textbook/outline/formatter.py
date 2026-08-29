from __future__ import annotations


_CN_DIGITS = "零一二三四五六七八九"


def format_project_number(number_path: list[int] | tuple[int, ...]) -> str:
    _require_path_length(number_path, 1, "project")
    return f"项目{_to_chinese_number(number_path[0])}"


def format_task_number(number_path: list[int] | tuple[int, ...]) -> str:
    _require_path_length(number_path, 2, "task")
    return f"任务{number_path[0]}-{number_path[1]}"


def format_learning_unit_number(number_path: list[int] | tuple[int, ...]) -> str:
    _require_path_length(number_path, 3, "learning_unit")
    return f"学习单元{number_path[0]}-{number_path[1]}-{number_path[2]}"


def format_number(level: str, number_path: list[int] | tuple[int, ...]) -> str:
    if level == "project":
        return format_project_number(number_path)
    if level == "task":
        return format_task_number(number_path)
    if level == "learning_unit":
        return format_learning_unit_number(number_path)
    raise ValueError(f"Unknown outline level: {level}")


def _require_path_length(number_path: list[int] | tuple[int, ...], expected: int, level: str) -> None:
    if len(number_path) != expected:
        raise ValueError(f"{level} number_path must have {expected} item(s), got {list(number_path)}")
    if any(not isinstance(value, int) or value <= 0 for value in number_path):
        raise ValueError(f"{level} number_path must contain positive integers, got {list(number_path)}")


def _to_chinese_number(value: int) -> str:
    if value <= 0:
        raise ValueError(f"Chinese project number must be positive, got {value}")
    if value < 10:
        return _CN_DIGITS[value]
    if value == 10:
        return "十"
    if value < 20:
        return f"十{_CN_DIGITS[value % 10]}"
    if value < 100:
        tens, ones = divmod(value, 10)
        return f"{_CN_DIGITS[tens]}十{_CN_DIGITS[ones] if ones else ''}"
    return str(value)
