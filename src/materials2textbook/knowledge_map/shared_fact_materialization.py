"""Phase 3B-3 controlled materialization for one COMPRESSIBLE shared fact.

This module is deliberately occurrence-local.  It accepts one already audited
compression plan, creates one candidate body, runs both renderer conformance
and evidence/closure gates, and returns new in-memory outputs only after every
gate passes.  It never edits the caller's Markdown/DigitalBook objects and it
never handles contextual shared facts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import difflib
import re
from typing import Any, Callable, Mapping, Protocol

from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    RenderedConformanceResult,
    RenderedOccurrence,
    check_rendered_occurrence_records,
)
from materials2textbook.knowledge_map.rendered_evidence_verification import (
    SupportStatus,
    verify_rendered_evidence,
)
from materials2textbook.knowledge_map.shared_fact_compression import (
    COMPRESSIBLE,
    SharedFactCompressionPlan,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.schemas import DigitalBook, EvidenceChunk


ACCEPTED = "ACCEPTED"
ROLLED_BACK = "ROLLED_BACK"
SKIPPED = "SKIPPED"


class SharedFactPatchGenerator(Protocol):
    def generate(
        self,
        *,
        original_text: str,
        shared_fact: str,
        earlier_verified_support: Mapping[str, Any],
        later_unique_contribution: str,
        allowed_actions: tuple[str, ...],
        forbidden_actions: tuple[str, ...],
        max_recap_scope: str,
        required_evidence_ids: tuple[str, ...],
        fact_level_constraints: Mapping[str, Any],
        brief: OccurrenceWritingBrief,
    ) -> str:
        """Return only a candidate for the current occurrence body."""


@dataclass(frozen=True)
class SharedFactMaterializationAttempt:
    shared_fact_id: str
    compression_plan_id: str
    occurrence_id: str
    original_text: str
    candidate_text: str
    diff: str
    removed_shared_spans: tuple[str, ...]
    retained_unique_contribution: str
    pre_conformance: dict[str, Any]
    post_conformance: dict[str, Any]
    pre_evidence: dict[str, Any]
    post_evidence: dict[str, Any]
    pre_downstream_closure: dict[str, Any]
    post_downstream_closure: dict[str, Any]
    markdown_materialization: dict[str, Any]
    digital_book_materialization: dict[str, Any]
    final_decision: str
    rollback_reason: str = ""
    unique_contribution_retention: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SharedFactMaterializationResult:
    attempt: SharedFactMaterializationAttempt
    markdown_candidate: str | None = None
    digital_book_candidate: DigitalBook | None = None

    def to_dict(self) -> dict[str, Any]:
        value = self.attempt.to_dict()
        value["has_markdown_candidate"] = self.markdown_candidate is not None
        value["has_digital_book_candidate"] = self.digital_book_candidate is not None
        return value


def skipped_shared_fact_materialization(
    *,
    shared_fact_id: str,
    compression_plan_id: str,
    occurrence_id: str,
    reason: str,
) -> SharedFactMaterializationResult:
    """Create an explicit SKIPPED audit when an opt-in request cannot resolve."""
    attempt = SharedFactMaterializationAttempt(
        shared_fact_id=shared_fact_id,
        compression_plan_id=compression_plan_id,
        occurrence_id=occurrence_id,
        original_text="",
        candidate_text="",
        diff="",
        removed_shared_spans=(),
        retained_unique_contribution="",
        pre_conformance={},
        post_conformance={},
        pre_evidence={},
        post_evidence={},
        pre_downstream_closure={},
        post_downstream_closure={},
        markdown_materialization={},
        digital_book_materialization={},
        final_decision=SKIPPED,
        rollback_reason=reason,
    )
    return SharedFactMaterializationResult(attempt=attempt)


def materialize_compressible_shared_fact(
    *,
    plan: SharedFactCompressionPlan,
    brief: OccurrenceWritingBrief,
    markdown_document: str,
    digital_book: DigitalBook,
    markdown_rendered: RenderedOccurrence | None,
    digital_book_rendered: RenderedOccurrence | None,
    evidence_by_id: Mapping[str, EvidenceChunk] | None = None,
    baseline_downstream_closure: Any | None = None,
    downstream_rechecker: Callable[[str, DigitalBook], Any] | None = None,
    shared_fact_span: str | None = None,
    patch_generator: SharedFactPatchGenerator | None = None,
    preflight: Callable[[], bool] | None = None,
    conformance_checker: Callable[[OccurrenceWritingBrief, RenderedOccurrence, RenderedOccurrence], dict[str, Any]] | None = None,
    evidence_checker: Callable[[str, DigitalBook, OccurrenceWritingBrief], dict[str, Any]] | None = None,
    materialization_writer: Callable[[str, DigitalBook], tuple[bool, bool]] | None = None,
) -> SharedFactMaterializationResult:
    """Attempt one exact, dual-render shared-fact compression.

    ``shared_fact_span`` is intentionally exact.  If it is not present, a
    caller may supply a constrained local patch generator; a fuzzy replacement
    is never attempted.
    """
    original = markdown_rendered.markdown if markdown_rendered else ""
    pre_conformance: dict[str, Any] = {}
    pre_evidence: dict[str, Any] = {}
    pre_closure = _closure_snapshot(baseline_downstream_closure)
    empty = _attempt(
        plan=plan,
        brief=brief,
        original=original,
        candidate=original,
        removed=(),
        pre_conformance=pre_conformance,
        post_conformance={},
        pre_evidence=pre_evidence,
        post_evidence={},
        pre_closure=pre_closure,
        post_closure={},
        markdown_materialization={},
        digital_materialization={},
        decision=SKIPPED,
    )
    if plan.disposition != COMPRESSIBLE:
        return _with_reason(empty, "ONLY_COMPRESSIBLE_DISPOSITION_IS_ELIGIBLE", SKIPPED)
    if brief.occurrence_id != plan.later_occurrence_id:
        return _with_reason(empty, "PLAN_BRIEF_OCCURRENCE_MISMATCH", SKIPPED)
    if preflight is not None and not preflight():
        return _with_reason(empty, "PREFLIGHT_REJECTED", SKIPPED)
    if not markdown_rendered or not digital_book_rendered:
        return _with_reason(empty, "MISSING_OCCURRENCE_TARGET", SKIPPED)
    if not _same_anchor(markdown_rendered, digital_book_rendered, brief.occurrence_id):
        return _with_reason(empty, "ANCHOR_MISMATCH", SKIPPED)
    digital_block = _find_digital_block(digital_book, brief.occurrence_id)
    if digital_block is None:
        return _with_reason(empty, "DIGITAL_BOOK_OCCURRENCE_BLOCK_MISSING_OR_AMBIGUOUS", SKIPPED)
    if markdown_rendered.markdown != digital_book_rendered.markdown or markdown_rendered.markdown != digital_block.markdown:
        return _with_reason(empty, "BASELINE_TEXT_MISMATCH", SKIPPED)

    pre_conformance = _run_conformance(brief, markdown_rendered, digital_book_rendered, conformance_checker)
    pre_evidence = _run_evidence(
        markdown_document=markdown_document,
        digital_book=digital_book,
        brief=brief,
        evidence_by_id=evidence_by_id,
        checker=evidence_checker,
    )
    pre_check_error = _precheck_error(pre_conformance, pre_evidence)
    if pre_check_error:
        return _with_reason(
            _replace_attempt(empty, pre_conformance=pre_conformance, pre_evidence=pre_evidence),
            pre_check_error,
            SKIPPED,
        )
    if baseline_downstream_closure is None or downstream_rechecker is None:
        return _with_reason(
            _replace_attempt(empty, pre_conformance=pre_conformance, pre_evidence=pre_evidence),
            "DOWNSTREAM_RECHECK_REQUIRED",
            SKIPPED,
        )

    candidate, removed_spans, generation_reason = _candidate_body(
        plan=plan,
        brief=brief,
        original=original,
        shared_fact_span=shared_fact_span,
        patch_generator=patch_generator,
    )
    if generation_reason:
        return _with_reason(
            _replace_attempt(empty, pre_conformance=pre_conformance, pre_evidence=pre_evidence),
            generation_reason,
            SKIPPED,
        )
    effective_span = shared_fact_span or plan.shared_fact_statement
    if patch_generator is not None and effective_span and effective_span in candidate:
        return _with_reason(
            _replace_attempt(
                empty,
                original_text=original,
                candidate_text=candidate,
                diff=_diff(original, candidate),
                removed_shared_spans=tuple(removed_spans),
                retained_unique_contribution=plan.later_unique_contribution,
                pre_conformance=pre_conformance,
                pre_evidence=pre_evidence,
            ),
            "SHARED_FACT_NOT_COMPRESSED_TO_ALLOWED_SCOPE",
            ROLLED_BACK,
        )

    markdown_candidate_record = replace(markdown_rendered, markdown=candidate)
    digital_candidate_record = replace(digital_book_rendered, markdown=candidate)
    candidate_book = deepcopy(digital_book)
    candidate_block = _find_digital_block(candidate_book, brief.occurrence_id)
    if candidate_block is None:
        return _with_reason(empty, "DIGITAL_BOOK_BLOCK_DISAPPEARED_BEFORE_PATCH", ROLLED_BACK)
    candidate_block.markdown = candidate
    candidate_document = _replace_markdown_occurrence(markdown_document, markdown_rendered, candidate)

    post_conformance = _run_conformance(brief, markdown_candidate_record, digital_candidate_record, conformance_checker)
    retention = _verify_unique_contribution_retention(brief, post_conformance)
    if retention["status"] != "PASS":
        reason = (
            "UNIQUE_CONTRIBUTION_NOT_RETAINED"
            if retention["status"] == "FAIL"
            else "UNIQUE_CONTRIBUTION_RETENTION_UNRESOLVED"
        )
        attempt = _attempt(
            plan=plan,
            brief=brief,
            original=original,
            candidate=candidate,
            removed=tuple(removed_spans),
            pre_conformance=pre_conformance,
            post_conformance=post_conformance,
            pre_evidence=pre_evidence,
            post_evidence={},
            pre_closure=pre_closure,
            post_closure={},
            markdown_materialization={"before_text_exact_match": True},
            digital_materialization={"before_text_exact_match": True},
            decision=ROLLED_BACK,
            rollback_reason=reason,
            unique_contribution_retention=retention,
        )
        return SharedFactMaterializationResult(attempt=attempt)
    post_evidence = _run_evidence(
        markdown_document=candidate_document,
        digital_book=candidate_book,
        brief=brief,
        evidence_by_id=evidence_by_id,
        checker=evidence_checker,
    )
    post_closure_raw = downstream_rechecker(candidate_document, candidate_book)
    post_closure = _closure_snapshot(post_closure_raw)
    rollback_reason = _postcheck_error(
        pre_closure=pre_closure,
        post_closure=post_closure,
        post_conformance=post_conformance,
        post_evidence=post_evidence,
        candidate=candidate,
    )
    materialization = {
        "before_text_exact_match": True,
        "markdown_written": False,
        "digital_book_written": False,
        "markdown_block_id": markdown_rendered.block_id,
        "digital_book_block_id": getattr(candidate_block, "block_id", ""),
    }
    if rollback_reason:
        attempt = _attempt(
            plan=plan,
            brief=brief,
            original=original,
            candidate=candidate,
            removed=tuple(removed_spans),
            pre_conformance=pre_conformance,
            post_conformance=post_conformance,
            pre_evidence=pre_evidence,
            post_evidence=post_evidence,
            pre_closure=pre_closure,
            post_closure=post_closure,
            markdown_materialization=materialization,
            digital_materialization=materialization,
            decision=ROLLED_BACK,
            rollback_reason=rollback_reason,
            unique_contribution_retention=retention,
        )
        return SharedFactMaterializationResult(attempt=attempt)
    if materialization_writer is not None:
        try:
            markdown_written, digital_written = materialization_writer(candidate_document, candidate_book)
        except Exception as exc:
            markdown_written, digital_written = False, False
            rollback_reason = f"MATERIALIZATION_WRITER_ERROR:{type(exc).__name__}"
        if not (markdown_written and digital_written) and not rollback_reason:
            rollback_reason = "DUAL_TARGET_MATERIALIZATION_FAILED"
        materialization["markdown_written"] = bool(markdown_written)
        materialization["digital_book_written"] = bool(digital_written)
        if rollback_reason:
            attempt = _attempt(
                plan=plan,
                brief=brief,
                original=original,
                candidate=candidate,
                removed=tuple(removed_spans),
                pre_conformance=pre_conformance,
                post_conformance=post_conformance,
                pre_evidence=pre_evidence,
                post_evidence=post_evidence,
                pre_closure=pre_closure,
                post_closure=post_closure,
                markdown_materialization=materialization,
                digital_materialization=materialization,
                decision=ROLLED_BACK,
                rollback_reason=rollback_reason,
                unique_contribution_retention=retention,
            )
            return SharedFactMaterializationResult(attempt=attempt)
    materialization = {
        **materialization,
        "markdown_written": materialization.get("markdown_written", True),
        "digital_book_written": materialization.get("digital_book_written", True),
        "alignment": markdown_candidate_record.markdown == digital_candidate_record.markdown == candidate_block.markdown,
    }
    attempt = _attempt(
        plan=plan,
        brief=brief,
        original=original,
        candidate=candidate,
        removed=tuple(removed_spans),
        pre_conformance=pre_conformance,
        post_conformance=post_conformance,
        pre_evidence=pre_evidence,
        post_evidence=post_evidence,
        pre_closure=pre_closure,
        post_closure=post_closure,
        markdown_materialization=materialization,
        digital_materialization=materialization,
        decision=ACCEPTED,
        unique_contribution_retention=retention,
    )
    return SharedFactMaterializationResult(
        attempt=attempt,
        markdown_candidate=candidate_document,
        digital_book_candidate=candidate_book,
    )


def render_shared_fact_materialization_markdown(result: SharedFactMaterializationResult) -> str:
    attempt = result.attempt
    lines = [
        "# Controlled Shared-Fact Materialization",
        "",
        f"- decision: {attempt.final_decision}",
        f"- shared fact: {attempt.shared_fact_id}",
        f"- compression plan: {attempt.compression_plan_id}",
        f"- occurrence: {attempt.occurrence_id}",
        f"- rollback reason: {attempt.rollback_reason or 'none'}",
        "",
        "## Before",
        "",
        "```text",
        attempt.original_text or "(empty)",
        "```",
        "",
        "## Candidate",
        "",
        "```text",
        attempt.candidate_text or "(empty)",
        "```",
        "",
        "```diff",
        attempt.diff or "(no text change)",
        "```",
        "",
        f"- pre conformance: {attempt.pre_conformance}",
        f"- post conformance: {attempt.post_conformance}",
        f"- pre evidence: {attempt.pre_evidence}",
        f"- post evidence: {attempt.post_evidence}",
        f"- unique-contribution retention: {attempt.unique_contribution_retention}",
        f"- pre downstream closure: {attempt.pre_downstream_closure}",
        f"- post downstream closure: {attempt.post_downstream_closure}",
        f"- Markdown materialization: {attempt.markdown_materialization}",
        f"- DigitalBook materialization: {attempt.digital_book_materialization}",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _candidate_body(
    *,
    plan: SharedFactCompressionPlan,
    brief: OccurrenceWritingBrief,
    original: str,
    shared_fact_span: str | None,
    patch_generator: SharedFactPatchGenerator | None,
) -> tuple[str, list[str], str]:
    span = shared_fact_span or plan.shared_fact_statement
    if patch_generator is not None:
        try:
            candidate = patch_generator.generate(
                original_text=original,
                shared_fact=plan.shared_fact_statement,
                earlier_verified_support=plan.prior_verified_support,
                later_unique_contribution=plan.later_unique_contribution,
                allowed_actions=plan.allowed_actions,
                forbidden_actions=plan.forbidden_actions,
                max_recap_scope=plan.max_recap_scope,
                required_evidence_ids=plan.required_evidence_ids,
                fact_level_constraints=plan.compiled_brief_constraints,
                brief=brief,
            )
        except Exception as exc:
            return original, [], f"PATCH_GENERATOR_ERROR:{type(exc).__name__}"
        if not isinstance(candidate, str) or not candidate.strip():
            return original, [], "PATCH_GENERATOR_EMPTY_CANDIDATE"
        return candidate.strip(), [span] if span and span in original else [], ""
    if not span or span not in original:
        return original, [], "NO_EXACT_SHARED_FACT_SPAN"
    candidate = original.replace(span, "", 1)
    candidate = _tidy_removed_span(candidate)
    return candidate, [span], ""


def _run_conformance(brief, markdown_record, digital_record, checker):
    if checker is not None:
        return checker(brief, markdown_record, digital_record)
    return {
        "markdown": check_rendered_occurrence_records([brief], [markdown_record]).results[0],
        "digital_book": check_rendered_occurrence_records([brief], [digital_record]).results[0],
    }


def _run_evidence(*, markdown_document, digital_book, brief, evidence_by_id, checker):
    if checker is not None:
        return checker(markdown_document, digital_book, brief)
    if not evidence_by_id:
        return {"status": "UNAVAILABLE", "claims": []}
    claims = verify_rendered_evidence(
        markdown=markdown_document,
        digital_book=digital_book,
        briefs=[brief],
        evidence_by_id=dict(evidence_by_id),
    )
    own = [item for item in claims if item.occurrence_id == brief.occurrence_id]
    statuses = {item.support_status for item in own}
    if SupportStatus.UNSUPPORTED in statuses:
        status = SupportStatus.UNSUPPORTED
    elif SupportStatus.UNCERTAIN in statuses:
        status = SupportStatus.UNCERTAIN
    else:
        status = SupportStatus.SUPPORTED
    return {"status": status, "claims": [asdict(item) for item in own]}


def _precheck_error(conformance, evidence) -> str:
    if not conformance or any(getattr(value, "overall", "VIOLATION") != ConformanceStatus.MATCH for value in conformance.values()):
        return "PRE_CONFORMANCE_NOT_MATCH"
    if evidence.get("status") != SupportStatus.SUPPORTED:
        return "PRE_EVIDENCE_NOT_SUPPORTED"
    return ""


def _postcheck_error(*, pre_closure, post_closure, post_conformance, post_evidence, candidate) -> str:
    if any(getattr(value, "overall", "VIOLATION") != ConformanceStatus.MATCH for value in post_conformance.values()):
        return "POST_CONFORMANCE_NOT_MATCH"
    if post_evidence.get("status") != SupportStatus.SUPPORTED:
        return f"POST_EVIDENCE_NOT_SUPPORTED:{post_evidence.get('status', 'UNKNOWN')}"
    if not _retains_nonempty(candidate):
        return "EMPTY_CANDIDATE"
    if _closure_regressed(pre_closure, post_closure):
        return "DOWNSTREAM_CLOSURE_REGRESSED"
    return ""


def _closure_snapshot(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    raw = value.to_dict() if hasattr(value, "to_dict") else value
    if not isinstance(raw, Mapping):
        return {"raw": repr(raw)}
    results = []
    for item in raw.get("results", ()) or ():
        if not isinstance(item, Mapping):
            continue
        requirement = item.get("requirement", {}) or {}
        results.append(
            {
                "requirement_id": requirement.get("requirement_id", ""),
                "status": item.get("status", ""),
                "supporting_occurrence_ids": list(item.get("supporting_occurrence_ids", ()) or ()),
            }
        )
    return {"results": results, "status_counts": dict(raw.get("status_counts", {}) or {})}


def _closure_regressed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    before_by_id = {item.get("requirement_id"): item for item in before.get("results", ()) or ()}
    after_by_id = {item.get("requirement_id"): item for item in after.get("results", ()) or ()}
    bad = {"UNDER_SUPPORTED", "UNSUPPORTED", "BLOCKED_BY_PRIOR_FAILURE", "TARGET_NOT_DELIVERED"}
    for requirement_id, item in before_by_id.items():
        if item.get("status") == "CLOSED" and (
            requirement_id not in after_by_id
            or after_by_id.get(requirement_id, {}).get("status") in bad
        ):
            return True
    return False


def _same_anchor(left: RenderedOccurrence, right: RenderedOccurrence, expected_id: str) -> bool:
    return (
        left.occurrence_id == right.occurrence_id == expected_id
        and left.chapter_id == right.chapter_id
        and left.section_id == right.section_id
    )


def _find_digital_block(book: DigitalBook, occurrence_id: str):
    found = []
    for project in book.projects:
        for task in project.tasks:
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                if isinstance(semantic, Mapping) and semantic.get("occurrence_id") == occurrence_id:
                    found.append(block)
    return found[0] if len(found) == 1 else None


def _replace_markdown_occurrence(document: str, record: RenderedOccurrence, candidate: str) -> str:
    return document[:record.start_offset] + candidate + document[record.end_offset:]


def _verify_unique_contribution_retention(
    brief: OccurrenceWritingBrief,
    post_conformance: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the later occurrence's existing teaching contract.

    ``later_unique_contribution`` is a semantic planning summary and is not
    required to occur literally in the rendered body.  Retention therefore
    delegates to the already authoritative occurrence conformance results.
    Facets and extensions are recorded as structured obligations; no second
    semantic checker or free-form similarity judgement is introduced.
    """
    obligations = {
        "required_contribution_keys": [
            *(f"facet:{item}" for item in brief.must_teach_facets),
            *(f"extension:{item}" for item in brief.extension_keys),
        ],
        "required_fact_ids": [],
        "required_extension_keys": list(brief.extension_keys),
        "required_body_spans": [],
        "semantic_summary_not_used_as_literal_requirement": bool(
            brief.contribution_goal or brief.must_include_points
        ),
    }
    if not post_conformance:
        return {
            "status": "UNRESOLVED",
            "mode": "FAIL_CLOSED",
            "missing_obligations": ["occurrence_conformance_result"],
            **obligations,
        }

    missing: list[str] = []
    contract_proves_retention = False
    for target, result in post_conformance.items():
        overall = _result_value(result, "overall", "VIOLATION")
        if overall != ConformanceStatus.MATCH:
            missing.append(f"{target}:overall={overall}")
            continue
        facets = _result_mapping(result, "must_teach_coverage")
        extensions = _result_mapping(result, "extension_coverage")
        contribution = _result_value(result, "contribution_goal_coverage", ConformanceStatus.NOT_APPLICABLE)
        if any(value != ConformanceStatus.MATCH for value in facets.values()):
            missing.extend(f"{target}:facet:{key}" for key, value in facets.items() if value != ConformanceStatus.MATCH)
        if any(value != ConformanceStatus.MATCH for value in extensions.values()):
            missing.extend(f"{target}:extension:{key}" for key, value in extensions.items() if value != ConformanceStatus.MATCH)
        if contribution == ConformanceStatus.MATCH or facets or extensions:
            contract_proves_retention = True
        elif contribution != ConformanceStatus.NOT_APPLICABLE:
            missing.append(f"{target}:contribution_goal={contribution}")

    if missing:
        return {
            "status": "FAIL",
            "mode": "EXISTING_CONFORMANCE_CONTRACT",
            "missing_obligations": missing,
            **obligations,
        }
    if not contract_proves_retention:
        return {
            "status": "UNRESOLVED",
            "mode": "FAIL_CLOSED",
            "missing_obligations": ["structured_independent_teaching_contract"],
            **obligations,
        }
    return {
        "status": "PASS",
        "mode": "EXISTING_CONFORMANCE_CONTRACT",
        "missing_obligations": [],
        **obligations,
    }


