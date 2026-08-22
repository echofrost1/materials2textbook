"""Publication Quality Layer, downstream of frozen semantic materialization.

This layer is intentionally read-only.  It neither changes KnowledgeMap,
SemanticDelta, roles, evidence planning, writing briefs, repairs, nor the
materialized outputs.  It decides only whether a student-visible publication
is fit to release and produces a complete audit for human follow-up.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.knowledge_map.dropped_goal_reconciliation import (
    inspect_dropped_goal_reconciliation,
)
from materials2textbook.knowledge_map.final_text_quality import inspect_final_text
from materials2textbook.knowledge_map.publication_quality_models import (
    PedagogicalSufficiencyRecord,
    PublicationContentFragment,
    PublicationProvenanceRecord,
    PublicationQualityIssue,
    PublicationQualityReport,
    PublicationQualityStatus,
    PublicationSeverity,
    RepairHistoryEntry,
)
from materials2textbook.knowledge_map.rendered_conformance import extract_rendered_occurrences
from materials2textbook.knowledge_map.rendered_evidence_verification import (
    ClaimType,
    SupportStatus,
    verify_rendered_evidence,
)
from materials2textbook.knowledge_map.writing_briefs import WritingBriefCoverage
from materials2textbook.schemas import DigitalBook, EvidenceChunk


class PublicationQualityCode:
    INTERNAL_LABEL_LEAKAGE = "INTERNAL_LABEL_LEAKAGE"
    TASK_WITHOUT_TEACHING_SUPPORT = "TASK_WITHOUT_TEACHING_SUPPORT"
    ASSESSMENT_WITHOUT_CONTENT_SUPPORT = "ASSESSMENT_WITHOUT_CONTENT_SUPPORT"
    EXERCISE_WITHOUT_CONTENT_SUPPORT = "EXERCISE_WITHOUT_CONTENT_SUPPORT"
    DROPPED_GOAL_STILL_REFERENCED = "DROPPED_GOAL_STILL_REFERENCED"
    PUBLICATION_TASK_CLOSURE_FAILURE = "PUBLICATION_TASK_CLOSURE_FAILURE"
    CORRUPTED_TEXT = "CORRUPTED_TEXT"
    BROKEN_CRITICAL_SENTENCE = "BROKEN_CRITICAL_SENTENCE"
    PLACEHOLDER_LEAKAGE = "PLACEHOLDER_LEAKAGE"
    ABNORMAL_LANGUAGE_MIX = "ABNORMAL_LANGUAGE_MIX"
    SUSPICIOUS_DOMAIN_TERM = "SUSPICIOUS_DOMAIN_TERM"
    DUPLICATED_SENTENCE = "DUPLICATED_SENTENCE"
    EMPTY_OR_TRIVIAL_SECTION = "EMPTY_OR_TRIVIAL_SECTION"
    UNSUPPORTED_RENDERED_SOURCE_FACT = "UNSUPPORTED_RENDERED_SOURCE_FACT"
    MISSING_RENDER_ANCHOR = "MISSING_RENDER_ANCHOR"
    CROSS_OUTPUT_CONTENT_MISMATCH = "CROSS_OUTPUT_CONTENT_MISMATCH"
    WEAK_APPLICATION_CONTRIBUTION = "WEAK_APPLICATION_CONTRIBUTION"
    CONTENT_TOO_THIN = "CONTENT_TOO_THIN"
    REPAIR_HISTORY_INCOMPLETE = "REPAIR_HISTORY_INCOMPLETE"
    UNSUPPORTED_RENDERED_SEMANTIC_CLAIM = "UNSUPPORTED_RENDERED_SEMANTIC_CLAIM"
    PARTIALLY_SUPPORTED_RENDERED_SEMANTIC_CLAIM = "PARTIALLY_SUPPORTED_RENDERED_SEMANTIC_CLAIM"
    UNRESOLVED_RENDERED_SEMANTIC_CLAIM = "UNRESOLVED_RENDERED_SEMANTIC_CLAIM"
    INVALID_RENDERED_CLAIM_SEMANTIC_AUDIT = "INVALID_RENDERED_CLAIM_SEMANTIC_AUDIT"


def evaluate_publication_quality(
    *,
    markdown: str,
    digital_book: DigitalBook,
    coverage: WritingBriefCoverage,
    chunks: list[EvidenceChunk],
    semantic_closed_loop_passed: bool,
    plan_contractions: list[Any] | None = None,
    final_states: list[Any] | None = None,
    repair_attempts: list[Any] | None = None,
    materialization_audit: list[Any] | None = None,
    declared_rollback_count: int | None = None,
    rendered_claim_audit: Any | None = None,
) -> PublicationQualityReport:
    """Run deterministic publication QA against already materialized outputs."""
    fragments = _student_visible_fragments(markdown, digital_book)
    issues = _text_quality_issues(fragments)
    issues.extend(_dropped_goal_issues(digital_book, coverage))
    issues.extend(_output_alignment_issues(markdown, digital_book, coverage))
    issues.extend(_zero_render_output_issues(markdown, digital_book, coverage))

    claims = verify_rendered_evidence(
        markdown=markdown,
        digital_book=digital_book,
        briefs=coverage.briefs,
        evidence_by_id={item.chunk_id: item for item in chunks},
    )
    for claim in claims:
        if (
            claim.claim_type in {ClaimType.SOURCE_FACT, ClaimType.MIXED}
            and claim.support_status == SupportStatus.UNSUPPORTED
        ):
            issues.append(_issue(
                code=PublicationQualityCode.UNSUPPORTED_RENDERED_SOURCE_FACT,
                severity=PublicationSeverity.BLOCKER,
                location=f"{claim.target}:{claim.occurrence_id}",
                message="Rendered source fact has no support in the occurrence-authorized evidence set.",
                span=claim.source_span,
                targets=(claim.target,),
                occurrence_id=claim.occurrence_id,
                classification="writer_quality_bug",
                supporting_evidence_ids=claim.supporting_evidence_ids,
            ))

    semantic_audit_summary: dict[str, Any] = {}
    if rendered_claim_audit is not None:
        audit_payload = rendered_claim_audit.to_dict() if hasattr(rendered_claim_audit, "to_dict") else rendered_claim_audit
        if not isinstance(audit_payload, dict):
            audit_payload = {}
        semantic_audit_summary = dict(audit_payload.get("summary") or {})
        issues.extend(_rendered_claim_audit_issues(rendered_claim_audit))

    issues.extend(_weak_apply_issues(markdown, coverage))
    sufficiency, thin_issues = _pedagogical_sufficiency(markdown, digital_book, coverage)
    issues.extend(thin_issues)
    history = _repair_history(repair_attempts or [], materialization_audit or [])
    if declared_rollback_count is not None:
        actual_rollbacks = sum(item.final_disposition == "ROLLED_BACK" for item in history)
        if actual_rollbacks < declared_rollback_count:
            issues.append(_issue(
                code=PublicationQualityCode.REPAIR_HISTORY_INCOMPLETE,
                severity=PublicationSeverity.BLOCKER,
                location="publication_package:repair_history",
                message=f"Publication declares {declared_rollback_count} rollback(s), but only {actual_rollbacks} are auditable.",
                span="repair_history.json",
                targets=("publication_package",),
                classification="renderer_bug",
            ))

    report = PublicationQualityReport(
        issues=_dedupe_issues(issues),
        rendered_claims=claims,
        pedagogical_sufficiency=sufficiency,
        provenance=_build_provenance(coverage, plan_contractions or [], final_states or []),
        repair_history=history,
        rendered_claim_semantic_audit=semantic_audit_summary,
        semantic_closed_loop_status=PublicationQualityStatus.PASS if semantic_closed_loop_passed else PublicationQualityStatus.FAIL,
    )
    report.publication_quality_status = PublicationQualityStatus.FAIL if report.blockers else PublicationQualityStatus.PASS
    report.final_publication_status = (
        PublicationQualityStatus.PASS
        if report.semantic_closed_loop_status == PublicationQualityStatus.PASS
        and report.publication_quality_status == PublicationQualityStatus.PASS
        else PublicationQualityStatus.FAIL
    )
    return report


def integrate_rendered_claim_semantic_audit(*, report: PublicationQualityReport, rendered_claim_audit: Any) -> PublicationQualityReport:
    """Attach the final claim audit to an already materialized QA report."""
    payload = rendered_claim_audit.to_dict() if hasattr(rendered_claim_audit, "to_dict") else rendered_claim_audit
    report.rendered_claim_semantic_audit = dict(payload.get("summary") or {}) if isinstance(payload, dict) else {}
    report.issues = _dedupe_issues([*report.issues, *_rendered_claim_audit_issues(rendered_claim_audit)])
    report.publication_quality_status = PublicationQualityStatus.FAIL if report.blockers else PublicationQualityStatus.PASS
    report.final_publication_status = (
        PublicationQualityStatus.PASS
        if report.semantic_closed_loop_status == PublicationQualityStatus.PASS
        and report.publication_quality_status == PublicationQualityStatus.PASS
        else PublicationQualityStatus.FAIL
    )
    return report


def _rendered_claim_audit_issues(rendered_claim_audit: Any) -> list[PublicationQualityIssue]:
    issues: list[PublicationQualityIssue] = []
    for record in list(getattr(rendered_claim_audit, "records", ())):
        location = f"{record.occurrence_id}:{record.claim_id}"
        targets = ("markdown",)
        if not record.evidence_provenance_valid:
            issues.append(_issue(
                code=PublicationQualityCode.INVALID_RENDERED_CLAIM_SEMANTIC_AUDIT,
                severity=PublicationSeverity.BLOCKER,
                location=location,
                message="Rendered claim semantic audit failed evidence provenance/schema validation.",
                span=record.source_span or record.claim_text,
                targets=targets,
                occurrence_id=record.occurrence_id,
                classification="evidence_gate",
                rationale=record.error or record.rationale,
                supporting_evidence_ids=tuple(record.authorized_evidence_ids),
            ))
        elif record.final_status == "UNSUPPORTED":
            issues.append(_issue(
                code=PublicationQualityCode.UNSUPPORTED_RENDERED_SEMANTIC_CLAIM,
                severity=PublicationSeverity.BLOCKER,
                location=location,
                message="Rendered source-fact claim is not semantically supported by authorized evidence.",
                span=record.source_span or record.claim_text,
                targets=targets,
                occurrence_id=record.occurrence_id,
                classification="evidence_gate",
                rationale=record.rationale,
                supporting_evidence_ids=tuple(record.authorized_evidence_ids),
            ))
        elif record.final_status == "PARTIALLY_SUPPORTED":
            issues.append(_issue(
                code=PublicationQualityCode.PARTIALLY_SUPPORTED_RENDERED_SEMANTIC_CLAIM,
                severity=PublicationSeverity.BLOCKER,
                location=location,
                message="Rendered source-fact claim is only partially supported; review is required before publication.",
                span=record.source_span or record.claim_text,
                targets=targets,
                occurrence_id=record.occurrence_id,
                classification="evidence_gate",
                rationale=record.unsupported_part or record.rationale,
                supporting_evidence_ids=tuple(record.authorized_evidence_ids),
            ))
        elif record.final_status != "SUPPORTED":
            issues.append(_issue(
                code=PublicationQualityCode.UNRESOLVED_RENDERED_SEMANTIC_CLAIM,
                severity=PublicationSeverity.BLOCKER,
                location=location,
                message="Rendered source-fact claim has no accepted semantic entailment result.",
                span=record.source_span or record.claim_text,
                targets=targets,
                occurrence_id=record.occurrence_id,
                classification="evidence_gate",
                rationale=record.rationale,
                supporting_evidence_ids=tuple(record.authorized_evidence_ids),
            ))
    return issues


def write_publication_quality_artifacts(*, report: PublicationQualityReport, output_dir: Path) -> tuple[Path, Path, Path, Path]:
    output_dir = Path(output_dir)
    quality_json = output_dir / "publication_quality.json"
    quality_markdown = output_dir / "publication_quality.md"
    history_json = output_dir / "repair_history.json"
    history_markdown = output_dir / "repair_history.md"
    write_json(quality_json, report.to_dict())
    write_text(quality_markdown, render_publication_quality_markdown(report))
    write_json(history_json, [asdict(item) for item in report.repair_history])
    write_text(history_markdown, render_repair_history_markdown(report.repair_history))
    return quality_json, quality_markdown, history_json, history_markdown


def render_publication_quality_markdown(report: PublicationQualityReport) -> str:
    lines = [
        "# Publication Quality Report", "",
        f"- semantic_closed_loop_status: {report.semantic_closed_loop_status}",
        f"- publication_quality_status: {report.publication_quality_status}",
        f"- final_publication_status: {report.final_publication_status}",
        f"- blockers / high / warnings: {len(report.blockers)} / {len(report.high_severity)} / {len(report.warnings)}", "",
        "## Issues", "",
    ]
    if not report.issues:
        lines.append("- none")
    for issue in report.issues:
        lines.extend([
            f"### {issue.code} [{issue.severity}]",
            f"- location / outputs: {issue.location} / {', '.join(issue.affected_outputs)}",
            f"- occurrence: {issue.occurrence_id or 'n/a'}",
            f"- classification: {issue.classification or 'n/a'}",
            f"- message: {issue.message}",
            "- span:", "```text", issue.source_span or "(none)", "```", "",
        ])
    lines.extend(["## Plan / Render provenance", ""])
    for item in report.provenance:
        lines.append(f"- `{item.occurrence_id}`: {item.plan_status} / {item.render_status}")
    lines.extend(["", "## Pedagogical sufficiency", ""])
    for item in report.pedagogical_sufficiency:
        lines.append(f"- `{item.occurrence_id}` {item.role}: {item.body_character_count} chars, {item.sentence_count} sentences, {item.status}")
    return "\n".join(lines).rstrip() + "\n"


def render_repair_history_markdown(history: list[RepairHistoryEntry]) -> str:
    lines = ["# Repair History", ""]
    if not history:
        return "\n".join(lines + ["- no repair attempts recorded", ""])
    for item in history:
        lines.extend([
            f"## {item.occurrence_id}",
            f"- repair type / disposition: {item.repair_type} / {item.final_disposition}",
            f"- reason: {item.reason or 'none'}",
            "- before:", "```text", item.before or "(empty)", "```",
            "- candidate:", "```text", item.candidate or "(empty)", "```",
            "```diff", item.diff or "(no diff)", "```", "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _student_visible_fragments(markdown: str, book: DigitalBook) -> list[PublicationContentFragment]:
    fragments = [PublicationContentFragment("markdown", "markdown:title", "book_title", _markdown_title(markdown))]
    for index, heading in enumerate(re.findall(r"^#{1,6}\s+(.+)$", markdown, flags=re.MULTILINE), start=1):
        fragments.append(PublicationContentFragment("markdown", f"markdown:heading:{index}", "heading", heading))
    for record in extract_rendered_occurrences(markdown):
        fragments.append(PublicationContentFragment("markdown", f"markdown:{record.occurrence_id}", "occurrence", record.markdown, record.occurrence_id, record.section_id, record.task_id))
    fragments.extend([
        PublicationContentFragment("digital_book", "digital_book:title", "book_title", book.title),
        PublicationContentFragment("digital_book", "digital_book:general_preface", "general_preface", book.general_preface),
        PublicationContentFragment("digital_book", "digital_book:preface", "preface", book.preface),
    ])
    for project in book.projects:
        fragments.extend([
            PublicationContentFragment("digital_book", f"digital_book:{project.project_id}:title", "project_title", project.title),
            PublicationContentFragment("digital_book", f"digital_book:{project.project_id}:intro", "project_intro", project.project_intro),
            PublicationContentFragment("digital_book", f"digital_book:{project.project_id}:summary", "project_summary", project.project_summary),
        ])
        for task in project.tasks:
            fragments.append(PublicationContentFragment("digital_book", f"digital_book:{task.task_id}:title", "task_title", task.title, task_id=task.task_id, section_id=str(task.metadata.get("section_id", ""))))
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                occurrence_id = str(semantic.get("occurrence_id") or "") if isinstance(semantic, dict) else ""
                section_id = str(semantic.get("section_id") or task.metadata.get("section_id", "")) if isinstance(semantic, dict) else str(task.metadata.get("section_id", ""))
                fragments.append(PublicationContentFragment("digital_book", f"digital_book:{task.task_id}:{block.block_id}:title", f"{block.type}_title", block.title, occurrence_id, section_id, task.task_id))
                if block.markdown:
                    fragments.append(PublicationContentFragment("digital_book", f"digital_book:{task.task_id}:{block.block_id}", block.type, block.markdown, occurrence_id, section_id, task.task_id))
                if block.items:
                    fragments.append(PublicationContentFragment("digital_book", f"digital_book:{task.task_id}:{block.block_id}:items", block.type, "\n".join(block.items), occurrence_id, section_id, task.task_id))
    return [item for item in fragments if item.text.strip()]


def _text_quality_issues(fragments: list[PublicationContentFragment]) -> list[PublicationQualityIssue]:
    translation = {"BROKEN_SENTENCE": PublicationQualityCode.BROKEN_CRITICAL_SENTENCE}
    issues = []
    for finding in inspect_final_text(fragments):
        fragment = finding["fragment"]
        code = translation.get(str(finding["code"]), str(finding["code"]))
        issues.append(_issue(
            code=code,
            severity=str(finding["severity"]),
            location=fragment.location,
            message=_message_for_text_code(code),
            span=str(finding["span"]),
            targets=(fragment.target,),
            component=fragment.component,
            occurrence_id=fragment.occurrence_id,
            classification=_classification_for_text_code(code),
        ))
    return issues


def _dropped_goal_issues(book: DigitalBook, coverage: WritingBriefCoverage) -> list[PublicationQualityIssue]:
    issues = []
    for finding in inspect_dropped_goal_reconciliation(digital_book=book, dropped_goals=coverage.dropped_occurrence_goals):
        issues.append(_issue(
            code=str(finding["code"]), severity=str(finding["severity"]), location=str(finding["location"]),
            message=str(finding["message"]), span=str(finding["span"]), targets=("digital_book",),
            occurrence_id=str(finding["occurrence_id"]), classification=str(finding["classification"]),
        ))
    return issues


def _output_alignment_issues(markdown: str, book: DigitalBook, coverage: WritingBriefCoverage) -> list[PublicationQualityIssue]:
    markdown_by_id = {item.occurrence_id: item for item in extract_rendered_occurrences(markdown)}
    digital_by_id: dict[str, str] = {}
    for fragment in _student_visible_fragments("", book):
        if fragment.target == "digital_book" and fragment.occurrence_id and fragment.component == "implementation":
            digital_by_id[fragment.occurrence_id] = fragment.text
    issues = []
    for brief in coverage.briefs:
        md = markdown_by_id.get(brief.occurrence_id)
        db = digital_by_id.get(brief.occurrence_id)
        if md is None or db is None:
            missing = "Markdown" if md is None else "DigitalBook"
            issues.append(_issue(
                code=PublicationQualityCode.MISSING_RENDER_ANCHOR,
                severity=PublicationSeverity.BLOCKER,
                location=f"render:{brief.occurrence_id}",
                message=f"{missing} is missing the required code-owned rendered occurrence.",
                span=brief.occurrence_id,
                targets=("markdown", "digital_book"), occurrence_id=brief.occurrence_id,
                classification="renderer_bug",
            ))
        elif _normalise(md.markdown) != _normalise(db):
            issues.append(_issue(
                code=PublicationQualityCode.CROSS_OUTPUT_CONTENT_MISMATCH,
                severity=PublicationSeverity.BLOCKER,
                location=f"render:{brief.occurrence_id}",
                message="Markdown and DigitalBook render different student-visible occurrence bodies.",
                span=f"Markdown: {md.markdown}\nDigitalBook: {db}",
                targets=("markdown", "digital_book"), occurrence_id=brief.occurrence_id,
                classification="renderer_bug",
            ))
    return issues


def _zero_render_output_issues(markdown: str, book: DigitalBook, coverage: WritingBriefCoverage) -> list[PublicationQualityIssue]:
    """Fail only when an explicit zero-render audit disagrees with output bodies."""
    markdown_ids = {item.occurrence_id for item in extract_rendered_occurrences(markdown)}
    digital_ids = {
        str((block.metadata.get("semantic_occurrence") or {}).get("occurrence_id"))
        for project in book.projects
        for task in project.tasks
        for block in task.blocks
        if isinstance(block.metadata.get("semantic_occurrence") if block.metadata else None, dict)
    }
    audited_ids = {
        str(item.get("occurrence_id"))
        for item in book.metadata.get("semantic_zero_render_occurrences", [])
        if isinstance(item, dict) and item.get("occurrence_id")
    }
    issues = []
    for zero in coverage.zero_render_occurrences:
        if zero.occurrence_id not in audited_ids:
            issues.append(_issue(
                code=PublicationQualityCode.MISSING_RENDER_ANCHOR,
                severity=PublicationSeverity.BLOCKER,
                location=f"zero-render:{zero.occurrence_id}",
                message="Explicit ZERO_RENDER occurrence is missing its DigitalBook audit record.",
                span=zero.occurrence_id,
                targets=("digital_book",),
                occurrence_id=zero.occurrence_id,
                classification="renderer_bug",
            ))
        if zero.occurrence_id in markdown_ids or zero.occurrence_id in digital_ids:
            issues.append(_issue(
                code=PublicationQualityCode.CROSS_OUTPUT_CONTENT_MISMATCH,
                severity=PublicationSeverity.BLOCKER,
                location=f"zero-render:{zero.occurrence_id}",
                message="Explicit ZERO_RENDER occurrence unexpectedly has a student-visible body.",
                span=zero.occurrence_id,
                targets=("markdown", "digital_book"),
                occurrence_id=zero.occurrence_id,
                classification="renderer_bug",
            ))
    return issues


def _weak_apply_issues(markdown: str, coverage: WritingBriefCoverage) -> list[PublicationQualityIssue]:
    bodies = {item.occurrence_id: item.markdown for item in extract_rendered_occurrences(markdown)}
    issues = []
    for brief in coverage.briefs:
        if brief.role != "APPLY":
            continue
        body = bodies.get(brief.occurrence_id, "")
        has_context = bool(re.search(r"本任务|当前.*任务|任务中|current\s+task", body, re.IGNORECASE))
        has_relation = bool(re.search(r"使用|应用|依据|根据|用于|基础上|指导下|结合|通过|apply|use", body, re.IGNORECASE))
        # A title such as “引弧方法” already names the method.  Require the
        # title plus a concrete current-task action, not a duplicated noun.
        has_specific_mapping = bool(re.search(re.escape(brief.canonical_title), body)) and bool(
            re.search(r"完成|调整|控制|观察|检查|实施|焊接|执行|操作|apply|perform", body, re.IGNORECASE)
        )
        generic = bool(re.search(r"(?:之前|前文|已掌握).{0,8}(?:相关)?知识|相关知识", body))
        # “前文知识” is insufficient only when it is the entire relation.
        # A named knowledge point plus a concrete task action is a valid
        # application even if it also refers to prior learning.
        if not (has_context and has_relation and has_specific_mapping) or (generic and not has_specific_mapping):
            issues.append(_issue(
                code=PublicationQualityCode.WEAK_APPLICATION_CONTRIBUTION,
                severity=PublicationSeverity.HIGH,
                location=f"markdown:{brief.occurrence_id}",
                message="APPLY text lacks a concrete prior-knowledge → current-action mapping.",
                span=body,
                targets=("markdown", "digital_book"), occurrence_id=brief.occurrence_id,
                classification="writer_quality_bug",
            ))
    return issues


def _pedagogical_sufficiency(markdown: str, book: DigitalBook, coverage: WritingBriefCoverage) -> tuple[list[PedagogicalSufficiencyRecord], list[PublicationQualityIssue]]:
    bodies = {item.occurrence_id: item.markdown for item in extract_rendered_occurrences(markdown)}
    records = []
    issues = []
    for brief in coverage.briefs:
        body = bodies.get(brief.occurrence_id, "")
        visible = re.sub(r"\bEvidence\s*:\s*C\d+\b", "", body, flags=re.IGNORECASE).strip()
        sentences = [item for item in re.split(r"(?<=[。！？.!?])\s*|\n+", visible) if item.strip()]
        chars = len(re.sub(r"\s+", "", visible))
        explanation = bool(re.search(r"原理|作用|因为|通过|定义|影响|explain|affects", visible, re.IGNORECASE))
        steps = len(re.findall(r"第[一二三四五六七八九十\d]+步|步骤|step\s*\d+", visible, re.IGNORECASE))
        example = bool(re.search(r"例如|比如|案例|示例|for example", visible, re.IGNORECASE))
        task = _digital_task_for_occurrence(book, brief.occurrence_id)
        exercise_support = bool(task and any(block.type == "exercises" and block.items for block in task.blocks))
        density = round(chars / max(1, len(sentences)), 2)
        thin = chars < (100 if brief.role == "TEACH" else 45) or (brief.role == "TEACH" and len(sentences) < 2)
        record = PedagogicalSufficiencyRecord(
            occurrence_id=brief.occurrence_id, role=brief.role, body_character_count=chars,
            sentence_count=len(sentences), teach_explanation_coverage=explanation, procedure_step_count=steps,
            example_present=example, exercise_support_coverage=exercise_support,
            role_specific_density=density, status="CONTENT_TOO_THIN" if thin else "ADEQUATE",
        )
        records.append(record)
        if thin:
            issues.append(_issue(
                code=PublicationQualityCode.CONTENT_TOO_THIN, severity=PublicationSeverity.WARNING,
                location=f"markdown:{brief.occurrence_id}",
                message="Rendered occurrence is thin for its teaching role; this is a reporting warning, not a hard threshold.",
                span=body, targets=("markdown", "digital_book"), occurrence_id=brief.occurrence_id,
                classification="writer_quality_bug",
            ))
    return records, issues


def _repair_history(attempts: list[Any], audits: list[Any]) -> list[RepairHistoryEntry]:
    entries = [_repair_entry(item, source="attempt") for item in attempts]
    entries.extend(_repair_entry(item, source="materialization") for item in audits)
    return [item for item in entries if item is not None]


def _repair_entry(value: Any, *, source: str) -> RepairHistoryEntry | None:
    item = _as_dict(value)
    if not item or not item.get("occurrence_id"):
        return None
    status = str(item.get("status") or "")
    disposition = "ACCEPTED" if status in {"ACCEPTED", "MATERIALIZED"} else "ROLLED_BACK" if status == "ROLLED_BACK" else "REJECTED" if "REJECT" in status else "SKIPPED"
    return RepairHistoryEntry(
        occurrence_id=str(item["occurrence_id"]),
        repair_type=str(item.get("repair_kind") or item.get("repair_action") or ",".join(item.get("actions") or []) or source),
        reason=str(item.get("rollback_reason") or item.get("reason") or ""),
        before=str(item.get("before_text") or item.get("original_text") or ""),
        candidate=str(item.get("after_text") or item.get("candidate_text") or item.get("generated_text") or ""),
        diff=str(item.get("diff") or ""),
        post_check_result=item.get("post_conformance") or item.get("post_check_result"),
        final_disposition=disposition,
    )


def _build_provenance(coverage: WritingBriefCoverage, contractions: list[Any], final_states: list[Any]) -> list[PublicationProvenanceRecord]:
    contracted = {str(_as_dict(item).get("occurrence_id")) for item in contractions if _as_dict(item).get("status") == "EVIDENCE_BOUNDED_AUTO_CONTRACTION"}
    state_by_id = {str(_as_dict(item).get("occurrence_id")): str(_as_dict(item).get("status")) for item in final_states}
    records = []
    for brief in coverage.briefs:
        state = state_by_id.get(brief.occurrence_id, "")
        render = "VERIFIED_REPAIRED" if state == "VERIFIED_REPAIRED" else "VERIFIED_AS_GENERATED" if state == "VERIFIED_ORIGINAL" else "ROLLED_BACK" if state == "FAILED_CONFORMANCE" else "NOT_RENDERED"
        records.append(PublicationProvenanceRecord(brief.occurrence_id, "CONTRACTED_PLAN" if brief.occurrence_id in contracted else "UNCHANGED_PLAN", render))
    records.extend(PublicationProvenanceRecord(item.occurrence_id, "DROPPED_GOAL", "NOT_RENDERED") for item in coverage.dropped_occurrence_goals)
    records.extend(PublicationProvenanceRecord(item.occurrence_id, "UNCHANGED_PLAN", "ZERO_RENDERED") for item in coverage.zero_render_occurrences)
    return records


def _digital_task_for_occurrence(book: DigitalBook, occurrence_id: str):
    for project in book.projects:
        for task in project.tasks:
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                if isinstance(semantic, dict) and semantic.get("occurrence_id") == occurrence_id:
                    return task
    return None


def _issue(*, code: str, severity: str, location: str, message: str, span: str, targets: tuple[str, ...], component: str = "", occurrence_id: str = "", classification: str = "", rationale: str = "", supporting_evidence_ids: tuple[str, ...] = ()) -> PublicationQualityIssue:
    digest = sha256(f"{code}|{location}|{span}".encode("utf-8")).hexdigest()[:12]
    return PublicationQualityIssue(
        issue_id=f"pqa:{code.lower()}:{digest}", code=code, severity=severity, location=location,
        message=message, source_span=span, affected_outputs=targets, component=component,
        occurrence_id=occurrence_id, classification=classification, rationale=rationale,
        supporting_evidence_ids=supporting_evidence_ids,
    )


def _dedupe_issues(items: list[PublicationQualityIssue]) -> list[PublicationQualityIssue]:
    grouped: dict[tuple[str, str], PublicationQualityIssue] = {}
    for item in items:
        key = (item.code, item.source_span)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = item
        else:
            outputs = tuple(dict.fromkeys([*existing.affected_outputs, *item.affected_outputs]))
            grouped[key] = PublicationQualityIssue(**{**asdict(existing), "affected_outputs": outputs})
    return list(grouped.values())


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    return {}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _markdown_title(markdown: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _message_for_text_code(code: str) -> str:
    messages = {
        PublicationQualityCode.INTERNAL_LABEL_LEAKAGE: "Internal/test/debug label is visible to students.",
        PublicationQualityCode.CORRUPTED_TEXT: "Rendered text contains deterministic corruption markers.",
        PublicationQualityCode.BROKEN_CRITICAL_SENTENCE: "A critical instructional sentence is not readable.",
        PublicationQualityCode.PLACEHOLDER_LEAKAGE: "Placeholder text is visible to students.",
        PublicationQualityCode.ABNORMAL_LANGUAGE_MIX: "Student-visible text has abnormal language mixing.",
        PublicationQualityCode.SUSPICIOUS_DOMAIN_TERM: "Rendered domain term is suspicious and should be reviewed.",
        PublicationQualityCode.DUPLICATED_SENTENCE: "The same sentence appears in multiple student-visible locations.",
        PublicationQualityCode.EMPTY_OR_TRIVIAL_SECTION: "Student-visible section is empty or trivial.",
    }
    return messages.get(code, code)


def _classification_for_text_code(code: str) -> str:
    if code in {PublicationQualityCode.CORRUPTED_TEXT, PublicationQualityCode.BROKEN_CRITICAL_SENTENCE, PublicationQualityCode.SUSPICIOUS_DOMAIN_TERM}:
        return "upstream_source_bug"
    if code in {PublicationQualityCode.INTERNAL_LABEL_LEAKAGE, PublicationQualityCode.PLACEHOLDER_LEAKAGE}:
        return "renderer_bug"
    return "writer_quality_bug"
