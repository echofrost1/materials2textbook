from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceLocator:
    path: str = ""
    original_path: str = ""
    page: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    keyframe_paths: list[str] = field(default_factory=list)


@dataclass
class EvidenceScore:
    relevance: float = 0.0
    teaching_value: float = 0.0
    confidence: float = 0.0


@dataclass
class EvidenceChunk:
    chunk_id: str
    asset_id: str
    title: str
    content: str
    summary: str
    keywords: list[str]
    subject: str
    material_block: str
    material_block_code: str
    recommended_chapter: str
    locator: EvidenceLocator
    score: EvidenceScore
    source_type: str = "video_segment"
    review_status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgePoint:
    knowledge_point_id: str
    title: str
    chunk_ids: list[str]
    summary: str = ""


@dataclass
class ChapterPlan:
    chapter_id: str
    title: str
    learning_goals: list[str]
    knowledge_points: list[KnowledgePoint]
    evidence_chunk_ids: list[str]
    activities: list[str] = field(default_factory=list)


@dataclass
class OutlineTopic:
    topic_id: str
    title: str
    chunk_ids: list[str]
    summary: str = ""


@dataclass
class OutlineSection:
    section_id: str
    title: str
    topics: list[OutlineTopic]


@dataclass
class TextbookOutline:
    chapter_id: str
    title: str
    sections: list[OutlineSection]


@dataclass
class ReviewIssue:
    severity: str
    location: str
    message: str
    suggestion: str


@dataclass
class ReviewReport:
    chapter_id: str
    chapter_title: str
    fact_issues: list[ReviewIssue] = field(default_factory=list)
    pedagogy_issues: list[ReviewIssue] = field(default_factory=list)
    missing_content: list[str] = field(default_factory=list)
    revision_suggestions: list[str] = field(default_factory=list)


@dataclass
class WorkflowOutputs:
    outline_path: str
    outline_markdown_path: str
    evidence_chunks_path: str
    chapter_plan_path: str
    draft_path: str
    draft_docx_path: str
    review_report_path: str
    review_markdown_path: str
    summary_path: str
    final_path: str
    final_docx_path: str


@dataclass
class WorkflowSummary:
    title: str
    source_records: int
    evidence_chunks: int
    skipped_chunks: int
    chapters: int
    knowledge_points: int
    fact_issue_count: int
    pedagogy_issue_count: int
    high_issue_count: int
    medium_issue_count: int
    low_issue_count: int
    review_status_counts: dict[str, int] = field(default_factory=dict)
    material_block_counts: dict[str, int] = field(default_factory=dict)
