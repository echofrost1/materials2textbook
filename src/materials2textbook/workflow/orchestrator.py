from __future__ import annotations

from pathlib import Path

from materials2textbook.agents.knowledge_organizer import KnowledgeOrganizerAgent
from materials2textbook.agents.outline_planner import OutlinePlannerAgent, render_outline_markdown
from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.agents.reviewers import EvidenceReviewerAgent, PedagogyReviewerAgent, ReviewComposer
from materials2textbook.agents.revision import RevisionAgent
from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.exporters.docx import markdown_to_docx
from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl, write_text
from materials2textbook.schemas import WorkflowOutputs
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.reporting import build_workflow_summary, render_review_markdown


class TextbookWorkflow:
    """Run the first multi-agent orchestration loop over processed material segments."""

    def __init__(self) -> None:
        self.resource_analyst = ResourceAnalystAgent()
        self.outline_planner = OutlinePlannerAgent()
        self.organizer = KnowledgeOrganizerAgent()
        self.writer = TextbookWriterAgent()
        self.evidence_reviewer = EvidenceReviewerAgent()
        self.pedagogy_reviewer = PedagogyReviewerAgent()
        self.review_composer = ReviewComposer()
        self.revision = RevisionAgent()

    def run(
        self,
        video_segments_path: Path,
        output_dir: Path,
        title: str,
        config: WorkflowConfig | None = None,
    ) -> WorkflowOutputs:
        config = config or WorkflowConfig()
        records = read_jsonl(video_segments_path)

        raw_chunks = self.resource_analyst.run(records)
        chunks = [
            chunk
            for chunk in raw_chunks
            if config.allows_review_status(chunk.review_status)
            and config.allows_teaching_value(chunk.score.teaching_value)
        ]
        plans = self.organizer.run(
            chunks,
            max_chunks_per_knowledge_point=config.max_chunks_per_knowledge_point,
        )
        outlines = self.outline_planner.run(chunks)
        outline_markdown = render_outline_markdown(outlines, title)
        draft = self.writer.run(plans, chunks, title=title)

        fact_issues = self.evidence_reviewer.run(plans, chunks)
        pedagogy_issues = self.pedagogy_reviewer.run(plans)
        reports = self.review_composer.run(plans, fact_issues, pedagogy_issues)
        summary = build_workflow_summary(
            title=title,
            source_records=len(records),
            evidence_chunks=chunks,
            skipped_chunks=len(raw_chunks) - len(chunks),
            plans=plans,
            reports=reports,
        )
        review_markdown = render_review_markdown(reports, summary)
        final = self.revision.run(draft, reports)

        outline_path = output_dir / "textbook_outline.json"
        outline_markdown_path = output_dir / "textbook_outline.md"
        evidence_chunks_path = output_dir / "evidence_chunks.jsonl"
        chapter_plan_path = output_dir / "chapter_plan.json"
        draft_path = output_dir / "textbook_draft.md"
        draft_docx_path = output_dir / "textbook_draft.docx"
        review_report_path = output_dir / "review_report.json"
        review_markdown_path = output_dir / "review_report.md"
        summary_path = output_dir / "workflow_summary.json"
        final_path = output_dir / "textbook_final.md"
        final_docx_path = output_dir / "textbook_final.docx"

        write_json(outline_path, outlines)
        write_text(outline_markdown_path, outline_markdown)
        write_jsonl(evidence_chunks_path, chunks)
        write_json(chapter_plan_path, plans)
        write_text(draft_path, draft)
        write_json(review_report_path, reports)
        write_text(review_markdown_path, review_markdown)
        write_json(summary_path, summary)
        write_text(final_path, final)
        markdown_to_docx(draft, draft_docx_path)
        markdown_to_docx(final, final_docx_path)

        return WorkflowOutputs(
            outline_path=str(outline_path),
            outline_markdown_path=str(outline_markdown_path),
            evidence_chunks_path=str(evidence_chunks_path),
            chapter_plan_path=str(chapter_plan_path),
            draft_path=str(draft_path),
            draft_docx_path=str(draft_docx_path),
            review_report_path=str(review_report_path),
            review_markdown_path=str(review_markdown_path),
            summary_path=str(summary_path),
            final_path=str(final_path),
            final_docx_path=str(final_docx_path),
        )
