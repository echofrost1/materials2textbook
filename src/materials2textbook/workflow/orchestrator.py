from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from materials2textbook.agents.digital_book_reviewer import (
    DigitalBookReviewerAgent,
    render_digital_book_review_markdown,
)
from materials2textbook.agents.activity_designer import ActivityDesignerAgent
from materials2textbook.agents.book_planner import (
    BookPlannerAgent,
    book_plan_to_chapter_plans,
    render_curriculum_order_yaml,
    render_book_outline_markdown,
    render_book_plan_review_markdown,
    review_book_plan,
)
from materials2textbook.agents.case_designer import CaseDesignerAgent
from materials2textbook.agents.knowledge_organizer import KnowledgeOrganizerAgent
from materials2textbook.agents.outline_planner import OutlinePlannerAgent, render_outline_markdown
from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.agents.reviewers import EvidenceReviewerAgent, PedagogyReviewerAgent, ReviewComposer
from materials2textbook.agents.revision import RevisionAgent, render_revision_diff_markdown
from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.agents.title_polisher import TitlePolisherAgent
from materials2textbook.exporters.digital_book import export_digital_book
from materials2textbook.exporters.docx import markdown_to_docx
from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl, write_text
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.schemas import WorkflowOutputs
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.reporting import build_workflow_summary, render_evidence_markdown, render_review_markdown
from materials2textbook.workflow.token_budget import apply_evidence_token_budget


def _progress(message: str) -> None:
    print(f"[workflow] {message}", flush=True)


