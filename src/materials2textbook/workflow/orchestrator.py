from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from materials2textbook.llm.cache import CachingLLMProvider, LLMCacheStats
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, ReviewReport, WorkflowOutputs
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
        chapter_output_root: Path | None = None,
        max_chapters: int = 0,
        max_chapter_input_tokens: int = 12000,
        resume_chapters: bool = True,
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
        _progress("building outline")
        outlines = self.outline_planner.run(chunks)
        outlines = self.title_polisher.run_outlines(outlines, chunks)
        outline_markdown = render_outline_markdown(outlines, title)

        if book_mode:
            _progress(f"chapter plans ready: chapters={len(plans)}; running per-chapter production")
            chapter_pipeline = self._run_book_chapter_pipeline(
                plans=plans,
                chunks=chunks,
                title=title,
                config=config,
                chapter_output_root=chapter_output_root or output_dir / "chapter_runs",
                resume_chapters=resume_chapters,
                book_plan=book_plan,
            )
            plans = chapter_pipeline["plans"]
            book_plan = _filter_book_plan(book_plan, plans)
            chunks = chapter_pipeline["chunks"]
            draft = chapter_pipeline["draft_markdown"]
            current_markdown = chapter_pipeline["final_markdown"]
            final = current_markdown
            reports = chapter_pipeline["reports"]
            review_history = chapter_pipeline["review_history"]
            chapter_run_records = chapter_pipeline["chapter_runs"]
            writer_generation_mode = chapter_pipeline["writer_generation_mode"]
            writer_generation_warning = chapter_pipeline["writer_generation_warning"]
        else:
            _progress(f"chapter plans ready: chapters={len(plans)}")
            _progress("polishing titles")
            plans = self.title_polisher.run(plans, chunks)
            _progress("designing learning activities")
            plans = self.activity_designer.run(plans)
            _progress("designing teaching cases")
            plans = self.case_designer.run(plans, chunks)
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
            chapter_run_records = []
            writer_generation_mode = self.writer.last_generation_mode
            writer_generation_warning = self.writer.last_generation_warning

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
        if not book_mode:
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
        artifact_warnings = []
        artifact_warnings.extend(_try_markdown_to_docx(draft, draft_docx_path))
        artifact_warnings.extend(_try_markdown_to_docx(final, final_docx_path))
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
                "writer_generation_mode": writer_generation_mode,
                "writer_generation_warning": writer_generation_warning,
                "artifact_warnings": artifact_warnings,
                "chapter_pipeline_enabled": bool(book_mode),
                "chapter_pipeline_total": len(chapter_run_records),
                "chapter_pipeline_completed": sum(1 for item in chapter_run_records if item.get("status") in {"success", "reused"}),
                "chapter_pipeline_failed": sum(1 for item in chapter_run_records if item.get("status") == "failed"),
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
                "chapter_output_root": _portable_path(chapter_output_root or output_dir / "chapter_runs") if book_mode else "",
            },
            "chapter_runs": chapter_run_records,
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

    def _run_book_chapter_pipeline(
        self,
        *,
        plans: list[ChapterPlan],
        chunks: list[EvidenceChunk],
        title: str,
        config: WorkflowConfig,
        chapter_output_root: Path,
        resume_chapters: bool,
        book_plan: Any = None,
    ) -> dict[str, Any]:
        chapter_output_root.mkdir(parents=True, exist_ok=True)
        completed_plans: list[ChapterPlan] = []
        used_chunks: list[EvidenceChunk] = []
        draft_parts: list[str] = []
        final_parts: list[str] = []
        all_reports: list[ReviewReport] = []
        review_history: list[dict[str, Any]] = []
        chapter_runs: list[dict[str, Any]] = []

        for chapter_index, plan in enumerate(plans, start=1):
            chapter_dir = chapter_output_root / f"{chapter_index:02d}_{_safe_file_stem(plan.chapter_id or plan.title)}"
            chapter_dir.mkdir(parents=True, exist_ok=True)
            status_path = chapter_dir / "chapter_status.json"
            draft_path = chapter_dir / "textbook_draft.md"
            final_path = chapter_dir / "textbook_final.md"

            chapter_chunks = _chunks_for_plan(plan, chunks)
            if resume_chapters and _reusable_chapter(status_path, final_path):
                _progress(f"chapter {chapter_index}/{len(plans)}: reusing completed output for {plan.title}")
                prepared_plans = self._prepare_chapter_plans([plan], chapter_chunks)
                completed_plans.extend(prepared_plans)
                used_chunks.extend(chapter_chunks)
                draft_parts.append(_strip_markdown_title(draft_path.read_text(encoding="utf-8") if draft_path.exists() else final_path.read_text(encoding="utf-8")))
                final_parts.append(_strip_markdown_title(final_path.read_text(encoding="utf-8")))
                record = _read_status(status_path)
                record.update({"status": "reused", "chapter_dir": _portable_path(chapter_dir)})
                chapter_runs.append(record)
                continue

            try:
                _progress(f"chapter {chapter_index}/{len(plans)}: generating {plan.title}")
                with _temporary_chapter_llm_cache(self.writer.llm_provider, chapter_dir / "llm_cache.json") as chapter_cache:
                    chapter_token_limit = _chapter_token_budget(book_plan, plan.chapter_id) or config.max_input_tokens
                    budgeted_chunks, chapter_token_budget_report = apply_evidence_token_budget(
                        chapter_chunks,
                        max_input_tokens=chapter_token_limit,
                        max_tokens_per_evidence_chunk=config.normalized_max_tokens_per_evidence_chunk(),
                        summarize_over_budget=config.summarize_over_budget,
                        summary_token_reserve_ratio=config.normalized_summary_token_reserve_ratio(),
                        max_tokens_per_summary_chunk=config.normalized_max_tokens_per_summary_chunk(),
                        max_summary_source_chunks=config.normalized_max_summary_source_chunks(),
                        llm_provider=self.resource_analyst.llm_provider,
                        use_llm=self.resource_analyst.use_llm,
                    )
                    prepared_plans = self._prepare_chapter_plans([plan], budgeted_chunks)
                    chapter_title = prepared_plans[0].title if prepared_plans else plan.title
                    chapter_draft = self.writer.run(prepared_plans, budgeted_chunks, title=chapter_title)
                    chapter_current = chapter_draft
                    chapter_reports: list[ReviewReport] = []
                    chapter_review_history: list[dict[str, Any]] = []

                    for round_index in range(1, config.normalized_review_rounds() + 1):
                        _progress(f"chapter {chapter_index}: review round {round_index}")
                        fact_issues = self.evidence_reviewer.run(prepared_plans, budgeted_chunks, chapter_current)
                        pedagogy_issues = self.pedagogy_reviewer.run(prepared_plans, chapter_current)
                        chapter_reports = self.review_composer.run(prepared_plans, fact_issues, pedagogy_issues)
                        issue_count = sum(len(report.fact_issues) + len(report.pedagogy_issues) for report in chapter_reports)
                        chapter_review_history.append(
                            {
                                "chapter_id": plan.chapter_id,
                                "chapter_title": chapter_title,
                                "round": round_index,
                                "issue_count": issue_count,
                                "reports": chapter_reports,
                            }
                        )
                        if round_index < config.normalized_review_rounds() and issue_count:
                            chapter_current = self.revision.run(chapter_current, chapter_reports)
                        else:
                            break

                    chapter_final = self.revision.run(chapter_current, chapter_reports)
                    chapter_cache_stats = _llm_cache_record(chapter_cache)
                chapter_summary = build_workflow_summary(
                    title=chapter_title,
                    source_records=len(budgeted_chunks),
                    evidence_chunks=budgeted_chunks,
                    skipped_chunks=chapter_token_budget_report.uncovered_dropped_chunks,
                    plans=prepared_plans,
                    reports=chapter_reports,
                    draft_markdown=chapter_final,
                )

                write_jsonl(chapter_dir / "evidence_chunks.jsonl", budgeted_chunks)
                write_json(chapter_dir / "chapter_plan.json", prepared_plans)
                write_text(draft_path, chapter_draft)
                write_text(final_path, chapter_final)
                write_json(chapter_dir / "review_report.json", chapter_reports)
                write_text(chapter_dir / "review_report.md", render_review_markdown(chapter_reports, chapter_summary))
                write_json(chapter_dir / "review_history.json", chapter_review_history)
                write_json(chapter_dir / "workflow_summary.json", chapter_summary)
                write_json(chapter_dir / "token_budget_report.json", chapter_token_budget_report)
                chapter_artifact_warnings = []
                chapter_artifact_warnings.extend(_try_markdown_to_docx(chapter_draft, chapter_dir / "textbook_draft.docx"))
                chapter_artifact_warnings.extend(_try_markdown_to_docx(chapter_final, chapter_dir / "textbook_final.docx"))

                record = {
                    "status": "success",
                    "chapter_id": plan.chapter_id,
                    "chapter_title": chapter_title,
                    "chapter_dir": _portable_path(chapter_dir),
                    "evidence_chunks": len(budgeted_chunks),
                    "token_budget_max_input_tokens": chapter_token_budget_report.max_input_tokens,
                    "token_budget_kept_estimated_tokens": chapter_token_budget_report.kept_estimated_tokens,
                    "token_budget_dropped_chunks": chapter_token_budget_report.dropped_chunks,
                    "review_rounds_completed": len(chapter_review_history),
                    "writer_generation_mode": self.writer.last_generation_mode,
                    "writer_generation_warning": self.writer.last_generation_warning,
                    "artifact_warnings": chapter_artifact_warnings,
                    **chapter_cache_stats,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                write_json(status_path, record)

                completed_plans.extend(prepared_plans)
                used_chunks.extend(budgeted_chunks)
                draft_parts.append(_strip_markdown_title(chapter_draft))
                final_parts.append(_strip_markdown_title(chapter_final))
                all_reports.extend(chapter_reports)
                review_history.extend(chapter_review_history)
                chapter_runs.append(record)
            except Exception as exc:  # pragma: no cover - exact provider and file failures vary.
                record = {
                    "status": "failed",
                    "chapter_id": plan.chapter_id,
                    "chapter_title": plan.title,
                    "chapter_dir": _portable_path(chapter_dir),
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                write_json(status_path, record)
                chapter_runs.append(record)
                _progress(f"chapter {chapter_index}: failed; continuing with remaining chapters")

        if not completed_plans:
            raise RuntimeError(f"No chapters were generated successfully. See {chapter_output_root / '*/chapter_status.json'}")

        return {
            "plans": completed_plans,
            "chunks": _dedupe_chunks(used_chunks),
            "draft_markdown": _combine_chapter_markdown(title, draft_parts, label="草稿"),
            "final_markdown": _combine_chapter_markdown(title, final_parts, label="定稿"),
            "reports": all_reports,
            "review_history": review_history,
            "chapter_runs": chapter_runs,
            "writer_generation_mode": _aggregate_writer_modes(chapter_runs),
            "writer_generation_warning": _aggregate_writer_warnings(chapter_runs),
        }

    def _prepare_chapter_plans(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk]) -> list[ChapterPlan]:
        prepared = self.title_polisher.run(plans, chunks)
        prepared = self.activity_designer.run(prepared)
        return self.case_designer.run(prepared, chunks)


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _chunks_for_plan(plan: ChapterPlan, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    expected_ids: list[str] = []
    expected_ids.extend(plan.evidence_chunk_ids)
    for point in plan.knowledge_points:
        expected_ids.extend(point.chunk_ids)
    expected = {chunk_id for chunk_id in expected_ids if chunk_id}
    if not expected:
        return []
    return [chunk for chunk in chunks if chunk.chunk_id in expected]


def _dedupe_chunks(chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    seen: set[str] = set()
    result: list[EvidenceChunk] = []
    for chunk in chunks:
        key = chunk.chunk_id or f"{chunk.asset_id}:{chunk.title}:{len(result)}"
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


def _chapter_token_budget(book_plan: Any, chapter_id: str) -> int:
    if not book_plan:
        return 0
    for chapter in getattr(book_plan, "chapters", []) or []:
        if getattr(chapter, "chapter_id", "") == chapter_id:
            return int(getattr(chapter, "token_budget", 0) or 0)
    return 0


def _filter_book_plan(book_plan: Any, plans: list[ChapterPlan]) -> Any:
    if not book_plan:
        return book_plan
    completed_ids = {plan.chapter_id for plan in plans}
    chapters = [chapter for chapter in getattr(book_plan, "chapters", []) or [] if getattr(chapter, "chapter_id", "") in completed_ids]
    metadata = dict(getattr(book_plan, "metadata", {}) or {})
    metadata["planned_chapter_count"] = len(getattr(book_plan, "chapters", []) or [])
    metadata["generated_chapter_count"] = len(chapters)
    return replace(book_plan, chapters=chapters, metadata=metadata)


def _safe_file_stem(value: str) -> str:
    text = str(value or "chapter").strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or "chapter"


def _reusable_chapter(status_path: Path, final_path: Path) -> bool:
    if not status_path.exists() or not final_path.exists():
        return False
    status = _read_status(status_path)
    return status.get("status") in {"success", "reused"} and final_path.stat().st_size > 0


def _read_status(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _strip_markdown_title(markdown: str) -> str:
    lines = str(markdown or "").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _combine_chapter_markdown(title: str, chapter_parts: list[str], *, label: str) -> str:
    parts = [part.strip() for part in chapter_parts if part and part.strip()]
    lines = [
        f"# {title}",
        "",
        f"> 本文件由全书按章生产线汇总而成。每章独立完成写作、审核、修订和状态记录；当前为{label}汇总。",
        "",
    ]
    lines.extend("\n\n".join(parts).splitlines())
    return "\n".join(lines).rstrip() + "\n"


def _aggregate_writer_modes(chapter_runs: list[dict[str, Any]]) -> str:
    modes = [
        str(record.get("writer_generation_mode") or "")
        for record in chapter_runs
        if record.get("status") in {"success", "reused"} and record.get("writer_generation_mode")
    ]
    if not modes:
        return "unknown"
    unique = sorted(set(modes))
    if len(unique) == 1:
        return unique[0]
    return "mixed:" + ",".join(unique)


def _aggregate_writer_warnings(chapter_runs: list[dict[str, Any]]) -> str:
    warnings = [
        str(record.get("writer_generation_warning") or "").strip()
        for record in chapter_runs
        if str(record.get("writer_generation_warning") or "").strip()
    ]
    return " | ".join(dict.fromkeys(warnings[:5]))


def _try_markdown_to_docx(markdown: str, output_path: Path) -> list[str]:
    try:
        markdown_to_docx(markdown, output_path)
    except RuntimeError as exc:
        warning = f"Skipped Word export for {output_path.name}: {exc}"
        _progress(warning)
        return [warning]
    return []


@contextmanager
def _temporary_chapter_llm_cache(provider: Any, cache_path: Path):
    cache_provider = _find_cache_provider(provider)
    if cache_provider is None:
        yield None
        return

    original_path = cache_provider.cache_path
    original_cache = cache_provider._cache
    original_stats = cache_provider.stats
    cache_provider.cache_path = cache_path
    cache_provider.stats = LLMCacheStats()
    cache_provider._cache = cache_provider._load()
    try:
        yield cache_provider
    finally:
        cache_provider.cache_path = original_path
        cache_provider._cache = original_cache
        cache_provider.stats = original_stats


def _find_cache_provider(provider: Any) -> CachingLLMProvider | None:
    current = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CachingLLMProvider):
            return current
        current = getattr(current, "provider", None)
    return None


def _llm_cache_record(cache_provider: CachingLLMProvider | None) -> dict[str, Any]:
    if cache_provider is None:
        return {
            "llm_cache_path": "",
            "llm_cache_entries": 0,
            "llm_cache_hits": 0,
            "llm_cache_misses": 0,
        }
    return {
        "llm_cache_path": _portable_path(cache_provider.cache_path),
        "llm_cache_entries": len(cache_provider._cache),
        "llm_cache_hits": cache_provider.stats.hits,
        "llm_cache_misses": cache_provider.stats.misses,
    }
