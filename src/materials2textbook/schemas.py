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
    order_index: int = 0
    difficulty_level: str = "basic"
    prerequisite_ids: list[str] = field(default_factory=list)
    cluster_id: str = ""


@dataclass
class LearningActivity:
    activity_id: str
    type: str
    difficulty_level: str
    prompt: str
    target_knowledge_point_ids: list[str] = field(default_factory=list)
    evidence_chunk_ids: list[str] = field(default_factory=list)
    rubric: list[str] = field(default_factory=list)


@dataclass
class CaseExample:
    case_id: str
    title: str
    prompt: str
    reference_answer: str
    target_knowledge_point_ids: list[str] = field(default_factory=list)
    evidence_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class ChapterPlan:
    chapter_id: str
    title: str
    learning_goals: list[str]
    knowledge_points: list[KnowledgePoint]
    evidence_chunk_ids: list[str]
    activities: list[str] = field(default_factory=list)
    learning_path: list[str] = field(default_factory=list)
    activity_items: list[LearningActivity] = field(default_factory=list)
    case_examples: list[CaseExample] = field(default_factory=list)


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
class ParagraphSupport:
    paragraph_id: str
    text: str
    cited_chunk_ids: list[str]
    unknown_chunk_ids: list[str]
    support_status: str
    score: float
    notes: str = ""


@dataclass
class ClaimSupport:
    claim_id: str
    paragraph_id: str
    text: str
    cited_chunk_ids: list[str]
    unknown_chunk_ids: list[str]
    support_status: str
    score: float
    notes: str = ""


@dataclass
class ClaimConsistencyIssue:
    issue_id: str
    topic: str
    claim_ids: list[str]
    cited_chunk_ids: list[str]
    message: str


@dataclass
class ReviewReport:
    chapter_id: str
    chapter_title: str
    fact_issues: list[ReviewIssue] = field(default_factory=list)
    pedagogy_issues: list[ReviewIssue] = field(default_factory=list)
    missing_content: list[str] = field(default_factory=list)
    revision_suggestions: list[str] = field(default_factory=list)


@dataclass
class DigitalBookBlock:
    block_id: str
    type: str
    title: str
    markdown: str = ""
    items: list[str] = field(default_factory=list)
    src: str = ""
    poster: str = ""
    start_time: str = ""
    end_time: str = ""
    evidence_chunk_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DigitalBookTask:
    task_id: str
    title: str
    blocks: list[DigitalBookBlock]
    knowledge_points: list[str] = field(default_factory=list)
    key_terms: list[str] = field(default_factory=list)
    evidence_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class DigitalBookProject:
    project_id: str
    title: str
    project_intro: str
    ability_map: list[str]
    learning_goals: list[str]
    tasks: list[DigitalBookTask]


@dataclass
class DigitalBook:
    book_id: str
    title: str
    metadata: dict[str, Any]
    projects: list[DigitalBookProject]
    assets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class WorkflowOutputs:
    outline_path: str
    outline_markdown_path: str
    evidence_chunks_path: str
    evidence_markdown_path: str
    chapter_plan_path: str
    draft_path: str
    draft_docx_path: str
    review_report_path: str
    review_markdown_path: str
    review_history_path: str
    revision_diff_path: str
    summary_path: str
    final_path: str
    final_docx_path: str
    manifest_path: str
    digital_book_dir: str = ""
    digital_book_path: str = ""
    digital_book_index_path: str = ""
    digital_book_review_path: str = ""
    digital_book_review_markdown_path: str = ""


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
    evidence_coverage_rate: float = 0.0
    citation_coverage_rate: float = 0.0
    paragraph_support_rate: float = 0.0
    claim_support_rate: float = 0.0
    approved_evidence_rate: float = 0.0
    pedagogy_completeness_rate: float = 0.0
    activity_quality_rate: float = 0.0
    case_quality_rate: float = 0.0
    overall_quality_score: float = 0.0
    review_status_counts: dict[str, int] = field(default_factory=dict)
    material_block_counts: dict[str, int] = field(default_factory=dict)
