from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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
from materials2textbook.agents.book_plan_llm import (
    BookPlanLLMAgent,
    book_plan_from_dict,
    enrich_chapter_evidence,
    enforce_material_block_coverage,
    enforce_minimum_sections,
    expand_tasks_by_material_density,
    plan_has_blocking_issues,
)
from materials2textbook.agents.case_designer import CaseDesignerAgent
from materials2textbook.agents.knowledge_organizer import KnowledgeOrganizerAgent
from materials2textbook.agents.outline_planner import OutlinePlannerAgent, render_outline_markdown
from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.agents.reviewers import EvidenceReviewerAgent, PedagogyReviewerAgent, ReviewComposer
from materials2textbook.agents.revision import RevisionAgent, render_revision_diff_markdown
from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.agents.title_polisher import TitlePolisherAgent
from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.exporters.digital_book import export_digital_book
from materials2textbook.exporters.docx import markdown_to_docx
from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl, write_text, to_jsonable
from materials2textbook.llm.cache import CachingLLMProvider, LLMCacheStats
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.knowledge_map.rendered_conformance import (
    extract_rendered_occurrences,
    render_conformance_report_markdown,
    RenderedOccurrence,
    wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.planning_evidence_gate import (
    apply_planning_evidence_gate,
    resolve_evidence_coverage_from_payload,
    render_planning_evidence_gate_markdown,
)
from materials2textbook.knowledge_map.outline import (
    book_plan_deep_equal,
    book_plan_fingerprint,
    book_plan_snapshot_payload,
    outline_signature,
    snapshot_source_book_plan,
)
from materials2textbook.knowledge_map.semantic_book_conformance import (
    build_semantic_book_conformance_report,
    render_semantic_book_conformance_markdown,
)
from materials2textbook.knowledge_map.publication_quality import (
    evaluate_publication_quality,
    integrate_rendered_claim_semantic_audit,
    write_publication_quality_artifacts,
)
from materials2textbook.knowledge_map.materialization import (
    fingerprint_semantic_objects,
    materialize_full_book,
    write_materialized_book_artifacts,
)
from materials2textbook.knowledge_map.pipeline import (
    analyze_book_knowledge,
    write_knowledge_map_artifacts,
    write_semantic_evaluation_artifacts,
)
from materials2textbook.knowledge_map.semantic import HeuristicSemanticPlanner
from materials2textbook.knowledge_map.semantic_evaluation import evaluate_semantic_planning
from materials2textbook.agents.knowledge_semantic_planner import LLMSemanticPlanningAgent
from materials2textbook.knowledge_map.execution import execute_verified_occurrences
from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (
    CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
    OpenAICompatibleEntailmentJudge,
    audit_rendered_claims,
    write_audit_artifacts,
)
from materials2textbook.knowledge_map.downstream_closure import (
    analyze_downstream_closure,
    render_downstream_closure_markdown,
)
from materials2textbook.knowledge_map.shared_facts import (
    audit_shared_fact_proposals,
    recall_shared_fact_candidates,
    render_shared_fact_audit_markdown,
)
from materials2textbook.knowledge_map.shared_fact_compression import (
    build_shared_fact_compression_plans,
    render_shared_fact_compression_markdown,
)
from materials2textbook.knowledge_map.shared_fact_materialization import (
    materialize_compressible_shared_fact,
    render_shared_fact_materialization_markdown,
    skipped_shared_fact_materialization,
)
from materials2textbook.knowledge_map.section_discourse import (
    build_section_discourse_bodies,
    complete_section_discourse_audits,
)
from materials2textbook.knowledge_map.writing_briefs import (
    FallbackOccurrence,
    OccurrenceWritingBrief,
    briefs_for_chapter,
    build_writing_brief_coverage_from_payload,
    fallbacks_for_chapter,
)
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, ReviewIssue, ReviewReport, WorkflowOutputs
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.reporting import build_workflow_summary, render_evidence_markdown, render_review_markdown
from materials2textbook.workflow.token_budget import TokenBudgetReport, apply_evidence_token_budget, estimate_chunks_tokens


def _progress(message: str) -> None:
    print(f"[workflow] {message}", flush=True)


class _DeterministicSemanticPlanningAgent:
    """Adapter for semantic mode when no model provider is configured."""

    def __init__(self) -> None:
        self.call_counts = {"identity": 0, "semantic_delta": 0}
        self._occurrences: dict[str, Any] = {}

    def judge_identity(self, candidates: list[dict]) -> dict:
        self.call_counts["identity"] += 1
        return {"judgements": []}

    def plan_semantic_deltas(self, trajectory: dict) -> dict:
        self.call_counts["semantic_delta"] += 1
        deltas: list[dict[str, Any]] = []
        for item in trajectory.get("occurrences", []):
            occurrence = self._occurrences.get(str(item.get("occurrence_id")))
            if occurrence is None:
                continue
            deltas.append(
                {
                    "occurrence_id": occurrence.occurrence_id,
                    "repeats_prior_explanation": bool(occurrence.repeats_prior_explanation),
                    "uses_prior_knowledge": bool(occurrence.uses_prior_knowledge),
                    "recall_needed": bool(occurrence.recall_needed),
                    "required_self_facets": list(occurrence.required_self_facets),
                    "required_self_extension_keys": list(occurrence.required_self_extension_keys),
                    "cross_prerequisite_uses": [],
                    "new_facets": list(occurrence.intended_grants),
                    "new_extension_keys": list(occurrence.intended_extension_keys),
                    "new_context": occurrence.new_context,
                    "repeated_aspects": list(occurrence.repeated_aspects),
                    "contribution_summary": occurrence.intended_contribution,
                    "confidence": max(float(occurrence.planning_confidence or 0.0), 0.82),
                    "rationale": "Deterministic adapter preserved the existing read-only occurrence proposal.",
                    "evidence_ids": list(occurrence.source_chunk_ids),
                    "orientation_only": occurrence.role == "INTRO",
                    "restores_prior_context": occurrence.role == "RECALL",
                    "repeats_complete_teaching": bool(occurrence.repeats_prior_explanation and not occurrence.intended_grants),
                }
            )
        return {"deltas": deltas}


class TextbookWorkflow:
    """Run the first multi-agent orchestration loop over processed material segments."""

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        use_llm: bool = False,
        domain_config: DomainConfig | None = None,
        auto_plan: bool = True,
        llm_book_planning: bool = True,
    ) -> None:
        self.domain_config = domain_config or default_domain_config()
        self.auto_plan = auto_plan
        self.llm_book_planning = llm_book_planning
        self.resource_analyst = ResourceAnalystAgent(llm_provider=llm_provider, use_llm=use_llm)
        self.book_planner = BookPlannerAgent(domain_config=self.domain_config)
        self.book_plan_llm = BookPlanLLMAgent(
            llm_provider=llm_provider,
            use_llm=use_llm and llm_book_planning,
        )
        self.outline_planner = OutlinePlannerAgent()
        self.organizer = KnowledgeOrganizerAgent()
        self.activity_designer = ActivityDesignerAgent()
        self.case_designer = CaseDesignerAgent()
        self.title_polisher = TitlePolisherAgent()
        self.writer = TextbookWriterAgent(llm_provider=llm_provider, use_llm=use_llm, domain_config=self.domain_config)
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
        domain_config: DomainConfig | None = None,
        auto_plan: bool | None = None,
        llm_book_planning: bool | None = None,
        book_plan_input: Path | None = None,
        occurrence_writing_briefs: list[OccurrenceWritingBrief] | None = None,
        fallback_occurrences: list[FallbackOccurrence] | None = None,
        semantic_evaluation_input: Path | None = None,
        semantic_book_mode: bool = False,
        book_plan_is_frozen: bool = False,
        shared_fact_proposals: list[dict[str, Any]] | None = None,
        shared_fact_materialization_request: Mapping[str, Any] | None = None,
    ) -> WorkflowOutputs:
        config = config or WorkflowConfig()
        domain_config = domain_config or self.domain_config
        auto_plan = self.auto_plan if auto_plan is None else auto_plan
        llm_book_planning = self.llm_book_planning if llm_book_planning is None else llm_book_planning
        if book_plan_is_frozen and book_plan_input is None:
            raise ValueError("book_plan_is_frozen requires book_plan_input; generated plans freeze only after normal post-processing.")
        semantic_book_mode = bool(semantic_book_mode or semantic_evaluation_input)
        # Semantic production execution is defined only for a frozen
        # whole-book plan; callers need not duplicate the book-mode flag.
        book_mode = bool(book_mode or semantic_book_mode)
        semantic_runtime_mode = semantic_book_mode and semantic_evaluation_input is None
        semantic_execution_mode = (
            "verified_sequential"
            if semantic_runtime_mode
            else "external_payload_replay"
            if semantic_evaluation_input
            else "disabled"
        )
        semantic_planner_mode = "external_payload" if semantic_evaluation_input else "disabled"
        semantic_outline_signature = ""
        semantic_evaluation_payload: dict[str, Any] | None = None
        if semantic_evaluation_input:
            payload = json.loads(Path(semantic_evaluation_input).read_text(encoding="utf-8"))
            semantic_evaluation_payload = payload
            coverage = build_writing_brief_coverage_from_payload(payload)
            semantic_outline_signature = str((payload.get("knowledge_map") or {}).get("outline_signature") or "")
            occurrence_writing_briefs = coverage.briefs
            fallback_occurrences = coverage.fallback_occurrences
        else:
            from materials2textbook.knowledge_map.writing_briefs import WritingBriefCoverage
            coverage = WritingBriefCoverage(
                briefs=list(occurrence_writing_briefs or []),
                fallback_occurrences=list(fallback_occurrences or []),
            )
        briefed_writer_mode = bool(coverage.briefs or coverage.fallback_occurrences)
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
        if book_mode:
            _progress("book mode: keeping full evidence pool for planning; applying token budgets per chapter")
            estimated_tokens = estimate_chunks_tokens(chunks)
            token_budget_report = TokenBudgetReport(
                enabled=False,
                max_input_tokens=0,
                max_tokens_per_evidence_chunk=config.normalized_max_tokens_per_evidence_chunk(),
                original_chunks=len(chunks),
                kept_chunks=len(chunks),
                kept_source_chunks=len(chunks),
                dropped_chunks=0,
                original_estimated_tokens=estimated_tokens,
                kept_estimated_tokens=estimated_tokens,
                truncated_chunks=0,
            )
        else:
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
        if semantic_evaluation_payload is not None:
            coverage_resolution = resolve_evidence_coverage_from_payload(
                payload=semantic_evaluation_payload,
                chunks=chunks,
            )
            semantic_evaluation_payload = coverage_resolution.contracted_payload
            gate_report = coverage_resolution.report
            write_json(
                output_dir / "evidence_coverage_resolution.json",
                {
                    "contractions": [asdict(item) for item in coverage_resolution.contractions],
                    "dropped_occurrence_ids": list(coverage_resolution.dropped_occurrence_ids),
                    "final_report": asdict(gate_report),
                },
            )
            write_text(output_dir / "planning_evidence_gate.md", render_planning_evidence_gate_markdown(gate_report))
            coverage = build_writing_brief_coverage_from_payload(semantic_evaluation_payload)
            coverage = apply_planning_evidence_gate(coverage=coverage, report=gate_report)
            occurrence_writing_briefs = coverage.briefs
            fallback_occurrences = coverage.fallback_occurrences
            briefed_writer_mode = bool(coverage.briefs or coverage.fallback_occurrences)
            if coverage.rejected_plan_occurrences:
                rejected_ids = ", ".join(item.occurrence_id for item in coverage.rejected_plan_occurrences)
                raise ValueError(
                    "Planning Evidence Gate rejected unsupported semantic plan(s); manual review is required before writing: "
                    + rejected_ids
                )
        book_plan = None
        semantic_evaluation = None
        source_book_plan_snapshot = None
        source_outline_signature = ""
        source_book_plan_fingerprint = ""
        source_book_plan_snapshot_path = output_dir / "source_book_plan_snapshot.json"
        source_book_plan_invariant_path = output_dir / "source_book_plan_invariant.json"
        book_plan_review = []
        planning_mode = "chapter"
        if book_mode:
            _progress("planning whole-book chapter structure")
            auto_plan_issues = []
            if book_plan_input:
                payload = json.loads(Path(book_plan_input).read_text(encoding="utf-8"))
                book_plan = book_plan_from_dict(
                    payload,
                    title=title,
                    chapter_token_budget=max_chapter_input_tokens,
                )
                planning_mode = "external"
            elif auto_plan and llm_book_planning:
                candidate_plan, auto_plan_issues = self.book_plan_llm.run(
                    title=title,
                    chunks=chunks,
                    domain_config=domain_config,
                    max_chapters=max_chapters,
                    chapter_token_budget=max_chapter_input_tokens,
                )
                if candidate_plan is not None and not plan_has_blocking_issues(auto_plan_issues):
                    book_plan = candidate_plan
                    planning_mode = "llm"
            if book_plan is None:
                book_plan = self.book_planner.run(
                    title=title,
                    chunks=chunks,
                    manifest_xlsx=manifest_xlsx,
                    max_chapters=max_chapters,
                    chapter_token_budget=max_chapter_input_tokens,
                    domain_config=domain_config,
                )
                planning_mode = "rule_fallback" if auto_plan_issues else "rule"
            # A frozen input is already the result of the original planner and
            # its post-processing.  It is a read-only A/B experiment input:
            # do not re-run structural normalization, evidence enrichment, or
            # execution metadata updates on either leg.
            if book_plan_is_frozen:
                section_issues = []
                coverage_issues = []
                density_issues = []
                structure_issues = []
            else:
                # Complete the original planning/post-processing sequence
                # before semantic mode freezes the BookPlan source of truth.
                book_plan, section_issues = enforce_minimum_sections(book_plan, chunks)
                book_plan, coverage_issues = enforce_material_block_coverage(
                    book_plan,
                    chunks,
                    max_chapters=max_chapters,
                    chapter_token_budget=max_chapter_input_tokens,
                )
                book_plan, density_issues = expand_tasks_by_material_density(book_plan, chunks)
                book_plan = enrich_chapter_evidence(book_plan, chunks)
                structure_issues = []
                metadata = dict(book_plan.metadata)
                metadata.update(
                    {
                        "planning_mode": planning_mode,
                        "domain_config": domain_config.to_dict(),
                        "auto_plan": bool(auto_plan),
                        "llm_book_planning": bool(llm_book_planning),
                    }
                )
                book_plan = replace(book_plan, metadata=metadata)
            book_plan_review = auto_plan_issues + section_issues + coverage_issues + density_issues + structure_issues + review_book_plan(book_plan, chunks)
            if semantic_book_mode:
                source_book_plan_snapshot = snapshot_source_book_plan(book_plan)
                source_outline_signature = outline_signature(source_book_plan_snapshot)
                source_book_plan_fingerprint = book_plan_fingerprint(source_book_plan_snapshot)
                write_json(source_book_plan_snapshot_path, book_plan_snapshot_payload(source_book_plan_snapshot))
                current_signature = outline_signature(book_plan)
                if semantic_outline_signature and current_signature != semantic_outline_signature:
                    raise ValueError(
                        "Semantic evaluation outline signature does not match the supplied fixed BookPlan; "
                        "re-run semantic planning for this outline before rendering."
                    )
            plans = book_plan_to_chapter_plans(book_plan, chunks)
            if semantic_runtime_mode:
                _progress("semantic mode: analyzing the frozen BookPlan")
                knowledge_map = analyze_book_knowledge(
                    book_plan=book_plan,
                    chunks=chunks,
                    semantic_planner=HeuristicSemanticPlanner(),
                )
                if self.writer.use_llm and self.writer.llm_provider is not None:
                    semantic_agent = LLMSemanticPlanningAgent(self.writer.llm_provider)
                    semantic_planner_mode = "llm"
                else:
                    semantic_agent = _DeterministicSemanticPlanningAgent()
                    semantic_planner_mode = "deterministic"
                    semantic_agent._occurrences = {
                        item.occurrence_id: item for item in knowledge_map.planned_occurrences
                    }
                semantic_evaluation = evaluate_semantic_planning(
                    knowledge_map=knowledge_map,
                    chunks=chunks,
                    agent=semantic_agent,
                )
                write_knowledge_map_artifacts(semantic_evaluation.knowledge_map, output_dir)
                write_semantic_evaluation_artifacts(semantic_evaluation, output_dir)
                semantic_evaluation_payload = {
                    "knowledge_map": to_jsonable(semantic_evaluation.knowledge_map),
                    "semantic_deltas": to_jsonable(semantic_evaluation.semantic_deltas),
                    "identity_judgements": semantic_evaluation.identity_judgements,
                    "rejected_proposals": semantic_evaluation.rejected_proposals,
                    "normalizations": semantic_evaluation.normalizations,
                    "prerequisite_audit": semantic_evaluation.prerequisite_audit,
                    "call_counts": semantic_evaluation.call_counts,
                }
                resolution = resolve_evidence_coverage_from_payload(
                    payload=semantic_evaluation_payload,
                    chunks=chunks,
                )
                semantic_evaluation_payload = resolution.contracted_payload
                self._synchronize_semantic_evaluation_with_payload(
                    semantic_evaluation,
                    semantic_evaluation_payload,
                )
                gate_report = resolution.report
                write_json(
                    output_dir / "evidence_coverage_resolution.json",
                    {
                        "contractions": [asdict(item) for item in resolution.contractions],
                        "dropped_occurrence_ids": list(resolution.dropped_occurrence_ids),
                        "final_report": asdict(gate_report),
                    },
                )
                write_text(output_dir / "planning_evidence_gate.md", render_planning_evidence_gate_markdown(gate_report))
                coverage = apply_planning_evidence_gate(
                    coverage=build_writing_brief_coverage_from_payload(semantic_evaluation_payload),
                    report=gate_report,
                )
                occurrence_writing_briefs = coverage.briefs
                fallback_occurrences = coverage.fallback_occurrences
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

        semantic_execution = None
        if book_mode:
            if semantic_runtime_mode and semantic_evaluation is not None:
                _progress(f"semantic mode: sequential verified occurrence execution ({len(plans)} chapter projections)")
                semantic_execution = self._run_semantic_runtime_execution(
                    plans=plans,
                    chunks=chunks,
                    title=title,
                    book_plan=book_plan,
                    semantic_evaluation=semantic_evaluation,
                    excluded_occurrence_ids={
                        item.occurrence_id for item in coverage.rejected_plan_occurrences
                    } | {
                        item.occurrence_id for item in coverage.dropped_occurrence_goals
                    },
                )
                runtime_coverage = semantic_execution.coverage
                runtime_coverage.rejected_plan_occurrences.extend(coverage.rejected_plan_occurrences)
                runtime_coverage.dropped_occurrence_goals.extend(coverage.dropped_occurrence_goals)
                coverage = runtime_coverage
                occurrence_writing_briefs = coverage.briefs
                fallback_occurrences = coverage.fallback_occurrences
                draft = self._render_semantic_execution_markdown(title, plans, semantic_execution, book_plan=book_plan)
                current_markdown = draft
                final = current_markdown
                reports = []
                review_history = []
                chapter_run_records = []
                writer_generation_mode = "sequential_verified_occurrence"
                writer_generation_warning = ""
            else:
                _progress(f"chapter plans ready: chapters={len(plans)}; running per-chapter production")
                chapter_pipeline = self._run_book_chapter_pipeline(
                    plans=plans,
                    chunks=chunks,
                    title=title,
                    config=config,
                    chapter_output_root=chapter_output_root or output_dir / "chapter_runs",
                    resume_chapters=resume_chapters,
                    book_plan=book_plan,
                    occurrence_writing_briefs=occurrence_writing_briefs or [],
                    fallback_occurrences=fallback_occurrences or [],
                    semantic_book_mode=semantic_book_mode,
                    book_plan_is_frozen=book_plan_is_frozen,
                )
                plans = chapter_pipeline["plans"]
                if not semantic_book_mode and not book_plan_is_frozen:
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
                if semantic_book_mode and source_book_plan_snapshot is not None and not book_plan_deep_equal(book_plan, source_book_plan_snapshot):
                    raise RuntimeError("Semantic book production mutated the fixed BookPlan before export.")
        else:
            _progress(f"chapter plans ready: chapters={len(plans)}")
            _progress("polishing titles")
            plans = self.title_polisher.run(plans, chunks)
            _progress("designing learning activities")
            plans = self.activity_designer.run(plans)
            _progress("designing teaching cases")
            plans = self.case_designer.run(plans, chunks)
            _progress("writing textbook draft")
            draft = self.writer.run(
                plans,
                chunks,
                title=title,
                occurrence_writing_briefs=occurrence_writing_briefs or [],
                fallback_occurrences=fallback_occurrences or [],
            )

            current_markdown = draft
            review_history = []
            reports = []
            # Phase 2A.5 inspects the first render.  Existing revision agents
            # must not alter that body before conformance is recorded.
            review_round_limit = 0 if briefed_writer_mode else config.normalized_review_rounds()
            for round_index in range(1, review_round_limit + 1):
                _progress(f"review round {round_index}: checking evidence support")
                review_warnings: list[str] = []
                try:
                    fact_issues = self.evidence_reviewer.run(plans, chunks, current_markdown)
                except Exception as exc:
                    fact_issues = _reviewer_failure_issues(plans, "evidence", exc)
                    review_warnings.append(f"evidence reviewer failed; kept draft: {type(exc).__name__}: {exc}")
                _progress(f"review round {round_index}: checking pedagogy")
                try:
                    pedagogy_issues = self.pedagogy_reviewer.run(plans, current_markdown)
                except Exception as exc:
                    pedagogy_issues = _reviewer_failure_issues(plans, "pedagogy", exc)
                    review_warnings.append(f"pedagogy reviewer failed; kept draft: {type(exc).__name__}: {exc}")
                reports = self.review_composer.run(plans, fact_issues, pedagogy_issues)
                issue_count = sum(len(report.fact_issues) + len(report.pedagogy_issues) for report in reports)
                review_history.append(
                    {
                        "round": round_index,
                        "issue_count": issue_count,
                        "warnings": review_warnings,
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
        if not book_mode and not briefed_writer_mode:
            final = self.revision.run(current_markdown, reports)
        elif not book_mode:
            final = current_markdown
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
        conformance_report_path = output_dir / "rendered_conformance.json"
        conformance_markdown_path = output_dir / "rendered_conformance.md"
        knowledge_map_path = output_dir / "knowledge_map.json"
        learning_trajectory_report_path = output_dir / "learning_trajectory_report.md"
        canonical_mapping_audit_path = output_dir / "canonical_mapping_audit.md"
        semantic_planning_evaluation_path = output_dir / "semantic_planning_evaluation.json"
        semantic_learning_trajectory_report_path = output_dir / "semantic_learning_trajectory_report.md"
        evidence_coverage_resolution_path = output_dir / "evidence_coverage_resolution.json"
        planning_evidence_gate_path = output_dir / "planning_evidence_gate.md"
        rendered_claim_evidence_audit_path = output_dir / "rendered_claim_evidence_audit.json"
        rendered_claim_evidence_audit_markdown_path = output_dir / "rendered_claim_evidence_audit.md"
        semantic_book_conformance_path = output_dir / "semantic_book_conformance.json"
        semantic_book_conformance_markdown_path = output_dir / "semantic_book_conformance.md"
        downstream_closure_path = output_dir / "downstream_teaching_support_closure.json"
        downstream_closure_markdown_path = output_dir / "downstream_teaching_support_closure.md"
        shared_fact_proposals_path = output_dir / "shared_fact_proposals.json"
        shared_fact_proposals_markdown_path = output_dir / "shared_fact_proposals.md"
        shared_fact_compression_plans_path = output_dir / "shared_fact_compression_plans.json"
        shared_fact_compression_plans_markdown_path = output_dir / "shared_fact_compression_plans.md"
        shared_fact_materialization_path = output_dir / "shared_fact_materialization.json"
        shared_fact_materialization_markdown_path = output_dir / "shared_fact_materialization.md"
        publication_quality_dir = output_dir / "publication_quality"
        publication_quality_path = publication_quality_dir / "publication_quality.json"
        publication_quality_markdown_path = publication_quality_dir / "publication_quality.md"
        repair_history_audit_path = publication_quality_dir / "repair_history.json"
        repair_history_markdown_path = publication_quality_dir / "repair_history.md"
        materialization_dir = output_dir / "materialization"
        materialization_audit_path = materialization_dir / "full_book_materialization.json"
        materialization_markdown_path = materialization_dir / "full_book_materialization.md"
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
        if semantic_book_mode and source_book_plan_snapshot is not None:
            write_json(source_book_plan_snapshot_path, book_plan_snapshot_payload(source_book_plan_snapshot))
        semantic_execution_path = output_dir / "semantic_execution_audit.json"
        if semantic_execution is not None:
            write_json(semantic_execution_path, semantic_execution.to_dict())
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
        if not book_mode and self.writer.last_conformance_report:
            write_json(conformance_report_path, self.writer.last_conformance_report.to_dict())
            write_text(conformance_markdown_path, render_conformance_report_markdown(self.writer.last_conformance_report))
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
            domain_config=domain_config,
            occurrence_writing_briefs=coverage.briefs,
            fallback_occurrences=coverage.fallback_occurrences,
            dropped_occurrence_goals=coverage.dropped_occurrence_goals,
            zero_render_occurrences=coverage.zero_render_occurrences,
            rendered_occurrence_bodies={
                item.occurrence_id: item.markdown
                for item in extract_rendered_occurrences(final)
            },
            section_assemblies=(semantic_execution.section_assemblies if semantic_execution is not None else []),
            semantic_book_mode=semantic_book_mode,
        )
        downstream_closure_report = None
        shared_fact_report = None
        shared_fact_compression_report = None
        shared_fact_materialization_result = None
        if semantic_book_mode and semantic_execution is not None:
            if semantic_evaluation is not None:
                closure_occurrences = semantic_evaluation.knowledge_map.planned_occurrences
                closure_sources = semantic_evaluation.knowledge_map.source_knowledge_points
            else:
                closure_map = (semantic_evaluation_payload or {}).get("knowledge_map", {})
                closure_occurrences = closure_map.get("planned_occurrences", [])
                closure_sources = closure_map.get("source_knowledge_points", [])
            downstream_closure_report = analyze_downstream_closure(
                digital_book=_digital_book,
                planned_occurrences=closure_occurrences,
                source_knowledge_points=closure_sources,
                semantic_execution=semantic_execution,
            )
            write_json(downstream_closure_path, downstream_closure_report.to_dict())
            write_text(
                downstream_closure_markdown_path,
                render_downstream_closure_markdown(downstream_closure_report),
            )
            shared_fact_records = _shared_fact_records_for_audit(
                semantic_execution=semantic_execution,
                semantic_evaluation=semantic_evaluation,
                coverage=coverage,
            )
            shared_fact_candidates = recall_shared_fact_candidates(shared_fact_records)
            blocked_ids = {
                str(item.get("occurrence_id"))
                for item in semantic_execution.blocked_occurrences
                if item.get("occurrence_id")
            }
            shared_fact_report = audit_shared_fact_proposals(
                rendered_occurrences=shared_fact_records,
                proposals=shared_fact_proposals or [],
                blocked_occurrence_ids=blocked_ids,
                downstream_closure=downstream_closure_report,
                candidate_pair_count=len(shared_fact_candidates),
            )
            shared_fact_report.candidate_pairs = shared_fact_candidates
            write_json(shared_fact_proposals_path, shared_fact_report.to_dict())
            write_text(
                shared_fact_proposals_markdown_path,
                render_shared_fact_audit_markdown(shared_fact_report),
            )
            shared_fact_compression_report = build_shared_fact_compression_plans(
                shared_fact_report.proposals,
                downstream_closure=downstream_closure_report,
                briefs_by_occurrence={item.occurrence_id: item for item in coverage.briefs},
            )
            write_json(shared_fact_compression_plans_path, shared_fact_compression_report.to_dict())
            write_text(
                shared_fact_compression_plans_markdown_path,
                render_shared_fact_compression_markdown(shared_fact_compression_report),
            )
            if shared_fact_materialization_request:
                shared_fact_materialization_result = self._run_shared_fact_materialization_request(
                    request=shared_fact_materialization_request,
                    compression_report=shared_fact_compression_report,
                    coverage=coverage,
                    markdown=final,
                    digital_book=_digital_book,
                    evidence_chunks=chunks,
                    downstream_closure=downstream_closure_report,
                    semantic_execution=semantic_execution,
                    semantic_evaluation=semantic_evaluation,
                )
                write_json(shared_fact_materialization_path, shared_fact_materialization_result.to_dict())
                write_text(
                    shared_fact_materialization_markdown_path,
                    render_shared_fact_materialization_markdown(shared_fact_materialization_result),
                )
                if shared_fact_materialization_result.markdown_candidate is not None and shared_fact_materialization_result.digital_book_candidate is not None:
                    final = shared_fact_materialization_result.markdown_candidate
                    _digital_book = shared_fact_materialization_result.digital_book_candidate
                    downstream_closure_report = analyze_downstream_closure(
                        digital_book=_digital_book,
                        planned_occurrences=closure_occurrences,
                        source_knowledge_points=closure_sources,
                        semantic_execution=semantic_execution,
                    )
                    write_json(downstream_closure_path, downstream_closure_report.to_dict())
                    write_text(
                        downstream_closure_markdown_path,
                        render_downstream_closure_markdown(downstream_closure_report),
                    )
        if semantic_book_mode and source_book_plan_snapshot is not None and not book_plan_deep_equal(book_plan, source_book_plan_snapshot):
            raise RuntimeError("Semantic book production mutated the fixed BookPlan during export.")
        publication_quality_report = None
        materialization_result = None
        rendered_claim_audit = None
        claim_audit_provider_name = ""
        claim_audit_model = ""
        if semantic_book_mode:
            semantic_book_report = build_semantic_book_conformance_report(
                coverage=coverage,
                markdown=final,
                digital_book_metadata=_digital_book.metadata,
            )
            write_json(semantic_book_conformance_path, semantic_book_report.to_dict())
            write_text(
                semantic_book_conformance_markdown_path,
                render_semantic_book_conformance_markdown(semantic_book_report),
            )
            semantic_closed_loop_passed = (
                semantic_execution is not None
                and not coverage.fallback_occurrences
                and not coverage.rejected_plan_occurrences
                and semantic_book_report.markdown_anchor_coverage == 1.0
                and semantic_book_report.digital_book_anchor_coverage == 1.0
                and semantic_book_report.occurrence_alignment["alignment_rate"] == 1.0
                and semantic_book_report.markdown["status_counts"]["PARTIAL"] == 0
                and semantic_book_report.markdown["status_counts"]["VIOLATION"] == 0
                and semantic_book_report.digital_book["status_counts"]["PARTIAL"] == 0
                and semantic_book_report.digital_book["status_counts"]["VIOLATION"] == 0
                and semantic_book_report.section_discourse.get("status") == "MATCH"
            )
            semantic_objects = semantic_evaluation if semantic_evaluation is not None else semantic_evaluation_payload or {}
            materialization_result = materialize_full_book(
                markdown=final,
                digital_book=_digital_book,
                coverage=coverage,
                outline_signature=outline_signature(book_plan),
                expected_outline_signature=source_outline_signature,
                semantic_objects=semantic_objects,
                expected_semantic_fingerprint=fingerprint_semantic_objects(semantic_objects),
                instructions=[],
                evidence_chunks=chunks,
                source_book_plan_snapshot=source_book_plan_snapshot,
                final_reference_book_plan=book_plan,
                planned_occurrences=(
                    semantic_evaluation.knowledge_map.planned_occurrences
                    if semantic_evaluation is not None
                    else []
                ),
                downstream_closure_report=downstream_closure_report,
                downstream_closure_required=True,
            )
            final = materialization_result.markdown
            _digital_book = materialization_result.digital_book
            write_materialized_book_artifacts(result=materialization_result, output_dir=materialization_dir)
            claim_judge = None
            claim_provider = self.writer.llm_provider if self.writer.use_llm else None
            if claim_provider is not None:
                claim_audit_provider_name = type(claim_provider).__name__
                claim_audit_model = str(getattr(getattr(claim_provider, "config", None), "model", ""))
                claim_judge = OpenAICompatibleEntailmentJudge(
                    claim_provider,
                    model=claim_audit_model,
                )
            rendered_claim_audit = audit_rendered_claims(
                markdown=final,
                briefs=[asdict(item) for item in coverage.briefs],
                evidence_by_id={item.chunk_id: item for item in chunks},
                artifact_root=str(output_dir),
                judge=claim_judge,
                semantic_routing_categories=CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
            )
            write_audit_artifacts(rendered_claim_audit, output_dir=str(output_dir))
            publication_quality_report = materialization_result.publication_quality
            if publication_quality_report is None:
                publication_quality_report = evaluate_publication_quality(
                    markdown=final,
                    digital_book=_digital_book,
                    coverage=coverage,
                    chunks=chunks,
                    semantic_closed_loop_passed=semantic_closed_loop_passed,
                )
            publication_quality_report = integrate_rendered_claim_semantic_audit(
                report=publication_quality_report,
                rendered_claim_audit=rendered_claim_audit,
            )
            # Materialization ran before this read-only claim audit.  Mirror
            # the final deterministic publication decision onto its audit
            # record so the materialization gate and workflow manifest cannot
            # disagree about semantic evidence blockers.
            materialization_result.publication_quality = publication_quality_report
            claim_blockers = tuple(
                item.code
                for item in publication_quality_report.blockers
                if item.code in {
                    "UNSUPPORTED_RENDERED_SEMANTIC_CLAIM",
                    "PARTIALLY_SUPPORTED_RENDERED_SEMANTIC_CLAIM",
                    "UNRESOLVED_RENDERED_SEMANTIC_CLAIM",
                    "INVALID_RENDERED_CLAIM_SEMANTIC_AUDIT",
                }
            )
            materialization_result.publication_gate = replace(
                materialization_result.publication_gate,
                publication_quality_status=publication_quality_report.publication_quality_status,
                final_publication_status=publication_quality_report.final_publication_status,
                publishable=(
                    materialization_result.publication_gate.publishable
                    and publication_quality_report.final_publication_status == "PASS"
                ),
                unresolved_high_severity_issues=(
                    materialization_result.publication_gate.unresolved_high_severity_issues
                    + len(claim_blockers)
                ),
                blockers=tuple(dict.fromkeys([*materialization_result.publication_gate.blockers, *claim_blockers])),
            )
            write_materialized_book_artifacts(result=materialization_result, output_dir=materialization_dir)
            (
                publication_quality_path,
                publication_quality_markdown_path,
                repair_history_audit_path,
                repair_history_markdown_path,
            ) = write_publication_quality_artifacts(
                report=publication_quality_report,
                output_dir=publication_quality_dir,
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
            "domain_config": domain_config.to_dict(),
            "planning_mode": planning_mode,
            "semantic_execution_mode": semantic_execution_mode,
            "semantic_planner_mode": semantic_planner_mode,
            "rendered_claim_evidence_audit": (
                {
                    "json": _portable_path(rendered_claim_evidence_audit_path),
                    "markdown": _portable_path(rendered_claim_evidence_audit_markdown_path),
                    "provider": claim_audit_provider_name,
                    "model": claim_audit_model,
                    "summary": rendered_claim_audit.to_dict().get("summary", {}),
                }
                if rendered_claim_audit is not None
                else None
            ),
            "source_book_plan_invariant": (
                {
                    "source_outline_signature": source_outline_signature,
                    "final_outline_signature": outline_signature(book_plan),
                    "source_book_plan_fingerprint": source_book_plan_fingerprint,
                    "final_book_plan_fingerprint": book_plan_fingerprint(book_plan),
                    "deep_equal": book_plan_deep_equal(book_plan, source_book_plan_snapshot),
                }
                if semantic_book_mode and source_book_plan_snapshot is not None
                else None
            ),
            "input": {
                "video_segments_path": _portable_path(video_segments_path),
                "document_segments_path": _portable_path(document_segments_path) if document_segments_path else "",
                "manifest_xlsx": _portable_path(manifest_xlsx) if manifest_xlsx else "",
                "book_plan_input": _portable_path(book_plan_input) if book_plan_input else "",
                "book_plan_is_frozen": bool(book_plan_is_frozen),
                "semantic_book_mode": bool(semantic_book_mode),
                "semantic_evaluation_input": _portable_path(semantic_evaluation_input) if semantic_evaluation_input else "",
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
                "rendered_claim_evidence_audit": (
                    rendered_claim_audit.to_dict().get("summary", {})
                    if rendered_claim_audit is not None
                    else {}
                ),
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
                "semantic_book_conformance_json": _portable_path(semantic_book_conformance_path) if semantic_book_mode else "",
                "semantic_book_conformance_markdown": _portable_path(semantic_book_conformance_markdown_path) if semantic_book_mode else "",
                "knowledge_map_json": _portable_path(knowledge_map_path) if knowledge_map_path.exists() else "",
                "learning_trajectory_report_markdown": _portable_path(learning_trajectory_report_path) if learning_trajectory_report_path.exists() else "",
                "canonical_mapping_audit_markdown": _portable_path(canonical_mapping_audit_path) if canonical_mapping_audit_path.exists() else "",
                "semantic_planning_evaluation_json": _portable_path(semantic_planning_evaluation_path) if semantic_planning_evaluation_path.exists() else "",
                "semantic_learning_trajectory_report_markdown": _portable_path(semantic_learning_trajectory_report_path) if semantic_learning_trajectory_report_path.exists() else "",
                "evidence_coverage_resolution_json": _portable_path(evidence_coverage_resolution_path) if evidence_coverage_resolution_path.exists() else "",
                "planning_evidence_gate_markdown": _portable_path(planning_evidence_gate_path) if planning_evidence_gate_path.exists() else "",
                "rendered_claim_evidence_audit_json": _portable_path(rendered_claim_evidence_audit_path) if rendered_claim_audit is not None else "",
                "rendered_claim_evidence_audit_markdown": _portable_path(rendered_claim_evidence_audit_markdown_path) if rendered_claim_audit is not None else "",
                "rendered_claim_evidence_audit_model": claim_audit_model,
                "rendered_claim_evidence_audit_provider": claim_audit_provider_name,
                "publication_quality_json": _portable_path(publication_quality_path) if publication_quality_report else "",
                "publication_quality_markdown": _portable_path(publication_quality_markdown_path) if publication_quality_report else "",
                "repair_history_audit_json": _portable_path(repair_history_audit_path) if publication_quality_report else "",
                "repair_history_audit_markdown": _portable_path(repair_history_markdown_path) if publication_quality_report else "",
                "materialization_audit_json": _portable_path(materialization_audit_path) if materialization_result else "",
                "materialization_audit_markdown": _portable_path(materialization_markdown_path) if materialization_result else "",
                "materialization_directory": _portable_path(materialization_dir) if materialization_result else "",
                "book_plan": _portable_path(book_plan_path) if book_plan else "",
                "book_outline": _portable_path(book_outline_path) if book_plan else "",
                "curriculum_order": _portable_path(curriculum_order_path) if book_plan else "",
                "book_plan_review_json": _portable_path(book_plan_review_path) if book_plan else "",
                "book_plan_review_markdown": _portable_path(book_plan_review_markdown_path) if book_plan else "",
                "source_book_plan_snapshot": _portable_path(source_book_plan_snapshot_path) if semantic_book_mode and source_book_plan_snapshot is not None else "",
                "source_book_plan_invariant": _portable_path(source_book_plan_invariant_path) if semantic_book_mode and source_book_plan_snapshot is not None else "",
                "semantic_execution_audit": _portable_path(semantic_execution_path) if semantic_execution is not None else "",
                "downstream_teaching_support_closure_json": _portable_path(downstream_closure_path) if downstream_closure_report is not None else "",
                "downstream_teaching_support_closure_markdown": _portable_path(downstream_closure_markdown_path) if downstream_closure_report is not None else "",
                "shared_fact_proposals_json": _portable_path(shared_fact_proposals_path) if shared_fact_report is not None else "",
                "shared_fact_proposals_markdown": _portable_path(shared_fact_proposals_markdown_path) if shared_fact_report is not None else "",
                "shared_fact_compression_plans_json": _portable_path(shared_fact_compression_plans_path) if shared_fact_compression_report is not None else "",
                "shared_fact_compression_plans_markdown": _portable_path(shared_fact_compression_plans_markdown_path) if shared_fact_compression_report is not None else "",
                "shared_fact_materialization_json": _portable_path(shared_fact_materialization_path) if shared_fact_materialization_result is not None else "",
                "shared_fact_materialization_markdown": _portable_path(shared_fact_materialization_markdown_path) if shared_fact_materialization_result is not None else "",
                "chapter_output_root": _portable_path(chapter_output_root or output_dir / "chapter_runs") if book_mode else "",
            },
            "chapter_runs": chapter_run_records,
        }
        if materialization_result is not None:
            manifest["materialization"] = materialization_result.to_dict()
            manifest["semantic_execution"] = semantic_execution.to_dict() if semantic_execution is not None else None
        if semantic_book_mode and source_book_plan_snapshot is not None:
            source_book_plan_invariant = manifest["source_book_plan_invariant"]
            if not source_book_plan_invariant["deep_equal"]:
                raise RuntimeError("Final semantic-book BookPlan differs from its source snapshot.")
            write_json(source_book_plan_invariant_path, source_book_plan_invariant)
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
            semantic_book_conformance_path=str(semantic_book_conformance_path) if semantic_book_mode else "",
            semantic_book_conformance_markdown_path=str(semantic_book_conformance_markdown_path) if semantic_book_mode else "",
            publication_quality_path=str(publication_quality_path) if publication_quality_report else "",
            publication_quality_markdown_path=str(publication_quality_markdown_path) if publication_quality_report else "",
            repair_history_audit_path=str(repair_history_audit_path) if publication_quality_report else "",
            repair_history_markdown_path=str(repair_history_markdown_path) if publication_quality_report else "",
            semantic_closed_loop_status=(publication_quality_report.semantic_closed_loop_status if publication_quality_report else ""),
            publication_quality_status=(publication_quality_report.publication_quality_status if publication_quality_report else ""),
            final_publication_status=(publication_quality_report.final_publication_status if publication_quality_report else ""),
            source_book_plan_snapshot_path=(str(source_book_plan_snapshot_path) if semantic_book_mode and source_book_plan_snapshot is not None else ""),
            source_book_plan_invariant_path=(str(source_book_plan_invariant_path) if semantic_book_mode and source_book_plan_snapshot is not None else ""),
            semantic_execution_path=str(semantic_execution_path) if semantic_execution is not None else "",
            rendered_claim_evidence_audit_path=str(rendered_claim_evidence_audit_path) if rendered_claim_audit is not None else "",
            rendered_claim_evidence_audit_markdown_path=str(rendered_claim_evidence_audit_markdown_path) if rendered_claim_audit is not None else "",
            materialization_audit_path=str(materialization_audit_path) if materialization_result is not None else "",
            materialization_audit_markdown_path=str(materialization_markdown_path) if materialization_result is not None else "",
            downstream_closure_path=str(downstream_closure_path) if downstream_closure_report is not None else "",
            downstream_closure_markdown_path=str(downstream_closure_markdown_path) if downstream_closure_report is not None else "",
            shared_fact_proposals_path=str(shared_fact_proposals_path) if shared_fact_report is not None else "",
            shared_fact_proposals_markdown_path=str(shared_fact_proposals_markdown_path) if shared_fact_report is not None else "",
            shared_fact_compression_plans_path=str(shared_fact_compression_plans_path) if shared_fact_compression_report is not None else "",
            shared_fact_compression_plans_markdown_path=str(shared_fact_compression_plans_markdown_path) if shared_fact_compression_report is not None else "",
            shared_fact_materialization_path=str(shared_fact_materialization_path) if shared_fact_materialization_result is not None else "",
            shared_fact_materialization_markdown_path=str(shared_fact_materialization_markdown_path) if shared_fact_materialization_result is not None else "",
        )

    def _run_shared_fact_materialization_request(
        self,
        *,
        request: Mapping[str, Any],
        compression_report: Any,
        coverage: Any,
        markdown: str,
        digital_book: Any,
        evidence_chunks: list[EvidenceChunk],
        downstream_closure: Any,
        semantic_execution: Any,
        semantic_evaluation: Any,
    ):
        requested_plan_id = str(request.get("compression_plan_id") or "")
        requested_fact_id = str(request.get("shared_fact_id") or "")
        plan = next(
            (
                item
                for item in compression_report.plans
                if (requested_plan_id and item.plan_id == requested_plan_id)
                or (requested_fact_id and item.shared_fact_id == requested_fact_id)
            ),
            None,
        )
        occurrence_id = str(request.get("occurrence_id") or (plan.later_occurrence_id if plan else ""))
        brief = next((item for item in coverage.briefs if item.occurrence_id == occurrence_id), None)
        if plan is None or brief is None:
            return skipped_shared_fact_materialization(
                shared_fact_id=requested_fact_id,
                compression_plan_id=requested_plan_id,
                occurrence_id=occurrence_id,
                reason="COMPRESSION_PLAN_OR_LATER_BRIEF_NOT_FOUND",
            )
        markdown_record = next((item for item in extract_rendered_occurrences(markdown) if item.occurrence_id == occurrence_id), None)
        digital_record = _digital_rendered_occurrence(digital_book, occurrence_id)
        if semantic_evaluation is not None:
            planned_occurrences = semantic_evaluation.knowledge_map.planned_occurrences
            source_knowledge_points = semantic_evaluation.knowledge_map.source_knowledge_points
        else:
            planned_occurrences = []
            source_knowledge_points = []

        def recheck(candidate_markdown: str, candidate_book: Any):
            return analyze_downstream_closure(
                digital_book=candidate_book,
                planned_occurrences=planned_occurrences,
                source_knowledge_points=source_knowledge_points,
                semantic_execution=semantic_execution,
            )

        return materialize_compressible_shared_fact(
            plan=plan,
            brief=brief,
            markdown_document=markdown,
            digital_book=digital_book,
            markdown_rendered=markdown_record,
            digital_book_rendered=digital_record,
            evidence_by_id={item.chunk_id: item for item in evidence_chunks},
            baseline_downstream_closure=downstream_closure,
            downstream_rechecker=recheck,
            shared_fact_span=str(request.get("shared_fact_span") or plan.shared_fact_statement),
            preflight=lambda: True,
        )

    def _run_semantic_runtime_execution(
        self,
        *,
        plans: list[ChapterPlan],
        chunks: list[EvidenceChunk],
        title: str,
        book_plan: Any,
        semantic_evaluation: Any,
        excluded_occurrence_ids: set[str] | None = None,
    ):
        knowledge_map = semantic_evaluation.knowledge_map
        sources = {item.source_knowledge_point_id: item for item in knowledge_map.source_knowledge_points}
        points = {item.knowledge_id: item for item in knowledge_map.knowledge_points}
        plan_by_chapter = {item.chapter_id: item for item in plans}
        runtime_claim_judge = None
        if self.writer.use_llm and self.writer.llm_provider is not None:
            runtime_claim_judge = OpenAICompatibleEntailmentJudge(
                self.writer.llm_provider,
                model=str(getattr(getattr(self.writer.llm_provider, "config", None), "model", "")),
            )

        def render_one(brief: OccurrenceWritingBrief) -> str:
            plan = plan_by_chapter.get(brief.chapter_id)
            if plan is None:
                raise ValueError(f"No frozen ChapterPlan projection for {brief.chapter_id!r}.")
            chapter_chunks = _chunks_for_plan(plan, chunks, book_plan=book_plan)
            selected_ids = set(brief.source_chunk_ids)
            selected = [item for item in chapter_chunks if item.chunk_id in selected_ids] or chapter_chunks
            generated = self.writer.run(
                [plan],
                selected,
                title=title,
                occurrence_writing_briefs=[brief],
                fallback_occurrences=[],
            )
            # The writer owns only the body.  If it already returned a
            # code-generated span, retain that body; otherwise the
            # orchestrator deterministically wraps the returned body.  The
            # model never owns occurrence identity or anchor syntax.
            rendered = next(
                (item for item in extract_rendered_occurrences(generated)
                 if item.occurrence_id == brief.occurrence_id),
                None,
            )
            provenance = (
                rendered.generation_provenance
                if rendered is not None
                else self.writer.last_occurrence_generation_provenance.get(
                    brief.occurrence_id, "unknown"
                )
            )
            return wrap_rendered_occurrence(
                brief,
                rendered.markdown if rendered else generated,
                generation_provenance=provenance,
            )

        return execute_verified_occurrences(
            occurrences=knowledge_map.planned_occurrences,
            deltas=semantic_evaluation.semantic_deltas,
            sources=sources,
            points=points,
            chunks=chunks,
            render_occurrence=render_one,
            excluded_occurrence_ids=excluded_occurrence_ids,
            semantic_entailment_judge=runtime_claim_judge,
        )

    @staticmethod
    def _synchronize_semantic_evaluation_with_payload(evaluation: Any, payload: dict[str, Any]) -> None:
        """Apply an accepted evidence-bounded plan payload to typed runtime inputs.

        Evidence contraction is a semantic-plan transformation, not a BookPlan
        mutation.  Runtime must consume the contracted fields rather than the
        pre-gate typed proposal.  This small bridge keeps the existing typed
        planner objects and avoids a second semantic LLM call.
        """
        knowledge_payload = payload.get("knowledge_map") or {}
        occurrence_payloads = {
            str(item.get("occurrence_id")): item
            for item in knowledge_payload.get("planned_occurrences", [])
            if isinstance(item, dict) and item.get("occurrence_id")
        }
        typed_occurrences = {
            item.occurrence_id: item
            for item in getattr(evaluation.knowledge_map, "planned_occurrences", [])
        }
        occurrence_fields = (
            "role", "required_self_facets", "required_self_extension_keys",
            "intended_grants", "intended_extension_keys", "intended_contribution",
            "new_context", "repeated_aspects", "trusted_for_state",
        )
        for occurrence_id, raw in occurrence_payloads.items():
            target = typed_occurrences.get(occurrence_id)
            if target is None:
                continue
            for field_name in occurrence_fields:
                if field_name in raw:
                    value = raw[field_name]
                    if isinstance(value, list):
                        value = list(value)
                    setattr(target, field_name, value)

        delta_payloads = {
            str(item.get("occurrence_id")): item
            for item in payload.get("semantic_deltas", [])
            if isinstance(item, dict) and item.get("occurrence_id")
        }
        typed_deltas = {
            item.occurrence_id: item
            for item in getattr(evaluation, "semantic_deltas", [])
        }
        delta_fields = (
            "new_facets", "new_extension_keys", "contribution_summary",
            "evidence_chunk_ids",
        )
        for occurrence_id, raw in delta_payloads.items():
            target = typed_deltas.get(occurrence_id)
            if target is None:
                continue
            for field_name in delta_fields:
                if field_name in raw:
                    value = raw[field_name]
                    setattr(target, field_name, list(value) if isinstance(value, list) else value)

    def _render_semantic_execution_markdown(self, title: str, plans: list[ChapterPlan], execution, *, book_plan=None) -> str:
        plan_titles = {item.chapter_id: item.title for item in plans}
        briefs = {item.occurrence_id: item for item in execution.coverage.briefs}
        ordered_rows = sorted(
            execution.markdown_occurrences,
            key=lambda value: (
                briefs[value["occurrence_id"]].task_ordinal if value["occurrence_id"] in briefs else 0,
                briefs[value["occurrence_id"]].occurrence_ordinal if value["occurrence_id"] in briefs else 0,
                value["occurrence_id"],
            ),
        )
        section_bodies, section_audits = build_section_discourse_bodies(ordered_rows, list(briefs.values()))
        blocked_ids = {
            str(item.get("occurrence_id"))
            for item in execution.blocked_occurrences
            if item.get("occurrence_id")
        }
        zero_ids = {item.occurrence_id for item in execution.coverage.zero_render_occurrences}
        section_audits = complete_section_discourse_audits(
            section_audits,
            blocked_occurrence_ids=blocked_ids,
            zero_render_occurrence_ids=zero_ids,
            section_catalog=[
                {
                    "chapter_id": chapter.chapter_id,
                    "section_id": section.section_id,
                    "title": section.title,
                }
                for chapter in (getattr(book_plan, "chapters", None) or [])
                for section in chapter.sections
            ],
        )
        execution.section_assemblies = [item.to_dict() for item in section_audits]
        lines = [f"# {title}", "", "> 本教材按固定 BookPlan 生成；语义正文按运行时验证顺序形成。", ""]
        rows_by_section: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in ordered_rows:
            brief = briefs.get(item["occurrence_id"])
            if brief is not None:
                rows_by_section.setdefault((brief.chapter_id, brief.section_id), []).append(item)
        if book_plan is not None and getattr(book_plan, "chapters", None):
            for chapter in book_plan.chapters:
                lines.extend([f"## {chapter.title}", ""])
                for section in chapter.sections:
                    section_key = (chapter.chapter_id, section.section_id)
                    lines.extend([f"### {section.title}", ""])
                    visible_blocks: list[str] = []
                    for item in rows_by_section.get(section_key, []):
                        brief = briefs.get(item["occurrence_id"])
                        if brief is None:
                            continue
                        visible_blocks.append(
                            wrap_rendered_occurrence(
                                brief,
                                section_bodies.get(item["occurrence_id"], item["body"]),
                            ).rstrip()
                        )
                    if visible_blocks:
                        # Keep every occurrence anchor, but remove the blank
                        # paragraph separator so adjacent spans form one
                        # student-visible section passage in Markdown.
                        lines.extend(["\n".join(visible_blocks), ""])
        else:
            current_chapter = ""
            current_section: tuple[str, str] | None = None
            for item in ordered_rows:
                brief = briefs.get(item["occurrence_id"])
                if brief is None:
                    continue
                if brief.chapter_id != current_chapter:
                    current_chapter = brief.chapter_id
                    lines.extend([f"## {plan_titles.get(current_chapter, current_chapter)}", ""])
                    current_section = None
                section_key = (brief.chapter_id, brief.section_id)
                if section_key != current_section:
                    current_section = section_key
                    lines.extend([f"### {item.get('source_title') or brief.source_title}", ""])
                lines.extend([
                    wrap_rendered_occurrence(brief, section_bodies.get(item["occurrence_id"], item["body"])).rstrip(),
                    "",
                ])
        return "\n".join(lines).rstrip() + "\n"

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
        occurrence_writing_briefs: list[OccurrenceWritingBrief] | None = None,
        fallback_occurrences: list[FallbackOccurrence] | None = None,
        semantic_book_mode: bool = False,
        book_plan_is_frozen: bool = False,
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

            chapter_chunks = _chunks_for_plan(plan, chunks, book_plan=book_plan)
            # A cached chapter may predate the immutable trajectory constraints.
            # Never present it as a Phase 2A constrained generation.
            if resume_chapters and not occurrence_writing_briefs and not fallback_occurrences and _reusable_chapter(status_path, final_path):
                _progress(f"chapter {chapter_index}/{len(plans)}: reusing completed output for {plan.title}")
                prepared_plans = self._prepare_chapter_plans(
                    [plan], chapter_chunks,
                    semantic_book_mode=semantic_book_mode,
                    book_plan_is_frozen=book_plan_is_frozen,
                )
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
                    prepared_plans = self._prepare_chapter_plans(
                        [plan], budgeted_chunks,
                        semantic_book_mode=semantic_book_mode,
                        book_plan_is_frozen=book_plan_is_frozen,
                    )
                    chapter_title = prepared_plans[0].title if prepared_plans else plan.title
                    chapter_briefs = briefs_for_chapter(occurrence_writing_briefs or [], plan.chapter_id)
                    chapter_fallbacks = fallbacks_for_chapter(fallback_occurrences or [], plan.chapter_id)
                    chapter_draft = self.writer.run(
                        prepared_plans,
                        budgeted_chunks,
                        title=chapter_title,
                        occurrence_writing_briefs=chapter_briefs,
                        fallback_occurrences=chapter_fallbacks,
                    )
                    chapter_current = chapter_draft
                    chapter_reports: list[ReviewReport] = []
                    chapter_review_history: list[dict[str, Any]] = []
                    chapter_review_warnings: list[str] = []

                    chapter_review_round_limit = 0 if (chapter_briefs or chapter_fallbacks) else config.normalized_review_rounds()
                    for round_index in range(1, chapter_review_round_limit + 1):
                        _progress(f"chapter {chapter_index}: review round {round_index}")
                        review_warnings: list[str] = []
                        try:
                            fact_issues = self.evidence_reviewer.run(prepared_plans, budgeted_chunks, chapter_current)
                        except Exception as exc:
                            fact_issues = _reviewer_failure_issues(prepared_plans, "evidence", exc)
                            review_warnings.append(f"evidence reviewer failed; kept chapter draft: {type(exc).__name__}: {exc}")
                        try:
                            pedagogy_issues = self.pedagogy_reviewer.run(prepared_plans, chapter_current)
                        except Exception as exc:
                            pedagogy_issues = _reviewer_failure_issues(prepared_plans, "pedagogy", exc)
                            review_warnings.append(f"pedagogy reviewer failed; kept chapter draft: {type(exc).__name__}: {exc}")
                        chapter_review_warnings.extend(review_warnings)
                        chapter_reports = self.review_composer.run(prepared_plans, fact_issues, pedagogy_issues)
                        issue_count = sum(len(report.fact_issues) + len(report.pedagogy_issues) for report in chapter_reports)
                        chapter_review_history.append(
                            {
                                "chapter_id": plan.chapter_id,
                                "chapter_title": chapter_title,
                                "round": round_index,
                                "issue_count": issue_count,
                                "warnings": review_warnings,
                                "reports": chapter_reports,
                            }
                        )
                        if round_index < config.normalized_review_rounds() and issue_count:
                            chapter_current = self.revision.run(chapter_current, chapter_reports)
                        else:
                            break

                    chapter_revision_warnings: list[str] = []
                    if chapter_briefs or chapter_fallbacks:
                        chapter_final = chapter_current
                    else:
                        try:
                            chapter_final = self.revision.run(chapter_current, chapter_reports)
                        except Exception as exc:
                            chapter_final = chapter_current
                            chapter_revision_warnings.append(
                                f"final revision failed; kept reviewed draft as final: {type(exc).__name__}: {exc}"
                            )
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
                if self.writer.last_conformance_report:
                    write_json(chapter_dir / "rendered_conformance.json", self.writer.last_conformance_report.to_dict())
                    write_text(
                        chapter_dir / "rendered_conformance.md",
                        render_conformance_report_markdown(self.writer.last_conformance_report),
                    )
                write_json(chapter_dir / "review_report.json", chapter_reports)
                write_text(chapter_dir / "review_report.md", render_review_markdown(chapter_reports, chapter_summary))
                write_json(chapter_dir / "review_history.json", chapter_review_history)
                write_json(chapter_dir / "workflow_summary.json", chapter_summary)
                write_json(chapter_dir / "token_budget_report.json", chapter_token_budget_report)
                chapter_artifact_warnings = [*chapter_review_warnings, *chapter_revision_warnings]
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
                    "conformance_anchor_coverage": (
                        self.writer.last_conformance_report.anchor_coverage
                        if self.writer.last_conformance_report else None
                    ),
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

    def _prepare_chapter_plans(
        self,
        plans: list[ChapterPlan],
        chunks: list[EvidenceChunk],
        *,
        semantic_book_mode: bool = False,
        book_plan_is_frozen: bool = False,
    ) -> list[ChapterPlan]:
        if semantic_book_mode or book_plan_is_frozen:
            # Semantic execution consumes the direct frozen BookPlan
            # projection.  Presentation polishing and generated activities are
            # deliberately outside the semantic A/B input.
            return plans
        prepared = self.title_polisher.run(plans, chunks)
        prepared = self.activity_designer.run(prepared)
        return self.case_designer.run(prepared, chunks)


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _chunks_for_plan(plan: ChapterPlan, chunks: list[EvidenceChunk], book_plan: Any = None) -> list[EvidenceChunk]:
    expected_ids: list[str] = []
    expected_ids.extend(plan.evidence_chunk_ids)
    for point in plan.knowledge_points:
        expected_ids.extend(point.chunk_ids)
    expected_ids.extend(_book_project_evidence_ids(book_plan, plan.chapter_id))
    expected = {chunk_id for chunk_id in expected_ids if chunk_id}
    if not expected:
        return []
    return [chunk for chunk in chunks if chunk.chunk_id in expected]


