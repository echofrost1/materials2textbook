from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LearningUnitOutlineNode:
    source_node_id: str
    number_path: list[int]
    display_number: str
    title: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskOutlineNode:
    source_node_id: str
    number_path: list[int]
    display_number: str
    title: str
    learning_units: list[LearningUnitOutlineNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectOutlineNode:
    source_node_id: str
    number_path: list[int]
    display_number: str
    title: str
    tasks: list[TaskOutlineNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Outline:
    title: str
    schema_version: str = "materials2textbook.standard_outline.v1"
    projects: list[ProjectOutlineNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutlineValidationIssue:
    severity: str
    code: str
    location: str
    message: str
    suggestion: str
    source_node_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    errors: list[OutlineValidationIssue] = field(default_factory=list)
    warnings: list[OutlineValidationIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors and not self.warnings

    @property
    def preview_allowed(self) -> bool:
        return not self.errors

    @property
    def release_allowed(self) -> bool:
        return self.passed

    def add_error(self, code: str, location: str, message: str, suggestion: str, source_node_id: str = "") -> None:
        self.errors.append(OutlineValidationIssue("ERROR", code, location, message, suggestion, source_node_id))

    def add_warning(self, code: str, location: str, message: str, suggestion: str, source_node_id: str = "") -> None:
        self.warnings.append(OutlineValidationIssue("WARNING", code, location, message, suggestion, source_node_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "passed": self.passed,
            "preview_allowed": self.preview_allowed,
            "release_allowed": self.release_allowed,
        }
