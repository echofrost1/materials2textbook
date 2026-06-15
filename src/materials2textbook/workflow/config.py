from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkflowConfig:
    """Runtime controls for the orchestration MVP."""

    include_pending: bool = True
    allowed_review_statuses: set[str] = field(default_factory=set)
    min_teaching_value: float = 0.0
    max_chunks_per_knowledge_point: int | None = None

    def allows_review_status(self, status: str) -> bool:
        normalized = status.strip().lower()
        if self.allowed_review_statuses:
            return normalized in {item.lower() for item in self.allowed_review_statuses}
        if self.include_pending:
            return True
        return "approved" in normalized

    def allows_teaching_value(self, value: float) -> bool:
        return value >= self.min_teaching_value