def _reviewer_failure_issues(plans: list[ChapterPlan], reviewer_name: str, exc: Exception) -> dict[str, list[ReviewIssue]]:
    message = f"{reviewer_name} reviewer failed: {type(exc).__name__}: {exc}"
    return {
        plan.chapter_id: [
            ReviewIssue(
                severity="medium",
                location=plan.chapter_id,
                message=message,
                suggestion="保留当前章节草稿继续导出；后续应检查审稿模型输出格式或重跑本章审稿。",
            )
        ]
        for plan in plans
    }


def _book_project_evidence_ids(book_plan: Any, chapter_id: str) -> list[str]:
    if not book_plan:
        return []
    for chapter in getattr(book_plan, "chapters", []) or []:
        if getattr(chapter, "chapter_id", "") != chapter_id:
            continue
        ids: list[str] = []
        ids.extend(getattr(chapter, "primary_material_ids", []) or [])
        ids.extend(getattr(chapter, "reference_material_ids", []) or [])
        ids.extend(getattr(chapter, "recommended_video_ids", []) or [])
        for section in getattr(chapter, "sections", []) or []:
            ids.extend(getattr(section, "primary_material_ids", []) or [])
            ids.extend(getattr(section, "reference_material_ids", []) or [])
            ids.extend(getattr(section, "recommended_video_ids", []) or [])
        return list(dict.fromkeys(chunk_id for chunk_id in ids if chunk_id))
    return []


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


