from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from materials2textbook.agents.knowledge_organizer import KnowledgeOrganizerAgent
from materials2textbook.agents.outline_planner import OutlinePlannerAgent, render_outline_markdown
from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.agents.reviewers import EvidenceReviewerAgent, PedagogyReviewerAgent, ReviewComposer
from materials2textbook.agents.revision import RevisionAgent
from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.exporters.docx import markdown_to_docx
from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl, write_text
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.schemas import WorkflowOutputs
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.reporting import build_workflow_summary, render_evidence_markdown, render_review_markdown


class TextbookWorkflow:
    """Run the first multi-agent orchestration loop over processed material segments."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.resource_analyst = ResourceAnalystAgent()
        self.outline_planner = OutlinePlannerAgent()
        self.organizer = KnowledgeOrganizerAgent()
        self.writer = TextbookWriterAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.evidence_reviewer = EvidenceReviewerAgent()
        self.pedagogy_reviewer = PedagogyReviewerAgent()
        self.review_composer = ReviewComposer()
        self.revision = RevisionAgent(llm_provider=llm_provider, use_llm=use_llm)

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
        evidence_markdown = render_evidence_markdown(chunks, title)
        final = self.revision.run(draft, reports)

        outline_path = output_dir / "textbook_outline.json"
        outline_markdown_path = output_dir / "textbook_outline.md"
        evidence_chunks_path = output_dir / "evidence_chunks.jsonl"
        evidence_markdown_path = output_dir / "evidence_index.md"
        chapter_plan_path = output_dir / "chapter_plan.json"
        draft_path = output_dir / "textbook_draft.md"
        draft_docx_path = output_dir / "textbook_draft.docx"
        review_report_path = output_dir / "review_report.json"
        review_markdown_path = output_dir / "review_report.md"
        summary_path = output_dir / "workflow_summary.json"
        final_path = output_dir / "textbook_final.md"
        final_docx_path = output_dir / "textbook_final.docx"
        manifest_path = output_dir / "artifact_manifest.json"

        write_json(outline_path, outlines)
        write_text(outline_markdown_path, outline_markdown)
        write_jsonl(evidence_chunks_path, chunks)
        write_text(evidence_markdown_path, evidence_markdown)
        write_json(chapter_plan_path, plans)
        write_text(draft_path, draft)
        write_json(review_report_path, reports)
        write_text(review_markdown_path, review_markdown)
        write_json(summary_path, summary)
        write_text(final_path, final)
        markdown_to_docx(draft, draft_docx_path)
        markdown_to_docx(final, final_docx_path)

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "title": title,
            "input": {
                "video_segments_path": _portable_path(video_segments_path),
                "source_records": len(records),
            },
            "summary": {
                "evidence_chunks": summary.evidence_chunks,
                "skipped_chunks": summary.skipped_chunks,
                "chapters": summary.chapters,
                "knowledge_points": summary.knowledge_points,
                "fact_issue_count": summary.fact_issue_count,
                "pedagogy_issue_count": summary.pedagogy_issue_count,
                "review_status_counts": summary.review_status_counts,
                "material_block_counts": summary.material_block_counts,
            },
            "outputs": {
                "outline_json": _portable_path(outline_path),
                "outline_markdown": _portable_path(outline_markdown_path),
                "evidence_chunks": _portable_path(evidence_chunks_path),
                "evidence_index": _portable_path(evidence_markdown_path),
                "chapter_plan": _portable_path(chapter_plan_path),
                "draft_markdown": _portable_path(draft_path),
                "draft_docx": _portable_path(draft_docx_path),
                "review_report_json": _portable_path(review_report_path),
                "review_report_markdown": _portable_path(review_markdown_path),
                "workflow_summary": _portable_path(summary_path),
                "final_markdown": _portable_path(final_path),
                "final_docx": _portable_path(final_docx_path),
            },
        }
        write_json(manifest_path, manifest)

        return WorkflowOutputs(
            outline_path=str(outline_path),
            outline_markdown_path=str(outline_markdown_path),
            evidence_chunks_path=str(evidence_chunks_path),
            evidence_markdown_path=str(evidence_markdown_path),
            chapter_plan_path=str(chapter_plan_path),
            draft_path=str(draft_path),
            draft_docx_path=str(draft_docx_path),
            review_report_path=str(review_report_path),
            review_markdown_path=str(review_markdown_path),
            summary_path=str(summary_path),
            final_path=str(final_path),
            final_docx_path=str(final_docx_path),
            manifest_path=str(manifest_path),
        )


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)