def _result_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(key, default)
    return getattr(result, key, default)


def _result_mapping(result: Any, key: str) -> Mapping[str, Any]:
    value = _result_value(result, key, {})
    return value if isinstance(value, Mapping) else {}


def _retains_nonempty(candidate: str) -> bool:
    return bool(re.sub(r"[`*_>#\-\s]", "", candidate or ""))


def _tidy_removed_span(candidate: str) -> str:
    candidate = re.sub(r"[ \t]{2,}", " ", candidate)
    candidate = re.sub(r"\n{3,}", "\n\n", candidate)
    return candidate.strip()


def _diff(before: str, after: str) -> str:
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="before", tofile="candidate", lineterm=""))


def _attempt(*, plan, brief, original, candidate, removed, pre_conformance, post_conformance, pre_evidence, post_evidence, pre_closure, post_closure, markdown_materialization, digital_materialization, decision, rollback_reason="", unique_contribution_retention=None):
    return SharedFactMaterializationAttempt(
        shared_fact_id=plan.shared_fact_id,
        compression_plan_id=plan.plan_id,
        occurrence_id=brief.occurrence_id,
        original_text=original,
        candidate_text=candidate,
        diff=_diff(original, candidate),
        removed_shared_spans=tuple(removed),
        retained_unique_contribution=plan.later_unique_contribution,
        pre_conformance=pre_conformance,
        post_conformance=post_conformance,
        pre_evidence=pre_evidence,
        post_evidence=post_evidence,
        pre_downstream_closure=pre_closure,
        post_downstream_closure=post_closure,
        markdown_materialization=markdown_materialization,
        digital_book_materialization=digital_materialization,
        final_decision=decision,
        rollback_reason=rollback_reason,
        unique_contribution_retention=unique_contribution_retention or {},
    )


def _replace_attempt(attempt: SharedFactMaterializationAttempt, **changes) -> SharedFactMaterializationAttempt:
    return replace(attempt, **changes)


def _with_reason(attempt: SharedFactMaterializationAttempt, reason: str, decision: str) -> SharedFactMaterializationResult:
    return SharedFactMaterializationResult(attempt=_replace_attempt(attempt, final_decision=decision, rollback_reason=reason))