def _shared_fact_records_for_audit(*, semantic_execution: Any, semantic_evaluation: Any, coverage: Any) -> list[dict[str, Any]]:
    """Build read-only cross-canonical audit records from live execution output.

    This projection intentionally contains no planning authority.  It joins the
    already-rendered spans to their planned identity, brief, and local execution
    result so Phase 3B-1 can audit support without changing any upstream object.
    """
    planned = {}
    if semantic_evaluation is not None:
        planned = {
            item.occurrence_id: item
            for item in getattr(semantic_evaluation.knowledge_map, "planned_occurrences", ())
        }
    briefs = {item.occurrence_id: item for item in getattr(coverage, "briefs", ())}
    transitions = {str(item.get("occurrence_id")): item for item in getattr(semantic_execution, "transitions", ())}
    rendered = {str(item.get("occurrence_id")): item for item in getattr(semantic_execution, "markdown_occurrences", ())}
    records: list[dict[str, Any]] = []
    for occurrence_id, item in rendered.items():
        occurrence = planned.get(occurrence_id)
        brief = briefs.get(occurrence_id)
        transition = transitions.get(occurrence_id, {})
        if occurrence is None:
            continue
        position = getattr(occurrence, "position", None)
        position_payload = {
            key: int(getattr(position, key, 0) or 0)
            for key in ("chapter_ordinal", "task_ordinal", "occurrence_ordinal", "section_ordinal", "source_point_ordinal")
        }
        source_ids = list(getattr(occurrence, "source_chunk_ids", ()) or ())
        if brief is not None:
            source_ids = list(dict.fromkeys(source_ids + list(getattr(brief, "source_chunk_ids", ()) or ())))
        records.append(
            {
                "occurrence_id": occurrence_id,
                "canonical_knowledge_id": getattr(occurrence, "knowledge_id", ""),
                "knowledge_id": getattr(occurrence, "knowledge_id", ""),
                "source_chunk_ids": source_ids,
                "body": item.get("body", ""),
                "role": getattr(occurrence, "role", ""),
                "position": position_payload,
                "required_facets": list(getattr(brief, "required_facets", ()) or ()) if brief else [],
                "verified_facets": list(transition.get("granted_facets", ()) or ()),
                "conformance": transition.get("conformance", ""),
                "evidence": transition.get("evidence", ""),
                "runtime_grant_applied": bool(transition.get("grant_applied", False)),
                "rendered_span_id": item.get("rendered_span_id", ""),
            }
        )
    return records


def _digital_rendered_occurrence(digital_book: Any, occurrence_id: str) -> RenderedOccurrence | None:
    found: list[RenderedOccurrence] = []
    for project in getattr(digital_book, "projects", ()):
        for task in getattr(project, "tasks", ()):
            for block in getattr(task, "blocks", ()):
                semantic = block.metadata.get("semantic_occurrence") if getattr(block, "metadata", None) else None
                if not isinstance(semantic, Mapping) or semantic.get("occurrence_id") != occurrence_id:
                    continue
                body = str(getattr(block, "markdown", "") or "")
                found.append(
                    RenderedOccurrence(
                        occurrence_id=occurrence_id,
                        chapter_id=str(semantic.get("chapter_id") or getattr(project, "project_id", "")),
                        section_id=str(semantic.get("section_id") or ""),
                        task_id=str(getattr(task, "task_id", "")),
                        markdown=body,
                        start_offset=0,
                        end_offset=len(body),
                        render_target="digital_book",
                        block_id=str(getattr(block, "block_id", "")),
                    )
                )
    return found[0] if len(found) == 1 else None


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
