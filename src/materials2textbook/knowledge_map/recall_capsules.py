"""Phase 3D controlled, evidence-bound minimal recall capsules.

This module is intentionally separate from role rewriting: it may add at most
two source-supported context sentences to a pre-planned RECALL occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Protocol

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.knowledge_map.models import LearningRole, PlannedOccurrence
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    RenderedConformanceResult,
    RenderedOccurrence,
    check_rendered_occurrence_records,
)
from materials2textbook.knowledge_map.safe_auto_repair import RepairAttemptStatus
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.prompts.recall_capsule import PROMPT_VERSION, build_recall_capsule_messages
from materials2textbook.schemas import EvidenceChunk


class RecallInsertionStrategy:
    INSERT_AFTER_CONTEXT = "INSERT_AFTER_CONTEXT"


@dataclass(frozen=True)
class RecallCapsulePlan:
    occurrence_id: str
    source_occurrence_ids: tuple[str, ...]
    required_facets: tuple[str, ...]
    required_aspects: tuple[str, ...]
    forbidden_aspects: tuple[str, ...]
    max_sentences: int
    allowed_evidence_ids: tuple[str, ...]
    insertion_strategy: str


@dataclass(frozen=True)
class RecallPlanResolution:
    plan: RecallCapsulePlan | None
    status: str  # READY | MANUAL_REVIEW
    reason: str = ""


@dataclass(frozen=True)
class RecallCapsuleDraft:
    generated_text: str
    evidence_chunk_ids: tuple[str, ...]
    model_id: str
    prompt_version: str = PROMPT_VERSION


class RecallCapsuleGenerator(Protocol):
    model_id: str

    def generate(
        self, *, plan: RecallCapsulePlan, brief: OccurrenceWritingBrief,
        evidence_chunks: list[EvidenceChunk], current_text: str,
    ) -> RecallCapsuleDraft:
        """Return only a recall capsule; it cannot choose sources or placement."""


class LLMRecallCapsuleGenerator:
    def __init__(self, provider: LLMProvider, *, model_id: str, prompt_version: str = PROMPT_VERSION) -> None:
        self.provider = provider
        self.model_id = model_id
        self.prompt_version = prompt_version

    def generate(self, **kwargs) -> RecallCapsuleDraft:
        plan: RecallCapsulePlan = kwargs["plan"]
        brief: OccurrenceWritingBrief = kwargs["brief"]
        payload = {
            "occurrence_id": plan.occurrence_id,
            "source_occurrence_ids": plan.source_occurrence_ids,
            "required_facets": plan.required_facets,
            "required_aspects": plan.required_aspects,
            "forbidden_aspects": plan.forbidden_aspects,
            "max_sentences": plan.max_sentences,
            "insertion_strategy": plan.insertion_strategy,
            "current_task_context": brief.contribution_goal,
            "current_text": kwargs["current_text"],
            "evidence": [
                {"chunk_id": item.chunk_id, "title": item.title, "content": (item.summary or item.content)[:1200]}
                for item in kwargs["evidence_chunks"]
            ],
        }
        raw = self.provider.generate(build_recall_capsule_messages(payload))
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Recall capsule model returned invalid JSON: {raw[:500]}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("generated_text"), str) or not isinstance(value.get("evidence_chunk_ids"), list):
            raise ValueError("Recall capsule model response does not match the capsule schema.")
        return RecallCapsuleDraft(
            generated_text=value["generated_text"].strip(),
            evidence_chunk_ids=tuple(item for item in value["evidence_chunk_ids"] if isinstance(item, str)),
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )


@dataclass(frozen=True)
class RecallCapsuleAttempt:
    occurrence_id: str
    plan: RecallCapsulePlan | None
    generated_text: str
    evidence_source_ids: tuple[str, ...]
    before_text: str
    after_text: str
    pre_conformance: dict[str, RenderedConformanceResult | None]
    post_conformance: dict[str, RenderedConformanceResult | None]
    status: str
    rollback_reason: str = ""


@dataclass(frozen=True)
class RecallCapsuleResult:
    attempt: RecallCapsuleAttempt
    markdown_candidate: RenderedOccurrence | None
    digital_book_candidate: RenderedOccurrence | None


def plan_recall_capsule(
    *, recall_occurrence: PlannedOccurrence, recall_brief: OccurrenceWritingBrief,
    all_occurrences: list[PlannedOccurrence], briefs: list[OccurrenceWritingBrief],
    verified_occurrence_ids: set[str],
) -> RecallPlanResolution:
    """Choose only earlier, verified same-knowledge teaching sources.

    The explicit ``verified_occurrence_ids`` boundary prevents an availability
    transition alone from being mistaken for a verified source occurrence.
    """
    if recall_occurrence.role != LearningRole.RECALL or recall_brief.role != LearningRole.RECALL:
        return RecallPlanResolution(None, "MANUAL_REVIEW", "NOT_A_RECALL_OCCURRENCE")
    required = tuple(dict.fromkeys(recall_occurrence.required_self_facets or recall_brief.required_facets))
    if not required:
        return RecallPlanResolution(None, "MANUAL_REVIEW", "NO_REQUIRED_RECALL_FACET")
    brief_by_id = {item.occurrence_id: item for item in briefs}
    eligible = [
        item for item in all_occurrences
        if item.knowledge_id == recall_occurrence.knowledge_id
        and item.position < recall_occurrence.position
        and item.occurrence_id in verified_occurrence_ids
        and set(required).issubset(item.intended_grants)
        and item.role in {LearningRole.TEACH, LearningRole.EXTEND}
        and item.occurrence_id in brief_by_id
    ]
    if not eligible:
        return RecallPlanResolution(None, "MANUAL_REVIEW", "REQUIRED_FACET_NOT_PREVIOUSLY_VERIFIED")
    sources = sorted(eligible, key=lambda item: item.position)
    source_briefs = [brief_by_id[item.occurrence_id] for item in sources]
    allowed = tuple(dict.fromkeys(
        evidence_id
        for source in source_briefs
        for evidence_id in [*source.source_chunk_ids, *source.semantic_delta_evidence_ids]
    ))
    if not allowed:
        return RecallPlanResolution(None, "MANUAL_REVIEW", "VERIFIED_SOURCE_HAS_NO_ALLOWED_EVIDENCE")
    forbidden = tuple(dict.fromkeys([
        "definition", "complete procedure", "parameter/method rule", "new facet", "new extension",
        *[f"facet:{item}" for item in _other_facets(required)],
        *[f"extension:{item}" for source in sources for item in source.intended_extension_keys],
    ]))
    return RecallPlanResolution(RecallCapsulePlan(
        occurrence_id=recall_occurrence.occurrence_id,
        source_occurrence_ids=tuple(item.occurrence_id for item in sources),
        required_facets=required,
        required_aspects=tuple(f"facet:{item}" for item in required),
        forbidden_aspects=forbidden,
        max_sentences=min(2, max(1, recall_brief.max_recap_sentences)),
        allowed_evidence_ids=allowed,
        insertion_strategy=RecallInsertionStrategy.INSERT_AFTER_CONTEXT,
    ), "READY")


def execute_recall_capsule(
    *, plan: RecallCapsulePlan, brief: OccurrenceWritingBrief,
    markdown_rendered: RenderedOccurrence | None, digital_book_rendered: RenderedOccurrence | None,
    evidence_by_id: dict[str, EvidenceChunk], generator: RecallCapsuleGenerator,
) -> RecallCapsuleResult:
    """Generate one capsule and accept only a full identical dual-render MATCH."""
    before = markdown_rendered.markdown if markdown_rendered else ""
    empty = RecallCapsuleAttempt(
        occurrence_id=brief.occurrence_id, plan=plan, generated_text="", evidence_source_ids=(), before_text=before,
        after_text=before, pre_conformance={"markdown": None, "digital_book": None},
        post_conformance={"markdown": None, "digital_book": None}, status=RepairAttemptStatus.ROLLED_BACK,
    )
    if plan.occurrence_id != brief.occurrence_id or brief.role != LearningRole.RECALL:
        return _rollback(empty, "PLAN_BRIEF_ROLE_OR_OCCURRENCE_MISMATCH")
    if not markdown_rendered or not digital_book_rendered or not _aligned(markdown_rendered, digital_book_rendered, brief.occurrence_id):
        return _rollback(empty, "ANCHOR_MISMATCH_OR_MISSING_TARGET")
    if markdown_rendered.markdown != digital_book_rendered.markdown:
        return _rollback(empty, "RENDER_TARGET_TEXT_MISMATCH")
    pre = _check(brief, markdown_rendered, digital_book_rendered)
    if any(value is None for value in pre.values()):
        return _rollback(replace(empty, pre_conformance=pre), "PRE_CHECKER_ERROR")
    evidence = [evidence_by_id[item] for item in plan.allowed_evidence_ids if item in evidence_by_id]
    if not evidence or tuple(item.chunk_id for item in evidence) != plan.allowed_evidence_ids:
        return _rollback(replace(empty, pre_conformance=pre), "SOURCE_EVIDENCE_MISMATCH")
    try:
        draft = generator.generate(plan=plan, brief=brief, evidence_chunks=evidence, current_text=before)
    except Exception as exc:
        return _rollback(replace(empty, pre_conformance=pre), f"GENERATOR_ERROR:{type(exc).__name__}")
    candidate_text = _insert_after_context(before, draft.generated_text)
    attempt = replace(
        empty, generated_text=draft.generated_text, evidence_source_ids=draft.evidence_chunk_ids,
        after_text=candidate_text, pre_conformance=pre,
    )
    error = _validate_capsule(draft, plan, evidence)
    if error:
        return _rollback(attempt, error)
    markdown_candidate = replace(markdown_rendered, markdown=candidate_text)
    digital_candidate = replace(digital_book_rendered, markdown=candidate_text)
    post = _check(brief, markdown_candidate, digital_candidate)
    attempt = replace(attempt, post_conformance=post)
    if any(value is None for value in post.values()):
        return _rollback(attempt, "POST_CHECKER_ERROR")
    if any(value.overall != ConformanceStatus.MATCH for value in post.values()):
        statuses = ",".join(f"{key}:{value.overall}" for key, value in post.items() if value)
        return _rollback(attempt, f"POST_CONFORMANCE_NOT_MATCH:{statuses}")
    return RecallCapsuleResult(replace(attempt, status=RepairAttemptStatus.ACCEPTED), markdown_candidate, digital_candidate)


def _validate_capsule(draft: RecallCapsuleDraft, plan: RecallCapsulePlan, evidence: list[EvidenceChunk]) -> str:
    text = draft.generated_text.strip()
    if not text or text.startswith("#") or len(_sentences(text)) > plan.max_sentences or len(text) > 420:
        return "NON_MINIMAL_RECALL_CAPSULE"
    if not draft.evidence_chunk_ids or not set(draft.evidence_chunk_ids).issubset(plan.allowed_evidence_ids):
        return "UNAUTHORIZED_EVIDENCE_ID"
    cited = [item for item in evidence if item.chunk_id in draft.evidence_chunk_ids]
    if not _evidence_support_terms(text, cited):
        return "UNSUPPORTED_RECALL_FACT"
    lowered = text.lower()
    if _contains_any(lowered, ("defined as", "definition", "完整步骤", "操作步骤", "step 1", "步骤如下", "参数设置")):
        return "RECALL_BECAME_COMPLETE_TEACH"
    if _introduces_unrequired_facet(lowered, plan.required_facets):
        return "RECALL_INTRODUCED_NEW_FACET"
    if any(_extension_appears(item.removeprefix("extension:"), lowered) for item in plan.forbidden_aspects if item.startswith("extension:")):
        return "RECALL_INTRODUCED_NEW_EXTENSION"
    return ""


def _check(brief, markdown, digital) -> dict[str, RenderedConformanceResult | None]:
    try:
        return {
            "markdown": check_rendered_occurrence_records([brief], [markdown]).results[0],
            "digital_book": check_rendered_occurrence_records([brief], [digital]).results[0],
        }
    except Exception:
        return {"markdown": None, "digital_book": None}


def _rollback(attempt: RecallCapsuleAttempt, reason: str) -> RecallCapsuleResult:
    return RecallCapsuleResult(replace(attempt, status=RepairAttemptStatus.ROLLED_BACK, rollback_reason=reason), None, None)


def _aligned(left: RenderedOccurrence, right: RenderedOccurrence, occurrence_id: str) -> bool:
    return left.occurrence_id == right.occurrence_id == occurrence_id and left.chapter_id == right.chapter_id and left.section_id == right.section_id


def _insert_after_context(before: str, capsule: str) -> str:
    return f"{before.rstrip()}\n\n{capsule.strip()}" if before.strip() else capsule.strip()


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?。！？])\s*|\n+", text) if item.strip()]


def _evidence_support_terms(text: str, evidence: list[EvidenceChunk]) -> tuple[str, ...]:
    lowered = text.lower()
    terms: list[str] = []
    for chunk in evidence:
        source = " ".join([chunk.title, chunk.summary, chunk.content])
        for term in re.findall(r"[\u4e00-\u9fff]{4,}|[A-Za-z][A-Za-z0-9_-]{2,}", source):
            if term.lower() in lowered:
                terms.append(term)
    return tuple(dict.fromkeys(terms))


def _other_facets(required: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in ("ORIENTED", "EXPLAIN", "PERFORM", "ANALYZE") if item not in required)


def _introduces_unrequired_facet(text: str, required: tuple[str, ...]) -> bool:
    signals = {
        "ORIENTED": ("overview", "introduction", "概述", "认识"),
        "EXPLAIN": ("explain", "explanation", "principle", "reason", "原理", "解释", "原因"),
        "PERFORM": ("operate", "procedure", "adjust", "操作", "调整", "执行"),
        "ANALYZE": ("analyze", "analysis", "diagnos", "分析", "判断"),
    }
    return any(_contains_any(text, signals[facet]) for facet in _other_facets(required))


def _extension_appears(key: str, text: str) -> bool:
    terms = [item for item in re.split(r"[:_\-]+", key.lower()) if item not in {"constraint", "variant", "condition"}]
    return bool(terms) and all(term in text for term in terms)


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(item in text for item in values)
