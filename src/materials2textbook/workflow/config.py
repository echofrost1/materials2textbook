from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkflowConfig:
    """Runtime controls for the orchestration MVP."""

    include_pending: bool = True
    include_rejected: bool = False
    allowed_review_statuses: set[str] = field(default_factory=set)
    min_teaching_value: float = 0.0
    max_chunks_per_knowledge_point: int | None = None
    max_input_tokens: int = 0
    max_tokens_per_evidence_chunk: int = 1200
    summarize_over_budget: bool = False
    summary_token_reserve_ratio: float = 0.3
    max_tokens_per_summary_chunk: int = 500
    max_summary_source_chunks: int = 8
    review_rounds: int = 1
    copy_media_assets: bool = True

    def allows_review_status(self, status: str) -> bool:
        normalized = status.strip().lower()
        if self.allowed_review_statuses:
            return normalized in {item.lower() for item in self.allowed_review_statuses}
        if "rejected" in normalized and not self.include_rejected:
            return False
        if self.include_pending:
            return True
        return "approved" in normalized

    def allows_teaching_value(self, value: float) -> bool:
        return value >= self.min_teaching_value

    def normalized_review_rounds(self) -> int:
        return max(1, self.review_rounds)

    def token_budget_enabled(self) -> bool:
        return self.max_input_tokens > 0

    def normalized_max_tokens_per_evidence_chunk(self) -> int:
        return max(1, self.max_tokens_per_evidence_chunk)

    def normalized_summary_token_reserve_ratio(self) -> float:
        return min(0.8, max(0.0, self.summary_token_reserve_ratio))

    def normalized_max_tokens_per_summary_chunk(self) -> int:
        return max(1, self.max_tokens_per_summary_chunk)

    def normalized_max_summary_source_chunks(self) -> int:
        return max(1, self.max_summary_source_chunks)
