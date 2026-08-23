"""Section-level authoring contracts and deterministic draft materialization.

Phase 5D changes the authoring unit from an independently rendered
occurrence to one coherent section.  Occurrences remain semantic/audit
constraints.  This module deliberately stops at a fake-writer boundary: it
does not call a model, change BookPlan/Blueprint/Packet, or export a DigitalBook.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Iterable, Mapping

from materials2textbook.knowledge_map.evidence_packet import (
    OBLIGATION_STATUSES,
    PARTIAL_OBLIGATION,
    SOURCE_GAP,
    SUPPORTED_OBLIGATION,
    ObligationEvidenceBinding,
    SectionEvidencePacket,
)
from materials2textbook.knowledge_map.outline import book_plan_fingerprint
from materials2textbook.knowledge_map.teaching_blueprint import (
    CASE_ACTIVITY,
    CONCEPT_PRINCIPLE,
    OBSERVATION_QUALITY_JUDGEMENT,
    OPTIONAL,
    PARAMETER_CONDITION,
    PROCEDURE_OPERATION,
    REQUIRED,
    SAFETY_COMMON_ERROR,
    SUMMARY,
    SectionTeachingBlueprint,
    TeachingObligation,
)
from materials2textbook.schemas import BookPlan, BookSectionPlan


RENDERED = "RENDERED"
RENDERED_PARTIAL = "RENDERED_PARTIAL"
BLOCKED_CORE_SOURCE_GAP = "BLOCKED_CORE_SOURCE_GAP"


class SectionAuthoringError(ValueError):
    """Raised when a draft violates the immutable section authoring contract."""


@dataclass(frozen=True)
class SectionAuthoringObligation:
    obligation_id: str
    kind: str
    objective: str
    required: str
    evidence_status: str
    authorized_evidence_ids: tuple[str, ...] = ()
    target_knowledge_ids: tuple[str, ...] = ()
    linked_occurrence_ids: tuple[str, ...] = ()
    evidence_requirement: str = ""
    depth: str = "FOCUSED"
    source_gap: bool = False
    source_field: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "objective": self.objective,
            "required": self.required,
            "evidence_status": self.evidence_status,
            "authorized_evidence_ids": list(self.authorized_evidence_ids),
            "target_knowledge_ids": list(self.target_knowledge_ids),
            "linked_occurrence_ids": list(self.linked_occurrence_ids),
            "evidence_requirement": self.evidence_requirement,
            "depth": self.depth,
            "source_gap": self.source_gap,
            "source_field": self.source_field,
        }


@dataclass(frozen=True)
class SectionAuthoringBrief:
    """Immutable section-level writing contract compiled from prior overlays."""

    brief_id: str
    outline_node_id: str
    blueprint_id: str
    packet_id: str
    source_book_plan_signature: str
    chapter_id: str
    section_no: str
    section_title: str
    section_purpose: str
    expected_learning_outcome: str
    current_task_action: str
    required_obligations: tuple[SectionAuthoringObligation, ...] = ()
    optional_obligations: tuple[SectionAuthoringObligation, ...] = ()
    authorized_evidence_per_obligation: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    source_gaps: tuple[Mapping[str, Any], ...] = ()
    partial_obligations: tuple[Mapping[str, Any], ...] = ()
    prior_verified_support: Mapping[str, Any] = field(default_factory=dict)
    occurrence_constraints: tuple[Mapping[str, Any], ...] = ()
    must_teach: tuple[str, ...] = ()
    may_recap: tuple[str, ...] = ()
    forbidden_reteach: tuple[str, ...] = ()
    required_current_contribution: tuple[str, ...] = ()
    case_activity_requirement: str = "NOT_APPLICABLE"
    exercise_requirement: str = "NOT_APPLICABLE"
    assessment_requirement: str = "NOT_APPLICABLE"
    summary_requirement: str = "NOT_APPLICABLE"
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def all_obligations(self) -> tuple[SectionAuthoringObligation, ...]:
        return self.required_obligations + self.optional_obligations

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "outline_node_id": self.outline_node_id,
            "blueprint_id": self.blueprint_id,
            "packet_id": self.packet_id,
            "source_book_plan_signature": self.source_book_plan_signature,
            "chapter_id": self.chapter_id,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "section_purpose": self.section_purpose,
            "expected_learning_outcome": self.expected_learning_outcome,
            "current_task_action": self.current_task_action,
            "required_obligations": [item.to_dict() for item in self.required_obligations],
            "optional_obligations": [item.to_dict() for item in self.optional_obligations],
            "authorized_evidence_per_obligation": {
                key: list(value) for key, value in self.authorized_evidence_per_obligation.items()
            },
            "source_gaps": [deepcopy(dict(item)) for item in self.source_gaps],
            "partial_obligations": [deepcopy(dict(item)) for item in self.partial_obligations],
            "prior_verified_support": deepcopy(dict(self.prior_verified_support)),
            "occurrence_constraints": [deepcopy(dict(item)) for item in self.occurrence_constraints],
            "must_teach": list(self.must_teach),
            "may_recap": list(self.may_recap),
            "forbidden_reteach": list(self.forbidden_reteach),
            "required_current_contribution": list(self.required_current_contribution),
            "case_activity_requirement": self.case_activity_requirement,
            "exercise_requirement": self.exercise_requirement,
            "assessment_requirement": self.assessment_requirement,
            "summary_requirement": self.summary_requirement,
            "provenance": deepcopy(dict(self.provenance)),
        }


@dataclass(frozen=True)
class RenderedSection:
    """Student-visible section output plus internal audit mappings."""

    section_id: str
    outline_node_id: str
    blueprint_id: str
    packet_id: str
    body: str
    case_activity: str = ""
    exercises: tuple[str, ...] = ()
    assessment: str = ""
    summary: str = ""
    obligation_span_map: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    occurrence_span_map: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    evidence_usage: tuple[Mapping[str, Any], ...] = ()
    generation_provenance: Mapping[str, Any] = field(default_factory=dict)
    render_status: str = RENDERED
    blocked: bool = False
    block_reasons: tuple[str, ...] = ()

    @property
    def student_visible_body(self) -> str:
        return self.body

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "outline_node_id": self.outline_node_id,
            "blueprint_id": self.blueprint_id,
            "packet_id": self.packet_id,
            "body": self.body,
            "student_visible_body": self.body,
            "case_activity": self.case_activity,
            "exercises": list(self.exercises),
            "assessment": self.assessment,
            "summary": self.summary,
            "obligation_span_map": _map_to_lists(self.obligation_span_map),
            "occurrence_span_map": _map_to_lists(self.occurrence_span_map),
            "evidence_usage": [deepcopy(dict(item)) for item in self.evidence_usage],
            "generation_provenance": deepcopy(dict(self.generation_provenance)),
            "render_status": self.render_status,
            "blocked": self.blocked,
            "block_reasons": list(self.block_reasons),
        }


def build_section_authoring_brief(
    book_plan: BookPlan,
    section_id: str,
    blueprint: SectionTeachingBlueprint,
    packet: SectionEvidencePacket,
    *,
    occurrence_constraints: Iterable[Any] | None = None,
    prior_verified_support: Mapping[str, Any] | None = None,
) -> SectionAuthoringBrief:
    """Compile a section writer contract without changing upstream objects."""

    found = _find_section(book_plan, section_id)
    if found is None:
        raise SectionAuthoringError(f"unknown outline section: {section_id}")
    chapter, section = found
    expected_signature = book_plan_fingerprint(book_plan)
    if blueprint.outline_node_id != section_id or packet.outline_node_id != section_id:
        raise SectionAuthoringError("section identity mismatch between BookPlan and overlays")
    if blueprint.source_book_plan_signature != expected_signature or packet.source_book_plan_signature != expected_signature:
        raise SectionAuthoringError("authoring inputs do not belong to the supplied BookPlan")
    if packet.blueprint_id != blueprint.blueprint_id:
        raise SectionAuthoringError("Evidence Packet does not belong to the supplied Blueprint")

    binding_by_id = {item.obligation_id: item for item in packet.obligation_bindings}
    blueprint_ids = {item.obligation_id for item in blueprint.obligations}
    if blueprint_ids != set(binding_by_id):
        raise SectionAuthoringError("Evidence Packet obligation bindings do not match Blueprint")
    authorized = set(packet.authorized_primary_evidence_ids) | set(packet.authorized_reference_evidence_ids)
    obligations: list[SectionAuthoringObligation] = []
    for obligation in blueprint.obligations:
        binding = binding_by_id[obligation.obligation_id]
        if not set(binding.accepted_evidence_ids).issubset(authorized):
            raise SectionAuthoringError(f"obligation has unauthorized accepted evidence: {obligation.obligation_id}")
        obligations.append(_compile_obligation(obligation, binding))

    required = tuple(item for item in obligations if item.required == REQUIRED)
    optional = tuple(item for item in obligations if item.required != REQUIRED)
    constraints = tuple(_constraint_payload(item) for item in (occurrence_constraints or ()))
    must_teach = _collect_constraint_values(constraints, "must_teach_facets", "required_facets")
    may_recap = _collect_constraint_values(constraints, "already_available_facets", "available_facets", "prerequisite_context")
    forbidden = _collect_constraint_values(
        constraints,
        "must_not_reteach_facets",
        "forbidden_reteach",
        "forbidden_content",
        "repeated_aspects_to_avoid",
    )
    contributions = _collect_constraint_values(
        constraints,
        "required_current_contribution",
        "contribution_goal",
        "intended_contribution",
        "new_facets",
        "extension_keys",
    )
    if not contributions and blueprint.expected_learning_outcome.strip():
        contributions = (blueprint.expected_learning_outcome.strip(),)

    source_gaps = list(packet.source_gaps)
    for item in required:
        if item.source_gap and not any(gap.get("obligation_id") == item.obligation_id for gap in source_gaps):
            source_gaps.append(
                {
                    "obligation_id": item.obligation_id,
                    "issue_type": SOURCE_GAP,
                    "missing_support": item.evidence_requirement,
                    "reason": "required obligation has no accepted authorized evidence",
                }
            )
    partial = tuple(
        {
            "obligation_id": item.obligation_id,
            "kind": item.kind,
            "accepted_evidence_ids": list(item.authorized_evidence_ids),
            "reason": "only the accepted evidence-supported portion may be authored",
        }
        for item in obligations
        if item.evidence_status == PARTIAL_OBLIGATION
    )
    requirements = _module_requirements(obligations)
    brief_id = f"authoring:{section_id}:{packet.packet_id.split(':')[-1]}"
    provenance = {
        "generator": "phase5d-deterministic-section-authoring-brief-v1",
        "book_plan_mutated": False,
        "blueprint_mutated": False,
        "packet_mutated": False,
        "writer_may_replan": False,
        "evidence_scope_expansion": False,
        "section_authoring_granularity": True,
        "occurrence_role_decision": "immutable constraint input",
    }
    return SectionAuthoringBrief(
        brief_id=brief_id,
        outline_node_id=section_id,
        blueprint_id=blueprint.blueprint_id,
        packet_id=packet.packet_id,
        source_book_plan_signature=expected_signature,
        chapter_id=chapter.chapter_id,
        section_no=section.section_no,
        section_title=section.title,
        section_purpose=blueprint.section_purpose,
        expected_learning_outcome=blueprint.expected_learning_outcome,
        current_task_action=blueprint.current_task_action,
        required_obligations=required,
        optional_obligations=optional,
        authorized_evidence_per_obligation={
            item.obligation_id: tuple(item.authorized_evidence_ids) for item in obligations
        },
        source_gaps=tuple(source_gaps),
        partial_obligations=partial,
        prior_verified_support=deepcopy(
            dict(prior_verified_support if prior_verified_support is not None else blueprint.prior_verified_support)
        ),
        occurrence_constraints=constraints,
        must_teach=must_teach,
        may_recap=may_recap,
        forbidden_reteach=forbidden,
        required_current_contribution=contributions,
        case_activity_requirement=requirements["case_activity"],
        exercise_requirement=requirements["exercise"],
        assessment_requirement=requirements["assessment"],
        summary_requirement=requirements["summary"],
        provenance=provenance,
    )


def author_section(
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    writer: Callable[[SectionAuthoringBrief], Mapping[str, Any]],
) -> RenderedSection:
    """Run a fake/wrapped writer and deterministically materialize its draft."""

    draft = writer(brief)
    if not isinstance(draft, Mapping):
        raise SectionAuthoringError("section writer must return a mapping draft")
    return materialize_section_draft(brief, packet, draft)


def materialize_section_draft(
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    draft: Mapping[str, Any],
) -> RenderedSection:
    """Validate and materialize a constrained draft; never repair or replan it."""

    if packet.packet_id != brief.packet_id or packet.blueprint_id != brief.blueprint_id:
        raise SectionAuthoringError("draft inputs do not belong to the SectionAuthoringBrief")
    body = str(draft.get("body") or draft.get("student_visible_body") or "").strip()
    case_activity = str(draft.get("case_activity") or "").strip()
    exercises = tuple(_clean_strings(draft.get("exercises") or ()))
    assessment = str(draft.get("assessment") or "").strip()
    summary = str(draft.get("summary") or "").strip()
    visible_text = "\n\n".join([body, case_activity, *exercises, assessment, summary]).strip()
    _reject_internal_labels(visible_text)
    _reject_forbidden_reteach(visible_text, brief.forbidden_reteach)

    obligation_map = _normalize_span_map(draft.get("obligation_span_map") or {})
    occurrence_map = _normalize_span_map(draft.get("occurrence_span_map") or {})
    allowed_obligation_ids = {item.obligation_id for item in brief.all_obligations}
    unknown_obligations = sorted(set(obligation_map) - allowed_obligation_ids)
    if unknown_obligations:
        raise SectionAuthoringError(f"draft maps unknown obligations: {unknown_obligations}")
    allowed_occurrence_ids = {
        _constraint_value(item, "occurrence_id")
        for item in brief.occurrence_constraints
        if _constraint_value(item, "occurrence_id")
    }
    unknown_occurrences = sorted(set(occurrence_map) - allowed_occurrence_ids)
    if unknown_occurrences:
        raise SectionAuthoringError(f"draft maps unknown occurrences: {unknown_occurrences}")

    binding_by_id = {item.obligation_id: item for item in packet.obligation_bindings}
    span_evidence_usage: list[dict[str, Any]] = []
    for obligation in brief.all_obligations:
        spans = obligation_map.get(obligation.obligation_id, ())
        binding = binding_by_id[obligation.obligation_id]
        if binding.status == SOURCE_GAP:
            if spans:
                raise SectionAuthoringError(
                    f"source-gap obligation cannot receive generated content: {obligation.obligation_id}"
                )
            continue
        if obligation.required == REQUIRED and not spans:
            raise SectionAuthoringError(f"required obligation has no rendered span: {obligation.obligation_id}")
        for span in spans:
            text = str(span.get("text") or "").strip()
            if not text or text not in visible_text:
                raise SectionAuthoringError(f"obligation span is not present in rendered section: {obligation.obligation_id}")
            evidence_ids = tuple(_clean_strings(span.get("evidence_ids") or ()))
            allowed = set(brief.authorized_evidence_per_obligation.get(obligation.obligation_id, ()))
            if not evidence_ids:
                raise SectionAuthoringError(
                    f"obligation span must identify authorized evidence: {obligation.obligation_id}"
                )
            if not set(evidence_ids).issubset(allowed):
                raise SectionAuthoringError(
                    f"obligation span uses unauthorized evidence: {obligation.obligation_id}"
                )
            if binding.status == PARTIAL_OBLIGATION and not evidence_ids:
                raise SectionAuthoringError(
                    f"partial obligation span must identify its supported evidence: {obligation.obligation_id}"
                )
            span_evidence_usage.append(
                {
                    "obligation_id": obligation.obligation_id,
                    "evidence_ids": list(evidence_ids),
                    "span_id": str(span.get("span_id") or ""),
                    "support_status": binding.status,
                }
            )
    for occurrence_id, spans in occurrence_map.items():
        for span in spans:
            text = str(span.get("text") or "").strip()
            if not text or text not in visible_text:
                raise SectionAuthoringError(f"occurrence span is not present: {occurrence_id}")

    _validate_module_requirements(brief, binding_by_id, case_activity, exercises, assessment, summary)
    if not body and _has_deliverable_required_obligation(brief, binding_by_id):
        raise SectionAuthoringError("section body is empty while supported required teaching remains")

    explicit_usage = _normalize_evidence_usage(draft.get("evidence_usage") or ())
    for usage in explicit_usage:
        obligation_id = str(usage.get("obligation_id") or "")
        allowed = set(brief.authorized_evidence_per_obligation.get(obligation_id, ()))
        if obligation_id not in brief.authorized_evidence_per_obligation or not set(
            _clean_strings(usage.get("evidence_ids") or ())
        ).issubset(allowed):
            raise SectionAuthoringError(f"evidence usage is outside the obligation authorization: {obligation_id}")
    evidence_usage = tuple(explicit_usage or span_evidence_usage)
    draft_provenance = draft.get("generation_provenance")
    if not isinstance(draft_provenance, Mapping):
        draft_provenance = {"writer": "fake-or-wrapped-writer"}

    core_source_gaps = tuple(
        f"CORE_SOURCE_GAP:{item.obligation_id}"
        for item in brief.required_obligations
        if item.source_gap and item.kind in {CONCEPT_PRINCIPLE, PROCEDURE_OPERATION}
    )
    local_gaps = tuple(
        f"SOURCE_GAP:{item.obligation_id}"
        for item in brief.required_obligations
        if item.source_gap and item.kind not in {CONCEPT_PRINCIPLE, PROCEDURE_OPERATION}
    )
    blocked = bool(core_source_gaps)
    render_status = BLOCKED_CORE_SOURCE_GAP if blocked else (RENDERED_PARTIAL if local_gaps or brief.partial_obligations else RENDERED)
    provenance = {
        **dict(draft_provenance),
        "materializer": "phase5d-deterministic-section-materializer-v1",
        "brief_id": brief.brief_id,
        "packet_id": packet.packet_id,
        "evidence_scope_expanded": False,
        "internal_labels_rendered": False,
        "writer_replanned": False,
    }
    return RenderedSection(
        section_id=brief.outline_node_id,
        outline_node_id=brief.outline_node_id,
        blueprint_id=brief.blueprint_id,
        packet_id=brief.packet_id,
        body=body,
        case_activity=case_activity,
        exercises=exercises,
        assessment=assessment,
        summary=summary,
        obligation_span_map=obligation_map,
        occurrence_span_map=occurrence_map,
        evidence_usage=evidence_usage,
        generation_provenance=provenance,
        render_status=render_status,
        blocked=blocked,
        block_reasons=core_source_gaps + local_gaps,
    )


def _compile_obligation(
    obligation: TeachingObligation,
    binding: ObligationEvidenceBinding,
) -> SectionAuthoringObligation:
    if binding.status not in OBLIGATION_STATUSES:
        raise SectionAuthoringError(f"unknown evidence status: {binding.status}")
    return SectionAuthoringObligation(
        obligation_id=obligation.obligation_id,
        kind=obligation.kind,
        objective=obligation.objective,
        required=obligation.required,
        evidence_status=binding.status,
        authorized_evidence_ids=tuple(binding.accepted_evidence_ids),
        target_knowledge_ids=obligation.target_knowledge_ids,
        linked_occurrence_ids=obligation.linked_occurrence_ids,
        evidence_requirement=obligation.evidence_requirement,
        depth=obligation.depth,
        source_gap=binding.status == SOURCE_GAP,
        source_field=str(obligation.provenance.get("source_field") or ""),
    )


def _module_requirements(obligations: Iterable[SectionAuthoringObligation]) -> dict[str, str]:
    result = {"case_activity": "NOT_APPLICABLE", "exercise": "NOT_APPLICABLE", "assessment": "NOT_APPLICABLE", "summary": "NOT_APPLICABLE"}
    for item in obligations:
        status = item.required if item.required in {REQUIRED, OPTIONAL} else "NOT_APPLICABLE"
        source_field = str(item.objective).lower()
        if item.kind == CASE_ACTIVITY:
            result["case_activity"] = _merge_requirement(result["case_activity"], status)
        elif item.kind == SUMMARY:
            result["summary"] = _merge_requirement(result["summary"], status)
        elif item.kind == "assessment_exercise":
            # Blueprint provenance distinguishes exercise and assessment; the
            # kind remains one of the deliberately small Phase 5B kinds.
            if "exercise_purpose" in item.source_field:
                result["exercise"] = _merge_requirement(result["exercise"], status)
            if "assessment_purpose" in item.source_field:
                result["assessment"] = _merge_requirement(result["assessment"], status)
    return result


def _merge_requirement(current: str, incoming: str) -> str:
    if current == REQUIRED or incoming == REQUIRED:
        return REQUIRED
    if current == OPTIONAL or incoming == OPTIONAL:
        return OPTIONAL
    return "NOT_APPLICABLE"


def _validate_module_requirements(
    brief: SectionAuthoringBrief,
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
    case_activity: str,
    exercises: tuple[str, ...],
    assessment: str,
    summary: str,
) -> None:
    checks = (
        ("case_activity", brief.case_activity_requirement, bool(case_activity), CASE_ACTIVITY),
        ("exercise", brief.exercise_requirement, bool(exercises), "assessment_exercise"),
        ("assessment", brief.assessment_requirement, bool(assessment), "assessment_exercise"),
        ("summary", brief.summary_requirement, bool(summary), SUMMARY),
    )
    for name, requirement, present, kind in checks:
        if requirement != REQUIRED or present:
            continue
        obligations = [
            item
            for item in brief.required_obligations
            if item.kind == kind
            and (
                kind != "assessment_exercise"
                or (name == "exercise" and "exercise_purpose" in item.source_field)
                or (name == "assessment" and "assessment_purpose" in item.source_field)
            )
        ]
        if obligations and all(binding_by_id[item.obligation_id].status == SOURCE_GAP for item in obligations):
            continue
        raise SectionAuthoringError(f"required {name} module is missing from section draft")


def _has_deliverable_required_obligation(
    brief: SectionAuthoringBrief,
    bindings: Mapping[str, ObligationEvidenceBinding],
) -> bool:
    return any(
        item.required == REQUIRED and bindings[item.obligation_id].status != SOURCE_GAP
        for item in brief.required_obligations
    )


def _normalize_span_map(raw: Mapping[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    if not isinstance(raw, Mapping):
        raise SectionAuthoringError("span map must be a mapping")
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for key, value in raw.items():
        entries = value if isinstance(value, (list, tuple)) else [value]
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(entries):
            if isinstance(item, Mapping):
                payload = dict(item)
            else:
                payload = {"text": str(item or "")}
            payload.setdefault("span_id", f"{key}:span:{index + 1:02d}")
            normalized.append(payload)
        result[str(key)] = tuple(normalized)
    return result


def _normalize_evidence_usage(raw: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SectionAuthoringError("evidence_usage entries must be mappings")
        result.append(dict(item))
    return result


def _constraint_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return deepcopy(dict(item))
    names = (
        "occurrence_id", "role", "already_available_facets", "available_facets", "required_facets",
        "must_teach_facets", "must_not_reteach_facets", "extension_keys", "repeated_aspects_to_avoid",
        "prerequisite_context", "contribution_goal", "intended_contribution", "new_facets",
        "forbidden_content", "must_avoid_patterns", "availability_source_occurrence_ids",
    )
    return {name: deepcopy(getattr(item, name)) for name in names if hasattr(item, name)}


def _constraint_value(item: Mapping[str, Any], name: str) -> str:
    return str(item.get(name) or "").strip()


def _collect_constraint_values(constraints: Iterable[Mapping[str, Any]], *names: str) -> tuple[str, ...]:
    values: list[str] = []
    for constraint in constraints:
        for name in names:
            value = constraint.get(name)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item).strip() for item in value if str(item).strip())
            elif value:
                values.append(str(value).strip())
    return tuple(_dedupe(values))


def _reject_internal_labels(text: str) -> None:
    labels = r"\b(?:ORIENTED|EXPLAIN|PERFORM|ANALYZE|INTRO|TEACH|RECALL|APPLY|EXTEND)\b"
    match = re.search(labels, text)
    if match:
        raise SectionAuthoringError(f"student-visible draft leaks internal semantic label: {match.group(0)}")


def _reject_forbidden_reteach(text: str, forbidden: Iterable[str]) -> None:
    lowered = text.casefold()
    for value in forbidden:
        pattern = str(value or "").strip()
        if not pattern or pattern.upper() in {"ORIENTED", "EXPLAIN", "PERFORM", "ANALYZE"}:
            continue
        if len(pattern) >= 4 and pattern.casefold() in lowered:
            raise SectionAuthoringError(f"draft contains forbidden reteach content: {pattern}")


def _find_section(book_plan: BookPlan, section_id: str) -> tuple[Any, BookSectionPlan] | None:
    for chapter in book_plan.chapters:
        for section in chapter.sections:
            if section.section_id == section_id:
                return chapter, section
    return None


def _clean_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _dedupe(values: Iterable[str]) -> list[str]:
    return _clean_strings(values)


def _map_to_lists(value: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {key: [deepcopy(dict(item)) for item in entries] for key, entries in value.items()}
