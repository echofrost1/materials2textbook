"""Phase 3C: constrained one-gap generative repair patches.

The model may supply only a short evidence-grounded string.  All upstream
decisions, allowed action/gap, placement, validation and rollback are owned by
deterministic code.  No writer/exporter/BookPlan object is imported here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import re
from typing import Protocol

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.knowledge_map.repair_proposals import RepairAction, RepairProposal
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    RenderedConformanceResult,
    RenderedOccurrence,
    check_rendered_occurrence_records,
)
from materials2textbook.knowledge_map.safe_auto_repair import RepairAttemptStatus
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.prompts.controlled_repair import PROMPT_VERSION, build_controlled_repair_messages
from materials2textbook.schemas import EvidenceChunk


class InsertionStrategy:
    APPEND_TO_BODY = "APPEND_TO_BODY"
    INSERT_AFTER_CONTEXT = "INSERT_AFTER_CONTEXT"
    INSERT_BEFORE_TASK_STEPS = "INSERT_BEFORE_TASK_STEPS"
    REPLACE_MARKED_SPAN = "REPLACE_MARKED_SPAN"


_ALLOWED_ACTIONS = {
    RepairAction.ADD_REQUIRED_FACET,
    RepairAction.ADD_EXTENSION,
    RepairAction.ADD_CONTRIBUTION,
}


@dataclass(frozen=True)
class GeneratedRepairDraft:
    generated_text: str
    evidence_chunk_ids: tuple[str, ...]
    evidence_support_terms: tuple[str, ...]
    model_id: str
    prompt_version: str = PROMPT_VERSION


class ControlledPatchGenerator(Protocol):
    model_id: str

    def generate(
        self,
        *,
        brief: OccurrenceWritingBrief,
        repair_action: str,
        target_gap: str,
        insertion_strategy: str,
        evidence_chunks: list[EvidenceChunk],
        current_text: str,
    ) -> GeneratedRepairDraft:
        """Return a minimal patch draft, never a rewritten occurrence."""


class LLMControlledPatchGenerator:
    """Strict JSON adapter for an LLM provider; it cannot choose repair scope."""

    def __init__(self, provider: LLMProvider, *, model_id: str, prompt_version: str = PROMPT_VERSION) -> None:
        self.provider = provider
        self.model_id = model_id
        self.prompt_version = prompt_version

    def generate(self, **kwargs) -> GeneratedRepairDraft:
        evidence_chunks = kwargs["evidence_chunks"]
        payload = {
            "occurrence_id": kwargs["brief"].occurrence_id,
            "role": kwargs["brief"].role,
            "repair_action": kwargs["repair_action"],
            "target_gap": kwargs["target_gap"],
            "target_contract": _target_contract(kwargs["repair_action"], kwargs["target_gap"]),
            "insertion_strategy": kwargs["insertion_strategy"],
            "already_available_facets": kwargs["brief"].already_available_facets,
            "must_not_reteach_facets": kwargs["brief"].must_not_reteach_facets,
            "forbidden_content": kwargs["brief"].forbidden_content,
            "current_text": kwargs["current_text"],
            "evidence": [
                {"chunk_id": item.chunk_id, "title": item.title, "content": (item.summary or item.content)[:1200]}
                for item in evidence_chunks
            ],
        }
        raw = self.provider.generate(build_controlled_repair_messages(payload))
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Controlled repair model returned invalid JSON: {raw[:500]}") from exc
        if not isinstance(value, dict):
            raise ValueError("Controlled repair model returned a non-object JSON value.")
        text = value.get("generated_text")
        ids = value.get("evidence_chunk_ids")
        terms = value.get("evidence_support_terms")
        if not isinstance(text, str) or not isinstance(ids, list) or not isinstance(terms, list):
            raise ValueError("Controlled repair model response does not match the patch schema.")
        return GeneratedRepairDraft(
            generated_text=text.strip(),
            evidence_chunk_ids=tuple(item for item in ids if isinstance(item, str)),
            evidence_support_terms=tuple(item for item in terms if isinstance(item, str)),
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )


@dataclass(frozen=True)
class GeneratedRepairPatch:
    occurrence_id: str
    repair_action: str
    target_gap: str
    generated_text: str
    insertion_strategy: str
    evidence_source_ids: tuple[str, ...]
    evidence_support_terms: tuple[str, ...]
    model_id: str
    prompt_version: str
    before_text: str
    after_text: str
    pre_conformance: dict[str, RenderedConformanceResult | None]
    post_conformance: dict[str, RenderedConformanceResult | None]
    status: str
    rollback_reason: str = ""

    def to_dict(self) -> dict:
        return {
            **{key: value for key, value in asdict(self).items() if key not in {"pre_conformance", "post_conformance"}},
            "pre_conformance": {key: asdict(value) if value else None for key, value in self.pre_conformance.items()},
            "post_conformance": {key: asdict(value) if value else None for key, value in self.post_conformance.items()},
        }


@dataclass(frozen=True)
class ControlledRepairResult:
    patch: GeneratedRepairPatch
    markdown_candidate: RenderedOccurrence | None
    digital_book_candidate: RenderedOccurrence | None


@dataclass(frozen=True)
class ControlledRepairSequenceResult:
    """Sequential one-gap attempts; a rollback never becomes an interim edit."""

    patches: tuple[GeneratedRepairPatch, ...]
    markdown_candidate: RenderedOccurrence | None
    digital_book_candidate: RenderedOccurrence | None


def execute_controlled_generative_repair(
    *,
    brief: OccurrenceWritingBrief,
    proposal: RepairProposal,
    repair_action: str,
    target_gap: str,
    markdown_rendered: RenderedOccurrence | None,
    digital_book_rendered: RenderedOccurrence | None,
    evidence_by_id: dict[str, EvidenceChunk],
    generator: ControlledPatchGenerator,
) -> ControlledRepairResult:
    """Generate and test one patch for exactly one allowed failure dimension."""
    before = markdown_rendered.markdown if markdown_rendered else ""
    empty = _patch(
        brief=brief, action=repair_action, gap=target_gap, text="", strategy=_strategy_for(repair_action),
        evidence_ids=(), support_terms=(), model_id=getattr(generator, "model_id", "unknown"),
        prompt_version=getattr(generator, "prompt_version", PROMPT_VERSION), before=before,
        after=before, pre={"markdown": None, "digital_book": None}, post={"markdown": None, "digital_book": None},
        status=RepairAttemptStatus.ROLLED_BACK,
    )
    if proposal.occurrence_id != brief.occurrence_id:
        return ControlledRepairResult(_rollback(empty, "PROPOSAL_BRIEF_OCCURRENCE_MISMATCH"), None, None)
    if not markdown_rendered or not digital_book_rendered or not _aligned_occurrences(markdown_rendered, digital_book_rendered, brief.occurrence_id):
        return ControlledRepairResult(_rollback(empty, "ANCHOR_MISMATCH_OR_MISSING_TARGET"), None, None)
    pre = _check_targets(brief, markdown_rendered, digital_book_rendered)
    if any(value is None for value in pre.values()):
        return ControlledRepairResult(_rollback(replace(empty, pre_conformance=pre), "PRE_CHECKER_ERROR"), None, None)
    if markdown_rendered.markdown != digital_book_rendered.markdown:
        return ControlledRepairResult(_rollback(replace(empty, pre_conformance=pre), "RENDER_TARGET_TEXT_MISMATCH"), None, None)
    if repair_action not in _ALLOWED_ACTIONS or repair_action not in proposal.actions:
        return ControlledRepairResult(_rollback(replace(empty, pre_conformance=pre), "UNSUPPORTED_OR_UNPROPOSED_REPAIR_ACTION"), None, None)
    gap_error = _validate_target_gap(pre["markdown"], brief, repair_action, target_gap)
    if gap_error:
        return ControlledRepairResult(_rollback(replace(empty, pre_conformance=pre), gap_error), None, None)
    allowed_evidence = _allowed_evidence(brief, evidence_by_id)
    if not allowed_evidence:
        return ControlledRepairResult(_rollback(replace(empty, pre_conformance=pre), "MISSING_ALLOWED_EVIDENCE"), None, None)

    strategy = _strategy_for(repair_action)
    try:
        draft = generator.generate(
            brief=brief,
            repair_action=repair_action,
            target_gap=target_gap,
            insertion_strategy=strategy,
            evidence_chunks=allowed_evidence,
            current_text=before,
        )
    except Exception as exc:
        return ControlledRepairResult(_rollback(replace(empty, pre_conformance=pre), f"GENERATOR_ERROR:{type(exc).__name__}"), None, None)
    candidate = _insert(before, draft.generated_text, strategy)
    validation_error, verified_terms = _validate_draft(draft, allowed_evidence, before)
    draft_patch = _patch(
        brief=brief, action=repair_action, gap=target_gap, text=draft.generated_text, strategy=strategy,
        evidence_ids=draft.evidence_chunk_ids,
        support_terms=verified_terms or draft.evidence_support_terms, model_id=draft.model_id,
        prompt_version=draft.prompt_version, before=before, after=candidate, pre=pre,
        post={"markdown": None, "digital_book": None}, status=RepairAttemptStatus.ROLLED_BACK,
    )
    if validation_error:
        return ControlledRepairResult(_rollback(draft_patch, validation_error), None, None)
    markdown_candidate = replace(markdown_rendered, markdown=candidate)
    digital_candidate = replace(digital_book_rendered, markdown=candidate)
    post = _check_targets(brief, markdown_candidate, digital_candidate)
    draft_patch = replace(draft_patch, post_conformance=post)
    if any(value is None for value in post.values()):
        return ControlledRepairResult(_rollback(draft_patch, "POST_CHECKER_ERROR"), None, None)
    if any(value.overall != ConformanceStatus.MATCH for value in post.values()):
        statuses = ",".join(f"{key}:{value.overall}" for key, value in post.items() if value)
        return ControlledRepairResult(_rollback(draft_patch, f"POST_CONFORMANCE_NOT_MATCH:{statuses}"), None, None)
    return ControlledRepairResult(replace(draft_patch, status=RepairAttemptStatus.ACCEPTED), markdown_candidate, digital_candidate)


def execute_controlled_repair_sequence(
    *,
    brief: OccurrenceWritingBrief,
    proposal: RepairProposal,
    gaps: list[tuple[str, str]],
    markdown_rendered: RenderedOccurrence | None,
    digital_book_rendered: RenderedOccurrence | None,
    evidence_by_id: dict[str, EvidenceChunk],
    generator: ControlledPatchGenerator,
) -> ControlledRepairSequenceResult:
    """Attempt gaps serially, rechecking both outputs after every one.

    A generated patch is committed to this in-memory sequence only if it is a
    full dual-target MATCH. Therefore a first patch that leaves another gap as
    PARTIAL is rolled back and terminates the sequence rather than becoming a
    hidden intermediate state.
    """
    current_markdown = markdown_rendered
    current_digital = digital_book_rendered
    patches: list[GeneratedRepairPatch] = []
    for action, gap in gaps:
        result = execute_controlled_generative_repair(
            brief=brief,
            proposal=proposal,
            repair_action=action,
            target_gap=gap,
            markdown_rendered=current_markdown,
            digital_book_rendered=current_digital,
            evidence_by_id=evidence_by_id,
            generator=generator,
        )
        patches.append(result.patch)
        if result.patch.status != RepairAttemptStatus.ACCEPTED:
            break
        current_markdown = result.markdown_candidate
        current_digital = result.digital_book_candidate
    return ControlledRepairSequenceResult(tuple(patches), current_markdown, current_digital)


def _strategy_for(action: str) -> str:
    # The model never chooses placement. A single append keeps the repair
    # narrow and makes the Markdown/DigitalBook candidate identical.
    return InsertionStrategy.APPEND_TO_BODY


def _validate_target_gap(result: RenderedConformanceResult, brief: OccurrenceWritingBrief, action: str, gap: str) -> str:
    if action == RepairAction.ADD_REQUIRED_FACET:
        return "INVALID_FACET_GAP" if gap not in brief.must_teach_facets or result.must_teach_coverage.get(gap) == ConformanceStatus.MATCH else ""
    if action == RepairAction.ADD_EXTENSION:
        return "INVALID_EXTENSION_GAP" if gap not in brief.extension_keys or result.extension_coverage.get(gap) == ConformanceStatus.MATCH else ""
    if action == RepairAction.ADD_CONTRIBUTION:
        return "INVALID_CONTRIBUTION_GAP" if gap != "contribution" or result.contribution_goal_coverage == ConformanceStatus.MATCH else ""
    return "UNSUPPORTED_REPAIR_ACTION"


def _allowed_evidence(brief: OccurrenceWritingBrief, evidence_by_id: dict[str, EvidenceChunk]) -> list[EvidenceChunk]:
    ids = dict.fromkeys([*brief.source_chunk_ids, *brief.semantic_delta_evidence_ids])
    return [evidence_by_id[item] for item in ids if item in evidence_by_id]


def _validate_draft(draft: GeneratedRepairDraft, evidence: list[EvidenceChunk], before: str) -> tuple[str, tuple[str, ...]]:
    text = draft.generated_text.strip()
    if not text:
        return "EMPTY_GENERATED_PATCH", ()
    if text.startswith("#") or len(_sentences(text)) != 1 or len(text) > 420:
        return "NON_MINIMAL_OR_STRUCTURAL_PATCH", ()
    if text == before or text in before:
        return "PATCH_REPEATS_EXISTING_BODY", ()
    allowed = {item.chunk_id for item in evidence}
    if not draft.evidence_chunk_ids or not set(draft.evidence_chunk_ids).issubset(allowed):
        return "UNAUTHORIZED_EVIDENCE_ID", ()
    # Do not trust model-declared support terms. Derive the audit terms from
    # the exact permitted evidence and the generated patch itself.
    terms = _observed_evidence_terms(text, [item for item in evidence if item.chunk_id in draft.evidence_chunk_ids])
    if not terms:
        return "UNSUPPORTED_GENERATED_FACT", ()
    return "", terms


def _insert(before: str, patch: str, strategy: str) -> str:
    if strategy != InsertionStrategy.APPEND_TO_BODY:
        raise ValueError(f"Unsupported deterministic insertion strategy {strategy!r}.")
    return f"{before.rstrip()}\n\n{patch.strip()}" if before.strip() else patch.strip()


def _target_contract(action: str, gap: str) -> str:
    """Checker-facing wording constraints, fixed by code rather than the model."""
    if action == RepairAction.ADD_REQUIRED_FACET and gap == "EXPLAIN":
        return "Add one causal/explanatory statement and include the literal word 影响 or the English word affects."
    if action == RepairAction.ADD_EXTENSION:
        return f"Add only the new extension {gap}; for a limit include limit, 限制, or 限流."
    if action == RepairAction.ADD_CONTRIBUTION:
        return "State how to use the already-known knowledge in the current task; include 使用 and 任务, or use and task."
    return f"Address only {gap}."


def _observed_evidence_terms(generated_text: str, evidence: list[EvidenceChunk]) -> tuple[str, ...]:
    """Derive literal Chinese/English support terms rather than trusting LLM labels."""
    generated = generated_text.lower()
    candidates: list[str] = []
    for chunk in evidence:
        source = " ".join([chunk.title, chunk.summary, chunk.content])
        for group in re.findall(r"[\u4e00-\u9fff]{4,}|[A-Za-z][A-Za-z0-9_-]{2,}", source):
            # Require a substantive literal term. Two-character overlaps such
            # as “板焊” are too weak to establish that a new constraint came
            # from the permitted evidence.
            if group.lower() in generated:
                candidates.append(group)
    return tuple(dict.fromkeys(candidates))


def _check_targets(brief, markdown, digital) -> dict[str, RenderedConformanceResult | None]:
    try:
        return {
            "markdown": check_rendered_occurrence_records([brief], [markdown]).results[0],
            "digital_book": check_rendered_occurrence_records([brief], [digital]).results[0],
        }
    except Exception:
        return {"markdown": None, "digital_book": None}


def _aligned_occurrences(left: RenderedOccurrence, right: RenderedOccurrence, occurrence_id: str) -> bool:
    return (
        left.occurrence_id == right.occurrence_id == occurrence_id
        and left.chapter_id == right.chapter_id
        and left.section_id == right.section_id
    )


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?。！？])\s*|\n+", text) if item.strip()]


def _patch(**kwargs) -> GeneratedRepairPatch:
    return GeneratedRepairPatch(
        occurrence_id=kwargs["brief"].occurrence_id,
        repair_action=kwargs["action"],
        target_gap=kwargs["gap"],
        generated_text=kwargs["text"],
        insertion_strategy=kwargs["strategy"],
        evidence_source_ids=tuple(kwargs["evidence_ids"]),
        evidence_support_terms=tuple(kwargs["support_terms"]),
        model_id=kwargs["model_id"],
        prompt_version=kwargs["prompt_version"],
        before_text=kwargs["before"],
        after_text=kwargs["after"],
        pre_conformance=kwargs["pre"],
        post_conformance=kwargs["post"],
        status=kwargs["status"],
    )


def _rollback(patch: GeneratedRepairPatch, reason: str) -> GeneratedRepairPatch:
    return replace(patch, status=RepairAttemptStatus.ROLLED_BACK, rollback_reason=reason)


def render_generated_repair_patch_report_markdown(patches: list[GeneratedRepairPatch]) -> str:
    lines = ["# Controlled Generative Repair Audit", "", "Each entry is a one-gap patch; no full occurrence rewrite was requested.", ""]
    for patch in patches:
        lines.extend([
            f"## {patch.occurrence_id} / {patch.target_gap}",
            f"- action / insertion: {patch.repair_action} / {patch.insertion_strategy}",
            f"- status: {patch.status}",
            f"- model / prompt: {patch.model_id} / {patch.prompt_version}",
            f"- allowed evidence used: {', '.join(patch.evidence_source_ids) or 'none'}",
            f"- support terms: {', '.join(patch.evidence_support_terms) or 'none'}",
            f"- rollback reason: {patch.rollback_reason or 'none'}",
            f"- pre conformance: {_status_summary(patch.pre_conformance)}",
            f"- post conformance: {_status_summary(patch.post_conformance)}",
            "- generated patch:", "```text", patch.generated_text or "(none)", "```",
            "- before:", "```text", patch.before_text or "(empty)", "```",
            "- after candidate:", "```text", patch.after_text or "(empty)", "```", "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _status_summary(values: dict[str, RenderedConformanceResult | None]) -> str:
    return ", ".join(f"{key}={value.overall if value else 'CHECKER_ERROR'}" for key, value in values.items()) or "none"