class TextbookWorkflow:
    """Run the first multi-agent orchestration loop over processed material segments."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.resource_analyst = ResourceAnalystAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.book_planner = BookPlannerAgent()
        self.outline_planner = OutlinePlannerAgent()
        self.organizer = KnowledgeOrganizerAgent()
        self.activity_designer = ActivityDesignerAgent()
        self.case_designer = CaseDesignerAgent()
        self.title_polisher = TitlePolisherAgent()
        self.writer = TextbookWriterAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.evidence_reviewer = EvidenceReviewerAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.pedagogy_reviewer = PedagogyReviewerAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.review_composer = ReviewComposer()
        self.revision = RevisionAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.digital_book_reviewer = DigitalBookReviewerAgent()

    def run(
        self,
        video_segments_path: Path,
        output_dir: Path,
        title: str,
        config: WorkflowConfig | None = None,
        document_segments_path: Path | None = None,
        book_mode: bool = False,
        manifest_xlsx: Path | None = None,
        book_plan_output: Path | None = None,
        max_chapters: int = 0,
        max_chapter_input_tokens: int = 12000,
    ) -> WorkflowOutputs:
        config = config or WorkflowConfig()
        _progress("reading selected video and document evidence")
        records = read_jsonl(video_segments_path)
        document_records = read_jsonl(document_segments_path) if document_segments_path else []

        _progress(f"analyzing resources: videos={len(records)}, documents={len(document_records)}")
        raw_chunks = self.resource_analyst.run_mixed(records, document_records)
        chunks = [
            chunk
            for chunk in raw_chunks
            if config.allows_review_status(chunk.review_status)
            and config.allows_teaching_value(chunk.score.teaching_value)
        ]
        filter_skipped_chunks = len(raw_chunks) - len(chunks)
        _progress(f"resource chunks ready: kept={len(chunks)}, skipped_by_filter={filter_skipped_chunks}")
        _progress("applying evidence token budget")
        chunks, token_budget_report = apply_evidence_token_budget(
            chunks,
            max_input_tokens=config.max_input_tokens,
            max_tokens_per_evidence_chunk=config.normalized_max_tokens_per_evidence_chunk(),
            summarize_over_budget=config.summarize_over_budget,
            summary_token_reserve_ratio=config.normalized_summary_token_reserve_ratio(),
            max_tokens_per_summary_chunk=config.normalized_max_tokens_per_summary_chunk(),
            max_summary_source_chunks=config.normalized_max_summary_source_chunks(),
            llm_provider=self.resource_analyst.llm_provider,
            use_llm=self.resource_analyst.use_llm,
        )
        book_plan = None
        book_plan_review = []
        if book_mode:
            _progress("planning whole-book chapter structure")
            book_plan = self.book_planner.run(
                title=title,
                chunks=chunks,
                manifest_xlsx=manifest_xlsx,
                max_chapters=max_chapters,
                chapter_token_budget=max_chapter_input_tokens,
            )
            book_plan_review = review_book_plan(book_plan, chunks)
            plans = book_plan_to_chapter_plans(book_plan, chunks)
        else:
            _progress("organizing selected evidence into chapter plans")
            plans = self.organizer.run(
                chunks,
                max_chunks_per_knowledge_point=config.max_chunks_per_knowledge_point,
            )
        _progress(f"chapter plans ready: chapters={len(plans)}")
        _progress("polishing titles")
        plans = self.title_polisher.run(plans, chunks)
        _progress("designing learning activities")
        plans = self.activity_designer.run(plans)
        _progress("designing teaching cases")
        plans = self.case_designer.run(plans, chunks)
        _progress("building outline")
        outlines = self.outline_planner.run(chunks)
        outlines = self.title_polisher.run_outlines(outlines, chunks)
        outline_markdown = render_outline_markdown(outlines, title)
        _progress("writing textbook draft")
        draft = self.writer.run(plans, chunks, title=title)

        current_markdown = draft
        review_history = []
        reports = []
        for round_index in range(1, config.normalized_review_rounds() + 1):
            _progress(f"review round {round_index}: checking evidence support")
            fact_issues = self.evidence_reviewer.run(plans, chunks, current_markdown)
            _progress(f"review round {round_index}: checking pedagogy")
            pedagogy_issues = self.pedagogy_reviewer.run(plans, current_markdown)
            reports = self.review_composer.run(plans, fact_issues, pedagogy_issues)
            issue_count = sum(len(report.fact_issues) + len(report.pedagogy_issues) for report in reports)
            review_history.append(
                {
                    "round": round_index,
                    "issue_count": issue_count,
                    "reports": reports,
                }
            )
            if round_index < config.normalized_review_rounds() and issue_count:
                _progress(f"review round {round_index}: revising draft, issues={issue_count}")
                current_markdown = self.revision.run(current_markdown, reports)
            else:
                break

        _progress("building workflow summary and final revision")
        summary = build_workflow_summary(
            title=title,
            source_records=len(records) + len(document_records),
            evidence_chunks=chunks,
            skipped_chunks=filter_skipped_chunks + token_budget_report.uncovered_dropped_chunks,
            plans=plans,
            reports=reports,
            draft_markdown=current_markdown,
        )
        review_markdown = render_review_markdown(reports, summary)
        evidence_markdown = render_evidence_markdown(chunks, title)
        final = self.revision.run(current_markdown, reports)
        revision_diff = render_revision_diff_markdown(
            title=title,
            draft_markdown=draft,
            final_markdown=final,
            reports=reports,
        )

        outline_path = output_dir / "textbook_outline.json"
        outline_markdown_path = output_dir / "textbook_outline.md"
        book_plan_path = book_plan_output or output_dir / "book_plan.json"
        book_outline_path = output_dir / "book_outline.md"
        curriculum_order_path = output_dir / "curriculum_order.generated.yml"
        book_plan_review_path = output_dir / "book_plan_review.json"
        book_plan_review_markdown_path = output_dir / "book_plan_review.md"
        evidence_chunks_path = output_dir / "evidence_chunks.jsonl"
        evidence_markdown_path = output_dir / "evidence_index.md"
        chapter_plan_path = output_dir / "chapter_plan.json"
        draft_path = output_dir / "textbook_draft.md"
        draft_docx_path = output_dir / "textbook_draft.docx"
        review_report_path = output_dir / "review_report.json"
        review_markdown_path = output_dir / "review_report.md"
        review_history_path = output_dir / "review_history.json"
        revision_diff_path = output_dir / "revision_diff.md"
        summary_path = output_dir / "workflow_summary.json"
        final_path = output_dir / "textbook_final.md"
        final_docx_path = output_dir / "textbook_final.docx"
        manifest_path = output_dir / "artifact_manifest.json"
        digital_book_dir = output_dir.parent / "digital_book"

        _progress("writing workflow artifacts")
        write_json(outline_path, outlines)
        write_text(outline_markdown_path, outline_markdown)
        if book_plan:
            write_json(book_plan_path, book_plan)
            write_text(book_outline_path, render_book_outline_markdown(book_plan))
            write_text(curriculum_order_path, render_curriculum_order_yaml(book_plan))
            write_json(book_plan_review_path, book_plan_review)
            write_text(book_plan_review_markdown_path, render_book_plan_review_markdown(title, book_plan_review))
        write_jsonl(evidence_chunks_path, chunks)
        write_text(evidence_markdown_path, evidence_markdown)
        write_json(chapter_plan_path, plans)
        write_text(draft_path, draft)
        write_json(review_report_path, reports)
        write_text(review_markdown_path, review_markdown)
        write_json(review_history_path, review_history)
        write_text(revision_diff_path, revision_diff)
        write_json(summary_path, summary)
        write_text(final_path, final)
        markdown_to_docx(draft, draft_docx_path)
        markdown_to_docx(final, final_docx_path)
        _progress("exporting digital book")
        _digital_book, digital_book_path, digital_book_index_path = export_digital_book(
            title=title,
            plans=plans,
            chunks=chunks,
            output_dir=digital_book_dir,
            copy_media_assets=config.copy_media_assets,
            llm_provider=self.writer.llm_provider,
            use_llm=self.writer.use_llm,
            book_plan=book_plan,
        )
        _progress("reviewing exported digital book")
        digital_book_review = self.digital_book_reviewer.run(
            _digital_book,
            {chunk.chunk_id for chunk in chunks},
            digital_book_dir,
        )
        digital_book_review_path = digital_book_dir / "digital_book_review.json"
        digital_book_review_markdown_path = digital_book_dir / "digital_book_review.md"
        write_json(digital_book_review_path, digital_book_review)
        write_text(
            digital_book_review_markdown_path,
            render_digital_book_review_markdown(title, digital_book_review),
        )

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "title": title,
            "input": {
                "video_segments_path": _portable_path(video_segments_path),
                "document_segments_path": _portable_path(document_segments_path) if document_segments_path else "",
                "manifest_xlsx": _portable_path(manifest_xlsx) if manifest_xlsx else "",
                "source_records": len(records) + len(document_records),
                "video_source_records": len(records),
                "document_source_records": len(document_records),
            },
            "summary": {
                "evidence_chunks": summary.evidence_chunks,
                "skipped_chunks": summary.skipped_chunks,
                "token_budget_enabled": token_budget_report.enabled,
                "token_budget_max_input_tokens": token_budget_report.max_input_tokens,
                "token_budget_original_estimated_tokens": token_budget_report.original_estimated_tokens,
                "token_budget_kept_estimated_tokens": token_budget_report.kept_estimated_tokens,
                "token_budget_kept_source_chunks": token_budget_report.kept_source_chunks,
                "token_budget_truncated_chunks": token_budget_report.truncated_chunks,
                "token_budget_dropped_chunks": token_budget_report.dropped_chunks,
                "token_budget_summary_chunks": token_budget_report.summary_chunks,
                "token_budget_summarized_source_chunks": token_budget_report.summarized_source_chunks,
                "token_budget_uncovered_dropped_chunks": token_budget_report.uncovered_dropped_chunks,
                "chapters": summary.chapters,
                "knowledge_points": summary.knowledge_points,
                "fact_issue_count": summary.fact_issue_count,
                "pedagogy_issue_count": summary.pedagogy_issue_count,
                "review_rounds_requested": config.normalized_review_rounds(),
                "review_rounds_completed": len(review_history),
                "evidence_coverage_rate": summary.evidence_coverage_rate,
                "citation_coverage_rate": summary.citation_coverage_rate,
                "paragraph_support_rate": summary.paragraph_support_rate,
                "claim_support_rate": summary.claim_support_rate,
                "approved_evidence_rate": summary.approved_evidence_rate,
                "pedagogy_completeness_rate": summary.pedagogy_completeness_rate,
                "activity_quality_rate": summary.activity_quality_rate,
                "case_quality_rate": summary.case_quality_rate,
                "overall_quality_score": summary.overall_quality_score,
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
                "review_history": _portable_path(review_history_path),
                "revision_diff": _portable_path(revision_diff_path),
                "workflow_summary": _portable_path(summary_path),
                "final_markdown": _portable_path(final_path),
                "final_docx": _portable_path(final_docx_path),
                "digital_book_json": _portable_path(digital_book_path),
                "digital_book_index": _portable_path(digital_book_index_path),
                "digital_book_review_json": _portable_path(digital_book_review_path),
                "digital_book_review_markdown": _portable_path(digital_book_review_markdown_path),
                "book_plan": _portable_path(book_plan_path) if book_plan else "",
                "book_outline": _portable_path(book_outline_path) if book_plan else "",
                "curriculum_order": _portable_path(curriculum_order_path) if book_plan else "",
                "book_plan_review_json": _portable_path(book_plan_review_path) if book_plan else "",
                "book_plan_review_markdown": _portable_path(book_plan_review_markdown_path) if book_plan else "",
            },
        }
        write_json(manifest_path, manifest)

        _progress("workflow complete")
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
            review_history_path=str(review_history_path),
            revision_diff_path=str(revision_diff_path),
            summary_path=str(summary_path),
            final_path=str(final_path),
            final_docx_path=str(final_docx_path),
            manifest_path=str(manifest_path),
            digital_book_dir=str(digital_book_dir),
            digital_book_path=str(digital_book_path),
            digital_book_index_path=str(digital_book_index_path),
            digital_book_review_path=str(digital_book_review_path),
            digital_book_review_markdown_path=str(digital_book_review_markdown_path),
        )


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)
