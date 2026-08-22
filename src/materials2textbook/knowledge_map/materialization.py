"""Phase 3E: fail-closed materialization of already accepted repairs.

This is deliberately not another planner or writer.  It consumes only repair
candidates that earlier phases accepted after dual-render conformance checks,
applies the same body to the two occurrence targets, and then gates release.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    RenderedOccurrence,
    check_rendered_conformance,
    check_rendered_occurrence_records,
    extract_rendered_occurrences,
)
from materials2textbook.knowledge_map.publication_quality import (
    evaluate_publication_quality,
    write_publication_quality_artifacts,
)
from materials2textbook.knowledge_map.publication_quality_models import PublicationQualityReport
from materials2textbook.knowledge_map.downstream_closure import summarize_downstream_closure
from materials2textbook.knowledge_map.outline import book_plan_deep_equal
from materials2textbook.knowledge_map.safe_auto_repair import RepairAttemptStatus, SynchronizedRepairResult
from materials2textbook.knowledge_map.writing_briefs import WritingBriefCoverage
from materials2textbook.knowledge_map.models import PlannedOccurrence
from materials2textbook.schemas import BookPlan, DigitalBook, EvidenceChunk


class OccurrenceFinalStatus:
    VERIFIED_ORIGINAL = "VERIFIED_ORIGINAL"
    VERIFIED_REPAIRED = "VERIFIED_REPAIRED"
    REJECTED_EVIDENCE = "REJECTED_EVIDENCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FAILED_CONFORMANCE = "FAILED_CONFORMANCE"
    ZERO_RENDERED = "ZERO_RENDERED"
    BLOCKED_BEFORE_RENDER = "BLOCKED_BEFORE_RENDER"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"
    DROPPED_OCCURRENCE_GOAL = "DROPPED_OCCURRENCE_GOAL"


@dataclass(frozen=True)
class MaterializationInstruction:
    occurrence_id: str
    repair_kind: str
    actions: tuple[str, ...]
    before_text: str
    after_text: str
    audit_record: dict[str, Any]


@dataclass(frozen=True)
class MaterializedRepairAudit:
    occurrence_id: str
    repair_kind: str
    actions: tuple[str, ...]
    before_text: str
    after_text: str
    diff: str
    markdown_block_id: str
    digital_book_block_id: str
    status: str  # MATERIALIZED | ROLLED_BACK
    rollback_reason: str = ""


@dataclass(frozen=True)
class OccurrenceFinalState:
    occurrence_id: str
    status: str
    reasons: tuple[str, ...] = ()
    repair_audit_index: int | None = None
    canonical_knowledge_id: str = ""
    outline_node_id: str = ""
    execution_status: str = ""
    rendered: bool = False
    materialized: bool = True


@dataclass(frozen=True)
class PublicationGate:
    outline_signature_unchanged: bool
    source_book_plan_unchanged: bool
    semantic_objects_unchanged: bool
    markdown_digital_alignment: float
    no_accepted_partial: bool
    no_silent_fallback: bool
    all_materialized_repairs_audited: bool
    terminal_state_complete: bool
    unresolved_high_severity_issues: int
    publishable: bool
    downstream_closure_complete: bool = False
    downstream_hard_blockers: int = 0
    downstream_review_required: int = 0
    publication_quality_status: str = "PASS"
    final_publication_status: str = "PASS"
    blockers: tuple[str, ...] = ()


@dataclass
class FullBookMaterializationResult:
    markdown: str
    digital_book: DigitalBook
    final_states: list[OccurrenceFinalState]
    repair_audit: list[MaterializedRepairAudit]
    publication_gate: PublicationGate
    markdown_conformance: dict[str, Any]
    digital_book_conformance: dict[str, Any]
    publication_quality: PublicationQualityReport | None = None
    planned_occurrence_count: int = 0
    terminal_state_count: int = 0
    terminal_state_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_states": [asdict(item) for item in self.final_states],
            "repair_audit": [asdict(item) for item in self.repair_audit],
            "publication_gate": asdict(self.publication_gate),
            "planned_occurrence_count": self.planned_occurrence_count,
            "terminal_state_count": self.terminal_state_count,
            "terminal_state_complete": self.terminal_state_complete,
            "markdown_conformance": self.markdown_conformance,
            "digital_book_conformance": self.digital_book_conformance,
            "publication_quality": self.publication_quality.to_dict() if self.publication_quality else None,
        }


def instruction_from_safe_repair(result: SynchronizedRepairResult) -> MaterializationInstruction | None:
    """Adapt only an already accepted Phase 3B candidate."""
    attempt = result.attempt
    if attempt.status != RepairAttemptStatus.ACCEPTED or not result.markdown_candidate or not result.digital_book_candidate:
        return None
    if result.markdown_candidate.markdown != result.digital_book_candidate.markdown:
        return None
    return MaterializationInstruction(
        occurrence_id=attempt.occurrence_id,
        repair_kind="SAFE_REMOVE_RETEACH",
        actions=attempt.executed_actions,
        before_text=attempt.original_text,
        after_text=attempt.candidate_text,
        audit_record=attempt.to_dict(),
    )


def instruction_from_generated_repair(result) -> MaterializationInstruction | None:
    """Adapt accepted Phase 3C results without importing its generator layer."""
    patch = result.patch
    if patch.status != RepairAttemptStatus.ACCEPTED or not result.markdown_candidate or not result.digital_book_candidate:
        return None
    if result.markdown_candidate.markdown != result.digital_book_candidate.markdown:
        return None
    return MaterializationInstruction(
        occurrence_id=patch.occurrence_id,
        repair_kind="GENERATED_PATCH",
        actions=(patch.repair_action,),
        before_text=patch.before_text,
        after_text=patch.after_text,
        audit_record=patch.to_dict(),
    )


def instruction_from_recall_capsule(result) -> MaterializationInstruction | None:
    """Adapt accepted Phase 3D recall capsules without reopening its plan."""
    attempt = result.attempt
    if attempt.status != RepairAttemptStatus.ACCEPTED or not result.markdown_candidate or not result.digital_book_candidate:
        return None
    if result.markdown_candidate.markdown != result.digital_book_candidate.markdown:
        return None
    return MaterializationInstruction(
        occurrence_id=attempt.occurrence_id,
        repair_kind="RECALL_CAPSULE",
        actions=("RESTORE_MINIMAL_RECALL",),
        before_text=attempt.before_text,
        after_text=attempt.after_text,
        audit_record=_jsonable(attempt),
    )


def materialize_full_book(
    *, markdown: str, digital_book: DigitalBook, coverage: WritingBriefCoverage,
    outline_signature: str, expected_outline_signature: str,
    semantic_objects: Any, expected_semantic_fingerprint: str | None = None,
    instructions: list[MaterializationInstruction] | None = None,
    evidence_chunks: list[EvidenceChunk] | None = None,
    plan_contractions: list[Any] | None = None,
    repair_history_inputs: list[Any] | None = None,
    declared_rollback_count: int | None = None,
    source_book_plan_snapshot: BookPlan | None = None,
    final_reference_book_plan: BookPlan | None = None,
    planned_occurrences: list[PlannedOccurrence] | None = None,
    downstream_closure_report: Any | None = None,
    downstream_closure_required: bool = False,
) -> FullBookMaterializationResult:
    """Apply accepted candidate text identically to both occurrence renderers.

    Exact source-body matching is required before every mutation.  Any drift,
    duplicate instruction, missing anchor, or missing DigitalBook block is
    rolled back locally and becomes an explicit failed occurrence state.
    """
    instructions = instructions or []
    semantic_before = fingerprint_semantic_objects(semantic_objects)
    expected_semantic_fingerprint = expected_semantic_fingerprint or semantic_before
    source_book_plan_unchanged = (
        source_book_plan_snapshot is not None
        and final_reference_book_plan is not None
        and book_plan_deep_equal(source_book_plan_snapshot, final_reference_book_plan)
    )
    output_markdown = markdown
    output_book = deepcopy(digital_book)
    audit: list[MaterializedRepairAudit] = []
    rejected: dict[str, list[str]] = {
        item.occurrence_id: [f"REJECTED_EVIDENCE:{item.reason}"]
        for item in coverage.rejected_plan_occurrences
    }
    failed: dict[str, list[str]] = {}
    seen: set[str] = set()

    for instruction in instructions:
        if instruction.occurrence_id not in {item.occurrence_id for item in coverage.briefs}:
            reason = "UNKNOWN_OR_NON_WRITER_OCCURRENCE_INSTRUCTION"
            failed.setdefault(instruction.occurrence_id, []).append(reason)
            audit.append(_rolled_back_audit(instruction, "", "", reason))
            continue
        if instruction.occurrence_id in seen:
            failed.setdefault(instruction.occurrence_id, []).append("DUPLICATE_MATERIALIZATION_INSTRUCTION")
            audit.append(_rolled_back_audit(instruction, "", "", "DUPLICATE_MATERIALIZATION_INSTRUCTION"))
            continue
        seen.add(instruction.occurrence_id)
        markdown_records = {item.occurrence_id: item for item in extract_rendered_occurrences(output_markdown)}
        markdown_record = markdown_records.get(instruction.occurrence_id)
        digital_block = _digital_block_for_occurrence(output_book, instruction.occurrence_id)
        if not markdown_record or digital_block is None:
            reason = "MISSING_MARKDOWN_ANCHOR_OR_DIGITAL_BLOCK"
            failed.setdefault(instruction.occurrence_id, []).append(reason)
            audit.append(_rolled_back_audit(instruction, markdown_record.block_id if markdown_record else "", digital_block.block_id if digital_block else "", reason))
            continue
        if markdown_record.markdown != instruction.before_text or digital_block.markdown != instruction.before_text:
            reason = "MATERIALIZATION_BASE_MISMATCH"
            failed.setdefault(instruction.occurrence_id, []).append(reason)
            audit.append(_rolled_back_audit(instruction, markdown_record.block_id, digital_block.block_id, reason))
            continue
        candidate_markdown = _replace_markdown_occurrence(output_markdown, markdown_record, instruction.after_text)
        candidate_book = deepcopy(output_book)
        candidate_block = _digital_block_for_occurrence(candidate_book, instruction.occurrence_id)
        if candidate_block is None:
            reason = "DIGITAL_BLOCK_DISAPPEARED_DURING_COPY"
            failed.setdefault(instruction.occurrence_id, []).append(reason)
            audit.append(_rolled_back_audit(instruction, markdown_record.block_id, digital_block.block_id, reason))
            continue
        candidate_block.markdown = instruction.after_text
        if not _dual_match(coverage, candidate_markdown, candidate_book, instruction.occurrence_id):
            reason = "POST_MATERIALIZATION_CONFORMANCE_NOT_MATCH"
            failed.setdefault(instruction.occurrence_id, []).append(reason)
            audit.append(_rolled_back_audit(instruction, markdown_record.block_id, digital_block.block_id, reason))
            continue
        output_markdown, output_book = candidate_markdown, candidate_book
        audit.append(MaterializedRepairAudit(
            occurrence_id=instruction.occurrence_id,
            repair_kind=instruction.repair_kind,
            actions=instruction.actions,
            before_text=instruction.before_text,
            after_text=instruction.after_text,
            diff=_unified_diff(instruction.before_text, instruction.after_text),
            markdown_block_id=markdown_record.block_id,
            digital_book_block_id=digital_block.block_id,
            status="MATERIALIZED",
        ))

    _refresh_digital_semantic_metadata(output_book, coverage)
    markdown_report = check_rendered_conformance(
        coverage.briefs,
        output_markdown,
        zero_render_occurrences=coverage.zero_render_occurrences,
    )
    digital_records = _digital_occurrence_records(output_book)
    digital_report = check_rendered_occurrence_records(
        coverage.briefs,
        digital_records,
        zero_render_occurrences=coverage.zero_render_occurrences,
    )
    final_states = _final_states(
        coverage=coverage, markdown_report=markdown_report, digital_report=digital_report,
        audit=audit, rejected=rejected, failed=failed,
        planned_occurrences=planned_occurrences,
    )
    planned_count = len(planned_occurrences or []) or coverage.total_occurrences
    terminal_count = len(final_states)
    terminal_state_complete = planned_count == terminal_count and len({item.occurrence_id for item in final_states}) == terminal_count
    semantic_after = fingerprint_semantic_objects(semantic_objects)
    quality = evaluate_publication_quality(
        markdown=output_markdown,
        digital_book=output_book,
        coverage=coverage,
        chunks=evidence_chunks or [],
        semantic_closed_loop_passed=(
            outline_signature == expected_outline_signature
            and source_book_plan_unchanged
            and semantic_before == semantic_after == expected_semantic_fingerprint
            and not coverage.fallback_occurrences
            and terminal_state_complete
            and all(item.status not in {
                OccurrenceFinalStatus.REJECTED_EVIDENCE,
                OccurrenceFinalStatus.MANUAL_REVIEW,
                OccurrenceFinalStatus.FAILED_CONFORMANCE,
                OccurrenceFinalStatus.BLOCKED_BEFORE_RENDER,
                OccurrenceFinalStatus.EXECUTION_BLOCKED,
                OccurrenceFinalStatus.DROPPED_OCCURRENCE_GOAL,
            } for item in final_states)
            and all(item.overall in {ConformanceStatus.MATCH, ConformanceStatus.NOT_APPLICABLE} for item in [*markdown_report.results, *digital_report.results])
        ),
        plan_contractions=plan_contractions or [],
        final_states=final_states,
        repair_attempts=repair_history_inputs or [],
        materialization_audit=audit,
        declared_rollback_count=declared_rollback_count,
    )
    gate = _publication_gate(
        outline_signature=outline_signature,
        expected_outline_signature=expected_outline_signature,
        source_book_plan_unchanged=source_book_plan_unchanged,
        semantic_before=semantic_before,
        semantic_after=semantic_after,
        expected_semantic_fingerprint=expected_semantic_fingerprint,
        coverage=coverage,
        final_states=final_states,
        audit=audit,
        instructions=instructions,
        markdown_report=markdown_report,
        digital_report=digital_report,
        digital_records=digital_records,
        digital_book=output_book,
        publication_quality=quality,
        terminal_state_complete=terminal_state_complete,
        downstream_closure_report=downstream_closure_report,
        downstream_closure_required=downstream_closure_required,
    )
    return FullBookMaterializationResult(
        markdown=output_markdown,
        digital_book=output_book,
        final_states=final_states,
        repair_audit=audit,
        publication_gate=gate,
        markdown_conformance=markdown_report.to_dict(),
        digital_book_conformance=digital_report.to_dict(),
        publication_quality=quality,
        planned_occurrence_count=planned_count,
        terminal_state_count=terminal_count,
        terminal_state_complete=terminal_state_complete,
    )


def fingerprint_semantic_objects(value: Any) -> str:
    return sha256(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def render_full_book_audit_markdown(result: FullBookMaterializationResult) -> str:
    gate = result.publication_gate
    lines = [
        "# Full-book Materialization Audit", "",
        f"- publishable: {gate.publishable}",
        f"- publication quality status: {gate.publication_quality_status}",
        f"- final publication status: {gate.final_publication_status}",
        f"- outline signature unchanged: {gate.outline_signature_unchanged}",
        f"- source BookPlan unchanged: {gate.source_book_plan_unchanged}",
        f"- semantic objects unchanged: {gate.semantic_objects_unchanged}",
        f"- Markdown/DigitalBook alignment: {gate.markdown_digital_alignment:.0%}",
        f"- no accepted PARTIAL: {gate.no_accepted_partial}",
        f"- no silent fallback: {gate.no_silent_fallback}",
        f"- all materialized repairs audited: {gate.all_materialized_repairs_audited}",
        f"- unresolved high-severity issues: {gate.unresolved_high_severity_issues}",
        f"- downstream closure complete: {gate.downstream_closure_complete}",
        f"- downstream hard blockers: {gate.downstream_hard_blockers}",
        f"- downstream review required: {gate.downstream_review_required}",
        f"- planned occurrence count: {result.planned_occurrence_count}",
        f"- terminal state count: {result.terminal_state_count}",
        f"- terminal state complete: {result.terminal_state_complete}",
        "", "## Occurrence final states", "",
    ]
    for state in result.final_states:
        lines.append(f"- `{state.occurrence_id}` — {state.status}{': ' + '; '.join(state.reasons) if state.reasons else ''}")
    lines.extend(["", "## Materialized repairs", ""])
    if not result.repair_audit:
        lines.append("- none")
    for item in result.repair_audit:
        lines.extend([
            f"### {item.occurrence_id}",
            f"- kind / actions: {item.repair_kind} / {', '.join(item.actions) or 'none'}",
            f"- status: {item.status}",
            f"- Markdown / DigitalBook block: {item.markdown_block_id or 'anchor'} / {item.digital_book_block_id or 'none'}",
            f"- rollback reason: {item.rollback_reason or 'none'}",
            "- before:", "```text", item.before_text or "(empty)", "```",
            "- after:", "```text", item.after_text or "(empty)", "```",
            "```diff", item.diff or "(no text change)", "```", "",
        ])
    if gate.blockers:
        lines.extend(["## Publication blockers", "", *[f"- {item}" for item in gate.blockers]])
    return "\n".join(lines).rstrip() + "\n"


def write_materialized_book_artifacts(
    *, result: FullBookMaterializationResult, output_dir: Path,
) -> tuple[Path, Path, Path, Path]:
    """Persist the synchronized candidate and its audit as one publication unit.

    Callers must inspect ``result.publication_gate.publishable`` before
    promoting these artifacts to a student package.  This helper never writes
    over the original Markdown or DigitalBook export.
    """
    output_dir = Path(output_dir)
    markdown_path = output_dir / "textbook_materialized.md"
    digital_path = output_dir / "digital_book_materialized.json"
    audit_json_path = output_dir / "full_book_materialization.json"
    audit_markdown_path = output_dir / "full_book_materialization.md"
    write_text(markdown_path, result.markdown)
    write_json(digital_path, result.digital_book)
    write_json(audit_json_path, result.to_dict())
    write_text(audit_markdown_path, render_full_book_audit_markdown(result))
    if result.publication_quality:
        write_publication_quality_artifacts(
            report=result.publication_quality,
            output_dir=output_dir / "publication_quality",
        )
    return markdown_path, digital_path, audit_json_path, audit_markdown_path


def _digital_block_for_occurrence(book: DigitalBook, occurrence_id: str):
    found = []
    for project in book.projects:
        for task in project.tasks:
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                if isinstance(semantic, dict) and semantic.get("occurrence_id") == occurrence_id:
                    found.append(block)
    return found[0] if len(found) == 1 else None


def _digital_occurrence_records(book: DigitalBook) -> list[RenderedOccurrence]:
    records: list[RenderedOccurrence] = []
    for project in book.projects:
        for task in project.tasks:
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                if not isinstance(semantic, dict) or not semantic.get("occurrence_id"):
                    continue
                records.append(RenderedOccurrence(
                    occurrence_id=str(semantic["occurrence_id"]),
                    chapter_id=str(semantic.get("chapter_id") or project.project_id),
                    section_id=str(semantic.get("section_id") or ""),
                    task_id=task.task_id,
                    markdown=block.markdown,
                    start_offset=0,
                    end_offset=len(block.markdown),
                    render_target="digital_book",
                    block_id=block.block_id,
                ))
    return records


def _replace_markdown_occurrence(markdown: str, record: RenderedOccurrence, after: str) -> str:
    return markdown[:record.start_offset] + after + markdown[record.end_offset:]


def _dual_match(coverage: WritingBriefCoverage, markdown: str, book: DigitalBook, occurrence_id: str) -> bool:
    brief = next((item for item in coverage.briefs if item.occurrence_id == occurrence_id), None)
    if brief is None:
        return False
    markdown_result = check_rendered_conformance([brief], markdown).results[0]
    digital_result = check_rendered_occurrence_records([brief], _digital_occurrence_records(book)).results[0]
    return markdown_result.overall == digital_result.overall == ConformanceStatus.MATCH


def _refresh_digital_semantic_metadata(book: DigitalBook, coverage: WritingBriefCoverage) -> None:
    records = _digital_occurrence_records(book)
    report = check_rendered_occurrence_records(
        coverage.briefs,
        records,
        zero_render_occurrences=coverage.zero_render_occurrences,
    )
    book.metadata["semantic_rendered_occurrences"] = [_jsonable(item) for item in records]
    book.metadata["semantic_rendered_conformance"] = report.to_dict()
    book.metadata["phase3e_materialized"] = True


def _final_states(*, coverage, markdown_report, digital_report, audit, rejected, failed,
                  planned_occurrences: list[PlannedOccurrence] | None = None) -> list[OccurrenceFinalState]:
    markdown_by_id = {item.occurrence_id: item for item in markdown_report.results}
    digital_by_id = {item.occurrence_id: item for item in digital_report.results}
    repaired = {item.occurrence_id: index for index, item in enumerate(audit) if item.status == "MATERIALIZED"}
    states: list[OccurrenceFinalState] = []
    metadata: dict[str, dict[str, str]] = {}

    def register(occurrence_id: str, *, canonical: str = "", outline: str = "") -> None:
        entry = metadata.setdefault(occurrence_id, {})
        if canonical:
            entry["canonical_knowledge_id"] = canonical
        if outline:
            entry["outline_node_id"] = outline

    for item in planned_occurrences or []:
        register(item.occurrence_id, canonical=item.knowledge_id, outline=item.section_id)
    for item in coverage.briefs:
        register(item.occurrence_id, canonical=item.canonical_knowledge_id, outline=item.section_id)
    for item in coverage.fallback_occurrences:
        register(item.occurrence_id, canonical=item.canonical_knowledge_id, outline=item.section_id)
    for item in coverage.rejected_plan_occurrences:
        register(item.occurrence_id, canonical=item.canonical_knowledge_id, outline=item.section_id)
    for item in coverage.zero_render_occurrences:
        register(item.occurrence_id, canonical=item.canonical_knowledge_id, outline=item.outline_node_id)
    for item in coverage.dropped_occurrence_goals:
        register(item.occurrence_id, canonical=item.canonical_knowledge_id, outline=item.section_id)
    for item in coverage.execution_blocked_occurrences:
        occurrence_id = str(item.get("occurrence_id") or "")
        if occurrence_id:
            register(
                occurrence_id,
                canonical=str(item.get("canonical_knowledge_id") or ""),
                outline=str(item.get("outline_node_id") or ""),
            )
    all_ids = list(metadata)
    all_ids.extend(failed)
    for occurrence_id in dict.fromkeys(all_ids):
        info = metadata.get(occurrence_id, {})
        canonical = info.get("canonical_knowledge_id", "")
        outline = info.get("outline_node_id", "")
        if occurrence_id in rejected:
            states.append(OccurrenceFinalState(
                occurrence_id, OccurrenceFinalStatus.REJECTED_EVIDENCE, tuple(rejected[occurrence_id]),
                canonical_knowledge_id=canonical, outline_node_id=outline,
                execution_status=OccurrenceFinalStatus.REJECTED_EVIDENCE, rendered=False,
            ))
            continue
        dropped = next((item for item in coverage.dropped_occurrence_goals if item.occurrence_id == occurrence_id), None)
        if dropped:
            states.append(OccurrenceFinalState(
                occurrence_id, OccurrenceFinalStatus.DROPPED_OCCURRENCE_GOAL, (dropped.reason,),
                canonical_knowledge_id=canonical, outline_node_id=outline,
                execution_status=OccurrenceFinalStatus.DROPPED_OCCURRENCE_GOAL, rendered=False,
            ))
            continue
        fallback = next((item for item in coverage.fallback_occurrences if item.occurrence_id == occurrence_id), None)
        if fallback:
            states.append(OccurrenceFinalState(
                occurrence_id, OccurrenceFinalStatus.MANUAL_REVIEW, (fallback.reason,),
                canonical_knowledge_id=canonical, outline_node_id=outline,
                execution_status=OccurrenceFinalStatus.MANUAL_REVIEW, rendered=False,
            ))
            continue
        zero_render = next((item for item in coverage.zero_render_occurrences if item.occurrence_id == occurrence_id), None)
        if zero_render:
            markdown_result, digital_result = markdown_by_id.get(occurrence_id), digital_by_id.get(occurrence_id)
            if (
                markdown_result is not None
                and digital_result is not None
                and markdown_result.overall == digital_result.overall == ConformanceStatus.NOT_APPLICABLE
            ):
                states.append(OccurrenceFinalState(
                    occurrence_id,
                    OccurrenceFinalStatus.ZERO_RENDERED,
                    (zero_render.non_render_reason,),
                    canonical_knowledge_id=canonical, outline_node_id=outline,
                    execution_status=OccurrenceFinalStatus.ZERO_RENDERED, rendered=False,
                ))
            else:
                states.append(OccurrenceFinalState(
                    occurrence_id,
                    OccurrenceFinalStatus.FAILED_CONFORMANCE,
                    ("ZERO_RENDER_DECISION_AND_RENDERED_OUTPUT_DIVERGED",),
                    canonical_knowledge_id=canonical, outline_node_id=outline,
                    execution_status=OccurrenceFinalStatus.FAILED_CONFORMANCE, rendered=False,
                ))
            continue
        blocked = next((item for item in coverage.execution_blocked_occurrences if item.get("occurrence_id") == occurrence_id), None)
        if blocked:
            code = str(blocked.get("issue_code") or "RUNTIME_EXECUTION_BLOCKED")
            status = (
                OccurrenceFinalStatus.BLOCKED_BEFORE_RENDER
                if code in {"INCOMPLETE_SEMANTIC_EXECUTION_INPUT", "SKIPPED_BY_SEMANTIC_EVIDENCE_GATE"}
                else OccurrenceFinalStatus.EXECUTION_BLOCKED
            )
            states.append(OccurrenceFinalState(
                occurrence_id,
                status,
                (code, str(blocked.get("details") or "")),
                canonical_knowledge_id=canonical, outline_node_id=outline,
                execution_status=status, rendered=bool(blocked.get("rendered", False)),
            ))
            continue
        reasons = list(failed.get(occurrence_id, []))
        markdown_result, digital_result = markdown_by_id.get(occurrence_id), digital_by_id.get(occurrence_id)
        if not markdown_result or not digital_result or markdown_result.overall != ConformanceStatus.MATCH or digital_result.overall != ConformanceStatus.MATCH:
            if not reasons:
                reasons.append("FINAL_CONFORMANCE_NOT_MATCH")
            states.append(OccurrenceFinalState(
                occurrence_id, OccurrenceFinalStatus.FAILED_CONFORMANCE, tuple(reasons),
                canonical_knowledge_id=canonical, outline_node_id=outline,
                execution_status=OccurrenceFinalStatus.FAILED_CONFORMANCE, rendered=False,
            ))
        elif occurrence_id in repaired:
            states.append(OccurrenceFinalState(
                occurrence_id, OccurrenceFinalStatus.VERIFIED_REPAIRED,
                repair_audit_index=repaired[occurrence_id], canonical_knowledge_id=canonical,
                outline_node_id=outline, execution_status=OccurrenceFinalStatus.VERIFIED_REPAIRED,
                rendered=True,
            ))
        else:
            states.append(OccurrenceFinalState(
                occurrence_id, OccurrenceFinalStatus.VERIFIED_ORIGINAL,
                canonical_knowledge_id=canonical, outline_node_id=outline,
                execution_status=OccurrenceFinalStatus.VERIFIED_ORIGINAL, rendered=True,
            ))
    return states


def _publication_gate(*, outline_signature, expected_outline_signature, source_book_plan_unchanged, semantic_before, semantic_after, expected_semantic_fingerprint, coverage, final_states, audit, instructions, markdown_report, digital_report, digital_records, digital_book, publication_quality: PublicationQualityReport, terminal_state_complete: bool, downstream_closure_report: Any | None = None, downstream_closure_required: bool = False) -> PublicationGate:
    expected_ids = {item.occurrence_id for item in coverage.briefs}
    markdown_ids = {item.occurrence_id for item in extract_rendered_occurrences_from_report_source(markdown_report, expected_ids)}
    digital_ids = {item.occurrence_id for item in digital_records}
    aligned = expected_ids & markdown_ids & digital_ids
    roles = {item.occurrence_id: item.role for item in coverage.briefs}
    digital_roles = _digital_roles(digital_book)
    role_aligned = {item for item in aligned if roles.get(item) == digital_roles.get(item)}
    alignment = len(role_aligned) / len(expected_ids) if expected_ids else 1.0
    no_partial = all(item.overall in {ConformanceStatus.MATCH, ConformanceStatus.NOT_APPLICABLE} for item in [*markdown_report.results, *digital_report.results])
    audited = len(audit) == len(instructions) and all(item.status == "MATERIALIZED" for item in audit)
    high = sum(item.status in {
        OccurrenceFinalStatus.REJECTED_EVIDENCE, OccurrenceFinalStatus.MANUAL_REVIEW,
        OccurrenceFinalStatus.FAILED_CONFORMANCE, OccurrenceFinalStatus.BLOCKED_BEFORE_RENDER,
        OccurrenceFinalStatus.EXECUTION_BLOCKED, OccurrenceFinalStatus.DROPPED_OCCURRENCE_GOAL,
    } for item in final_states)
    closure = summarize_downstream_closure(downstream_closure_report)
    blockers: list[str] = []
    if outline_signature != expected_outline_signature:
        blockers.append("OUTLINE_SIGNATURE_CHANGED")
    if not source_book_plan_unchanged:
        blockers.append("SOURCE_BOOK_PLAN_MUTATED")
    if semantic_before != expected_semantic_fingerprint or semantic_after != expected_semantic_fingerprint:
        blockers.append("SEMANTIC_OBJECT_MUTATED")
    if alignment != 1.0:
        blockers.append("MARKDOWN_DIGITAL_ALIGNMENT_NOT_100_PERCENT")
    if not no_partial:
        blockers.append("ACCEPTED_PARTIAL_OR_VIOLATION_PRESENT")
    if coverage.fallback_occurrences:
        blockers.append("EXPLICIT_FALLBACK_REQUIRES_MANUAL_REVIEW")
    if not audited:
        blockers.append("MATERIALIZATION_AUDIT_INCOMPLETE_OR_UNACCEPTED")
    if not terminal_state_complete:
        blockers.append("TERMINAL_STATE_INCOMPLETE")
    if high:
        blockers.append("UNRESOLVED_HIGH_SEVERITY_ISSUES")
    if publication_quality.publication_quality_status != "PASS":
        blockers.append("PUBLICATION_QUALITY_BLOCKERS_PRESENT")
    if downstream_closure_required and not closure["provided"]:
        blockers.append("DOWNSTREAM_CLOSURE_MISSING")
    elif downstream_closure_required and closure["hard_blocker_count"]:
        blockers.append("DOWNSTREAM_CLOSURE_HARD_BLOCKERS")
    if downstream_closure_required and closure["review_required_count"]:
        blockers.append("DOWNSTREAM_CLOSURE_REVIEW_REQUIRED")
    return PublicationGate(
        outline_signature_unchanged=outline_signature == expected_outline_signature,
        source_book_plan_unchanged=source_book_plan_unchanged,
        semantic_objects_unchanged=semantic_before == semantic_after == expected_semantic_fingerprint,
        markdown_digital_alignment=alignment,
        no_accepted_partial=no_partial,
        no_silent_fallback=not coverage.fallback_occurrences,
        all_materialized_repairs_audited=audited,
        terminal_state_complete=terminal_state_complete,
        unresolved_high_severity_issues=high,
        publishable=not blockers,
        downstream_closure_complete=bool(closure["provided"]),
        downstream_hard_blockers=int(closure["hard_blocker_count"]),
        downstream_review_required=int(closure["review_required_count"]),
        publication_quality_status=publication_quality.publication_quality_status,
        final_publication_status=("PASS" if not blockers and publication_quality.semantic_closed_loop_status == "PASS" else "FAIL"),
        blockers=tuple(blockers),
    )


def extract_rendered_occurrences_from_report_source(report, expected_ids: set[str]) -> list[RenderedOccurrence]:
    """The report has anchor truth for every brief; synthesize IDs for gate math."""
    return [RenderedOccurrence(item.occurrence_id, "", "", "", "", 0, 0) for item in report.results if item.anchor_present and item.occurrence_id in expected_ids]


def _digital_roles(book: DigitalBook) -> dict[str, str]:
    roles: dict[str, str] = {}
    for project in book.projects:
        for task in project.tasks:
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                if isinstance(semantic, dict) and semantic.get("occurrence_id"):
                    roles[str(semantic["occurrence_id"])] = str(semantic.get("role") or "")
    return roles


def _rolled_back_audit(instruction, markdown_block_id, digital_book_block_id, reason) -> MaterializedRepairAudit:
    return MaterializedRepairAudit(
        occurrence_id=instruction.occurrence_id, repair_kind=instruction.repair_kind, actions=instruction.actions,
        before_text=instruction.before_text, after_text=instruction.after_text, diff=_unified_diff(instruction.before_text, instruction.after_text),
        markdown_block_id=markdown_block_id, digital_book_block_id=digital_book_block_id,
        status="ROLLED_BACK", rollback_reason=reason,
    )


def _unified_diff(before: str, after: str) -> str:
    import difflib
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="before", tofile="after", lineterm=""))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value
