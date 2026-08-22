"""Phase 3B: deterministic targeted deletion with full conformance re-check.

This module intentionally has no LLM, writer, exporter, BookPlan, or semantic
planner dependency.  It produces in-memory candidates and audit records only;
callers decide whether an ACCEPTED candidate is later materialized.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import difflib
import re

from materials2textbook.knowledge_map.repair_proposals import RepairAction, RepairProposal
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    RenderedConformanceResult,
    RenderedOccurrence,
    check_rendered_occurrence_records,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief


class RepairAttemptStatus:
    ACCEPTED = "ACCEPTED"
    ROLLED_BACK = "ROLLED_BACK"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class RemovedSpan:
    start_offset: int
    end_offset: int
    text: str
    violation_ids: tuple[str, ...]


@dataclass(frozen=True)
class RepairAttempt:
    """Full audit record for one synchronized Markdown/DigitalBook candidate."""

    occurrence_id: str
    original_text: str
    targeted_violation_ids: tuple[str, ...]
    removed_spans: tuple[RemovedSpan, ...]
    candidate_text: str
    diff: str
    pre_conformance: dict[str, RenderedConformanceResult | None]
    post_conformance: dict[str, RenderedConformanceResult | None]
    status: str
    rollback_reason: str = ""
    executed_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        def result(value: RenderedConformanceResult | None):
            return asdict(value) if value else None

        return {
            "occurrence_id": self.occurrence_id,
            "original_text": self.original_text,
            "targeted_violation_ids": list(self.targeted_violation_ids),
            "removed_spans": [asdict(item) for item in self.removed_spans],
            "candidate_text": self.candidate_text,
            "diff": self.diff,
            "pre_conformance": {key: result(value) for key, value in self.pre_conformance.items()},
            "post_conformance": {key: result(value) for key, value in self.post_conformance.items()},
            "status": self.status,
            "rollback_reason": self.rollback_reason,
            "executed_actions": list(self.executed_actions),
        }


@dataclass(frozen=True)
class SynchronizedRepairResult:
    """Accepted candidates are identical across output targets by construction."""

    attempt: RepairAttempt
    markdown_candidate: RenderedOccurrence | None
    digital_book_candidate: RenderedOccurrence | None


def execute_synchronized_safe_repair(
    *,
    brief: OccurrenceWritingBrief,
    proposal: RepairProposal,
    markdown_rendered: RenderedOccurrence | None,
    digital_book_rendered: RenderedOccurrence | None,
) -> SynchronizedRepairResult:
    """Delete only checker-cited spans and accept only a dual MATCH result.

    The candidate body is created once from an identical Markdown/DigitalBook
    source body and copied to both targets.  This prevents per-renderer repair
    drift.  Original records, briefs and proposals remain untouched.
    """
    original = markdown_rendered.markdown if markdown_rendered else ""
    empty = _attempt(
        occurrence_id=brief.occurrence_id,
        original_text=original,
        targeted=(),
        spans=(),
        candidate=original,
        pre={"markdown": None, "digital_book": None},
        post={"markdown": None, "digital_book": None},
        status=RepairAttemptStatus.ROLLED_BACK,
        rollback_reason="",
    )
    if proposal.occurrence_id != brief.occurrence_id:
        return SynchronizedRepairResult(
            _with_reason(empty, "PROPOSAL_BRIEF_OCCURRENCE_MISMATCH"), None, None,
        )
    if not markdown_rendered or not digital_book_rendered:
        return SynchronizedRepairResult(_with_reason(empty, "ANCHOR_MISMATCH_OR_MISSING_TARGET"), None, None)
    if not _same_anchor(markdown_rendered, digital_book_rendered, brief.occurrence_id):
        return SynchronizedRepairResult(_with_reason(empty, "ANCHOR_MISMATCH_OR_MISSING_TARGET"), None, None)

    pre = _check_targets(brief, markdown_rendered, digital_book_rendered)
    if any(value is None for value in pre.values()):
        return SynchronizedRepairResult(_with_reason(_replace_attempt(empty, pre_conformance=pre), "PRE_CHECKER_ERROR"), None, None)
    if markdown_rendered.markdown != digital_book_rendered.markdown:
        return SynchronizedRepairResult(_with_reason(_replace_attempt(empty, pre_conformance=pre), "RENDER_TARGET_TEXT_MISMATCH"), None, None)
    if RepairAction.REMOVE_RETEACH not in proposal.actions:
        return SynchronizedRepairResult(
            _replace_attempt(empty, pre_conformance=pre, status=RepairAttemptStatus.SKIPPED, rollback_reason="UNSAFE_PROPOSAL_NO_REMOVE_RETEACH"), None, None,
        )

    violations = pre["markdown"].forbidden_reteach_violation
    spans = spans_for_checker_violations(markdown_rendered.markdown, violations)
    if not spans:
        return SynchronizedRepairResult(_with_reason(_replace_attempt(empty, pre_conformance=pre), "NO_EXACT_CHECKER_SPAN"), None, None)
    candidate_text = _delete_spans(markdown_rendered.markdown, spans)
    candidate_markdown = replace(markdown_rendered, markdown=candidate_text)
    candidate_digital = replace(digital_book_rendered, markdown=candidate_text)
    post = _check_targets(brief, candidate_markdown, candidate_digital)
    attempt = _attempt(
        occurrence_id=brief.occurrence_id,
        original_text=markdown_rendered.markdown,
        targeted=tuple(violation_id for span in spans for violation_id in span.violation_ids),
        spans=tuple(spans),
        candidate=candidate_text,
        pre=pre,
        post=post,
        status=RepairAttemptStatus.ROLLED_BACK,
        rollback_reason="",
        executed=(RepairAction.REMOVE_RETEACH,),
    )
    if any(value is None for value in post.values()):
        return SynchronizedRepairResult(_with_reason(attempt, "POST_CHECKER_ERROR"), None, None)
    if not _has_substantive_content(candidate_text):
        return SynchronizedRepairResult(_with_reason(attempt, "EMPTY_OR_HEADING_ONLY_AFTER_REMOVAL"), None, None)
    if any(value.overall != ConformanceStatus.MATCH for value in post.values()):
        statuses = ",".join(f"{key}:{value.overall}" for key, value in post.items() if value)
        return SynchronizedRepairResult(_with_reason(attempt, f"POST_CONFORMANCE_NOT_MATCH:{statuses}"), None, None)
    accepted = _replace_attempt(attempt, status=RepairAttemptStatus.ACCEPTED)
    return SynchronizedRepairResult(accepted, candidate_markdown, candidate_digital)


def spans_for_checker_violations(text: str, violations) -> list[RemovedSpan]:
    """Locate exact checker sentences and merge overlapping spans stably."""
    raw: list[RemovedSpan] = []
    search_from = 0
    for index, violation in enumerate(violations):
        violation_id = f"violation:{index}:{violation.rule}"
        start = text.find(violation.sentence, search_from)
        if start < 0:
            # A checker sentence is required to be a literal rendered span.
            # Do not guess a fuzzy deletion target.
            return []
        end = start + len(violation.sentence)
        raw.append(RemovedSpan(start, end, text[start:end], (violation_id,)))
        search_from = end
    return [
        replace(span, text=text[span.start_offset:span.end_offset])
        for span in merge_overlapping_spans(raw)
    ]


def merge_overlapping_spans(spans: list[RemovedSpan]) -> list[RemovedSpan]:
    """Sort then union overlapping/touching exact spans while retaining IDs."""
    if not spans:
        return []
    merged: list[RemovedSpan] = []
    for span in sorted(spans, key=lambda item: (item.start_offset, item.end_offset, item.violation_ids)):
        if not merged or span.start_offset > merged[-1].end_offset:
            merged.append(span)
            continue
        previous = merged[-1]
        start = previous.start_offset
        end = max(previous.end_offset, span.end_offset)
        text = previous.text if previous.start_offset == start and previous.end_offset == end else ""
        merged[-1] = RemovedSpan(
            start,
            end,
            text,
            tuple(dict.fromkeys([*previous.violation_ids, *span.violation_ids])),
        )
    return merged


def render_repair_attempt_report_markdown(attempts: list[RepairAttempt]) -> str:
    lines = ["# Safe Auto Repair Audit", "", "Only exact checker-cited spans were considered; no LLM rewrite was called.", ""]
    for attempt in attempts:
        lines.extend([
            f"## {attempt.occurrence_id}",
            f"- status: {attempt.status}",
            f"- executed actions: {', '.join(attempt.executed_actions) or 'none'}",
            f"- targeted violations: {', '.join(attempt.targeted_violation_ids) or 'none'}",
            f"- removed spans: {len(attempt.removed_spans)}",
            f"- rollback reason: {attempt.rollback_reason or 'none'}",
            f"- pre conformance: {_conformance_summary(attempt.pre_conformance)}",
            f"- post conformance: {_conformance_summary(attempt.post_conformance)}",
            "- original text:",
            "```text",
            attempt.original_text or "(empty)",
            "```",
            "- candidate text:",
            "```text",
            attempt.candidate_text or "(empty)",
            "```",
            "```diff",
            attempt.diff or "(no text change)",
            "```",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _conformance_summary(values: dict[str, RenderedConformanceResult | None]) -> str:
    return ", ".join(f"{target}={result.overall if result else 'CHECKER_ERROR'}" for target, result in values.items()) or "none"


def _same_anchor(left: RenderedOccurrence, right: RenderedOccurrence, expected_id: str) -> bool:
    return (
        left.occurrence_id == right.occurrence_id == expected_id
        and left.chapter_id == right.chapter_id
        and left.section_id == right.section_id
    )


def _check_targets(
    brief: OccurrenceWritingBrief,
    markdown: RenderedOccurrence,
    digital: RenderedOccurrence,
) -> dict[str, RenderedConformanceResult | None]:
    try:
        return {
            "markdown": check_rendered_occurrence_records([brief], [markdown]).results[0],
            "digital_book": check_rendered_occurrence_records([brief], [digital]).results[0],
        }
    except Exception:
        return {"markdown": None, "digital_book": None}


def _delete_spans(text: str, spans: list[RemovedSpan]) -> str:
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor:span.start_offset])
        cursor = span.end_offset
    parts.append(text[cursor:])
    return "".join(parts)


def _has_substantive_content(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("Evidence:"):
            continue
        if re.sub(r"[`*_>#\-\s]", "", stripped):
            return True
    return False


def _attempt(
    *, occurrence_id, original_text, targeted, spans, candidate, pre, post, status, rollback_reason, executed=()
) -> RepairAttempt:
    diff = "\n".join(difflib.unified_diff(
        original_text.splitlines(), candidate.splitlines(), fromfile="before", tofile="candidate", lineterm="",
    ))
    return RepairAttempt(
        occurrence_id=occurrence_id,
        original_text=original_text,
        targeted_violation_ids=tuple(targeted),
        removed_spans=tuple(spans),
        candidate_text=candidate,
        diff=diff,
        pre_conformance=pre,
        post_conformance=post,
        status=status,
        rollback_reason=rollback_reason,
        executed_actions=tuple(executed),
    )


def _replace_attempt(attempt: RepairAttempt, **changes) -> RepairAttempt:
    return replace(attempt, **changes)


def _with_reason(attempt: RepairAttempt, reason: str) -> RepairAttempt:
    return _replace_attempt(attempt, status=RepairAttemptStatus.ROLLED_BACK, rollback_reason=reason)
