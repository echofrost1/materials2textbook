"""Deterministic section-level teaching responsibilities.

Phase 5B introduces this module as a read-only overlay between a validated
BookPlan and occurrence-level semantic writing.  The overlay describes what a
section must deliver; it never changes the BookPlan, assigns occurrence roles,
creates prerequisites, or binds individual EvidenceChunks.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from materials2textbook.agents.book_plan_completeness import (
    MODULE_NOT_APPLICABLE,
    MODULE_OPTIONAL,
    MODULE_REQUIRED,
    module_intent_requirements,
)
from materials2textbook.knowledge_map.outline import book_plan_fingerprint
from materials2textbook.schemas import BookPlan, BookSectionPlan


REQUIRED = "REQUIRED"
OPTIONAL = "OPTIONAL"
NOT_APPLICABLE = "NOT_APPLICABLE"

CONCEPT_PRINCIPLE = "concept_principle"
PROCEDURE_OPERATION = "procedure_operation"
PARAMETER_CONDITION = "parameter_condition"
OBSERVATION_QUALITY_JUDGEMENT = "observation_quality_judgement"
SAFETY_COMMON_ERROR = "safety_common_error"
CASE_ACTIVITY = "case_activity"
ASSESSMENT_EXERCISE = "assessment_exercise"
SUMMARY = "summary"

ALLOWED_OBLIGATION_KINDS = frozenset(
    {
        CONCEPT_PRINCIPLE,
        PROCEDURE_OPERATION,
        PARAMETER_CONDITION,
        OBSERVATION_QUALITY_JUDGEMENT,
        SAFETY_COMMON_ERROR,
        CASE_ACTIVITY,
        ASSESSMENT_EXERCISE,
        SUMMARY,
    }
)
ALLOWED_REQUIREMENT_STATUSES = frozenset({REQUIRED, OPTIONAL, NOT_APPLICABLE})


class SectionTeachingBlueprintError(ValueError):
    """Raised when a blueprint is requested for an invalid/unfrozen plan."""


@dataclass(frozen=True)
class TeachingObligation:
    """One section responsibility, independent of occurrence role."""

    obligation_id: str
    kind: str
    objective: str
    required: str
    target_knowledge_ids: tuple[str, ...] = ()
    linked_occurrence_ids: tuple[str, ...] = ()
    evidence_requirement: str = ""
    depth: str = "FOCUSED"
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target_knowledge_ids"] = list(self.target_knowledge_ids)
        value["linked_occurrence_ids"] = list(self.linked_occurrence_ids)
        value["provenance"] = deepcopy(dict(self.provenance))
        return value


@dataclass(frozen=True)
class SectionTeachingBlueprint:
    """A read-only teaching contract attached to one frozen outline section."""

    blueprint_id: str
    outline_node_id: str
    source_book_plan_signature: str
    section_purpose: str
    expected_learning_outcome: str
    current_task_action: str
    prior_verified_support: Mapping[str, Any] = field(default_factory=dict)
    # This is the section-owned candidate pool, not an obligation-level
    # EvidenceChunk binding.  Section Evidence Packet owns that later step.
    authorized_evidence_ids: tuple[str, ...] = ()
    obligations: tuple[TeachingObligation, ...] = ()
    source_gaps: tuple[Mapping[str, Any], ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "outline_node_id": self.outline_node_id,
            "source_book_plan_signature": self.source_book_plan_signature,
            "section_purpose": self.section_purpose,
            "expected_learning_outcome": self.expected_learning_outcome,
            "current_task_action": self.current_task_action,
            "prior_verified_support": deepcopy(dict(self.prior_verified_support)),
            "authorized_evidence_ids": list(self.authorized_evidence_ids),
            "obligations": [item.to_dict() for item in self.obligations],
            "source_gaps": [deepcopy(dict(item)) for item in self.source_gaps],
            "provenance": deepcopy(dict(self.provenance)),
        }


def build_section_teaching_blueprint(
    book_plan: BookPlan,
    section_id: str,
    *,
    completeness_report: Any,
    source_book_plan_signature: str,
    prior_verified_support: Mapping[str, Any] | None = None,
    occurrences: Iterable[Any] | None = None,
    authorized_evidence_ids: Iterable[str] | None = None,
) -> SectionTeachingBlueprint:
    """Build one blueprint without mutating the supplied plan.

    ``completeness_report.safe_to_freeze`` is the production admission gate.
    The caller must also provide the full-plan fingerprint captured at freeze;
    this prevents a report for one plan from being paired with another plan.
    ``authorized_evidence_ids`` is only an ownership pool.  Exact chunk
    binding is deliberately deferred to Section Evidence Packet.
    """

    section = _find_section(book_plan, section_id)
    if section is None:
        raise SectionTeachingBlueprintError(f"unknown outline section: {section_id}")

    expected_signature = book_plan_fingerprint(book_plan)
    if not source_book_plan_signature or source_book_plan_signature != expected_signature:
        raise SectionTeachingBlueprintError(
            "source_book_plan_signature does not match the supplied BookPlan fingerprint"
        )
    report_signature = str(getattr(completeness_report, "plan_fingerprint", "") or "")
    if report_signature != expected_signature:
        raise SectionTeachingBlueprintError(
            "completeness report belongs to a different BookPlan"
        )
    if not bool(getattr(completeness_report, "safe_to_freeze", False)):
        raise SectionTeachingBlueprintError(
            "Phase 5B requires a BookPlan that passed the completeness freeze gate"
        )

    chapter, _ = section
    plan_section = section[1]
    target_ids = tuple(_dedupe_strings(plan_section.knowledge_point_ids))
    linked_occurrence_ids = _linked_occurrence_ids(occurrences, section_id)
    evidence_ids = _ownership_ids(plan_section, authorized_evidence_ids)
    obligations = _derive_obligations(
        chapter_id=chapter.chapter_id,
        section=plan_section,
        target_knowledge_ids=target_ids,
        linked_occurrence_ids=linked_occurrence_ids,
    )
    source_gaps = _source_gaps(obligations, evidence_ids)
    blueprint_id = f"blueprint:{section_id}:{expected_signature[:12]}"
    provenance = {
        "generator": "phase5b-deterministic-section-teaching-blueprint-v1",
        "source_outline_node_id": section_id,
        "source_book_plan_signature": expected_signature,
        "completeness_report_fingerprint": report_signature,
        "authorized_evidence_ids": list(evidence_ids),
        "evidence_binding_deferred": True,
        "book_plan_mutated": False,
        "occurrence_role_decision": "deferred to occurrence semantic layer",
    }
    return SectionTeachingBlueprint(
        blueprint_id=blueprint_id,
        outline_node_id=section_id,
        source_book_plan_signature=expected_signature,
        section_purpose=str(plan_section.section_purpose or "").strip(),
        expected_learning_outcome=str(plan_section.expected_learning_outcome or "").strip(),
        current_task_action=str(plan_section.current_task_action or "").strip(),
        prior_verified_support=deepcopy(dict(prior_verified_support or {})),
        authorized_evidence_ids=evidence_ids,
        obligations=tuple(obligations),
        source_gaps=tuple(source_gaps),
        provenance=provenance,
    )


def validate_section_teaching_blueprint(
    blueprint: SectionTeachingBlueprint,
    *,
    expected_source_signature: str | None = None,
) -> list[str]:
    """Return deterministic structural violations for audit/tests."""

    issues: list[str] = []
    if expected_source_signature and blueprint.source_book_plan_signature != expected_source_signature:
        issues.append("SOURCE_BOOK_PLAN_SIGNATURE_MISMATCH")
    if not blueprint.outline_node_id:
        issues.append("MISSING_OUTLINE_NODE_ID")
    if not blueprint.blueprint_id:
        issues.append("MISSING_BLUEPRINT_ID")
    seen: set[str] = set()
    for obligation in blueprint.obligations:
        if obligation.obligation_id in seen:
            issues.append(f"DUPLICATE_OBLIGATION_ID:{obligation.obligation_id}")
        seen.add(obligation.obligation_id)
        if obligation.kind not in ALLOWED_OBLIGATION_KINDS:
            issues.append(f"UNKNOWN_OBLIGATION_KIND:{obligation.kind}")
        if obligation.required not in ALLOWED_REQUIREMENT_STATUSES:
            issues.append(f"UNKNOWN_REQUIREMENT_STATUS:{obligation.required}")
        if not obligation.objective.strip():
            issues.append(f"EMPTY_OBLIGATION_OBJECTIVE:{obligation.obligation_id}")
        if obligation.required == REQUIRED and not obligation.evidence_requirement:
            issues.append(f"MISSING_EVIDENCE_REQUIREMENT:{obligation.obligation_id}")
    return issues


def _find_section(book_plan: BookPlan, section_id: str) -> tuple[Any, BookSectionPlan] | None:
    for chapter in book_plan.chapters:
        for section in chapter.sections:
            if section.section_id == section_id:
                return chapter, section
    return None


def _ownership_ids(section: BookSectionPlan, supplied: Iterable[str] | None) -> tuple[str, ...]:
    section_pool = set(_dedupe_strings([*section.primary_material_ids, *section.reference_material_ids]))
    if supplied is None:
        values = _dedupe_strings([*section.primary_material_ids, *section.reference_material_ids])
    else:
        values = _dedupe_strings(supplied)
        unauthorized = sorted(set(values) - section_pool)
        if unauthorized:
            raise SectionTeachingBlueprintError(
                "authorized evidence exceeds the section's frozen BookPlan ownership: "
                + ", ".join(unauthorized)
            )
    return tuple(_dedupe_strings(values))


def _linked_occurrence_ids(occurrences: Iterable[Any] | None, section_id: str) -> tuple[str, ...]:
    result: list[str] = []
    for occurrence in occurrences or ():
        occurrence_section = _value(occurrence, "section_id")
        occurrence_id = _value(occurrence, "occurrence_id")
        if occurrence_id and occurrence_section == section_id:
            result.append(occurrence_id)
    return tuple(_dedupe_strings(result))


def _derive_obligations(
    *,
    chapter_id: str,
    section: BookSectionPlan,
    target_knowledge_ids: tuple[str, ...],
    linked_occurrence_ids: tuple[str, ...],
) -> list[TeachingObligation]:
    common = {
        "target_knowledge_ids": target_knowledge_ids,
        "linked_occurrence_ids": linked_occurrence_ids,
    }
    definitions: list[tuple[str, str, str, str, str, str]] = []
    purpose = str(section.section_purpose or "").strip()
    outcome = str(section.expected_learning_outcome or "").strip()
    action = str(section.current_task_action or "").strip()
    scope = _dedupe_strings(section.knowledge_scope or section.knowledge_point_ids)
    scope_text = ", ".join(scope)

    concept_objective = purpose or outcome or (f"Establish the section concepts: {scope_text}" if scope_text else section.title)
    definitions.append(
        (
            CONCEPT_PRINCIPLE,
            concept_objective,
            REQUIRED,
            "conceptual/principle support for the declared section scope",
            "CORE",
            "section_purpose + expected_learning_outcome + knowledge_scope",
        )
    )
    if action:
        definitions.append(
            (
                PROCEDURE_OPERATION,
                action,
                REQUIRED,
                "procedural/operational support for the current task action",
                "CORE",
                "current_task_action",
            )
        )

    text = " ".join([purpose, outcome, action, scope_text]).lower()
    if _contains_any(text, _PARAMETER_TERMS):
        definitions.append(
            (
                PARAMETER_CONDITION,
                "Make the declared parameters, conditions, constraints, or rules usable in this section.",
                REQUIRED,
                "evidence for the stated parameter/condition/constraint",
                "FOCUSED",
                "section metadata terminology",
            )
        )
    if _contains_any(text, _OBSERVATION_TERMS):
        definitions.append(
            (
                OBSERVATION_QUALITY_JUDGEMENT,
                "Observe or judge the quality/result conditions named by the section.",
                REQUIRED,
                "observable result or quality-judgement support",
                "FOCUSED",
                "section metadata terminology",
            )
        )
    if _contains_any(text, _SAFETY_TERMS):
        definitions.append(
            (
                SAFETY_COMMON_ERROR,
                "Address the declared safety conditions and common errors relevant to this section.",
                REQUIRED,
                "safety/common-error support for the declared scope",
                "FOCUSED",
                "section metadata terminology",
            )
        )

    module_requirements = module_intent_requirements(section)
    module_specs = (
        ("case", CASE_ACTIVITY, "case_purpose", "case/activity evidence for the planned context"),
        ("exercise", ASSESSMENT_EXERCISE, "exercise_purpose", "exercise evidence aligned to the planned practice"),
        ("assessment", ASSESSMENT_EXERCISE, "assessment_purpose", "assessment evidence aligned to the planned outcome"),
        ("activity", CASE_ACTIVITY, "activity_purpose", "activity evidence aligned to the planned observation/discussion"),
    )
    for module_name, kind, field_name, evidence_requirement in module_specs:
        status = module_requirements[module_name]
        objective = str(getattr(section, field_name) or "").strip()
        if status == MODULE_NOT_APPLICABLE:
            continue
        if not objective and status == MODULE_OPTIONAL:
            continue
        if not objective:
            objective = _module_objective(section, module_name)
        definitions.append(
            (
                kind,
                objective,
                REQUIRED if status == MODULE_REQUIRED else OPTIONAL,
                evidence_requirement,
                "APPLIED" if status == MODULE_REQUIRED else "MINIMAL",
                f"module_intent_requirements.{module_name} + {field_name}",
            )
        )

    if outcome:
        definitions.append(
            (
                SUMMARY,
                f"Summarize the achieved learning outcome: {outcome}",
                OPTIONAL,
                "summary may be derived from already-supported section content",
                "MINIMAL",
                "expected_learning_outcome",
            )
        )

    obligations: list[TeachingObligation] = []
    kind_counts: dict[str, int] = {}
    for kind, objective, status, evidence_requirement, depth, source_field in definitions:
        clean_objective = str(objective or "").strip()
        if not clean_objective:
            continue
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        obligation_id = f"{section.section_id}:obligation:{kind}:{kind_counts[kind]:02d}"
        obligations.append(
            TeachingObligation(
                obligation_id=obligation_id,
                kind=kind,
                objective=clean_objective,
                required=status,
                evidence_requirement=evidence_requirement,
                depth=depth,
                provenance={
                    "source_chapter_id": chapter_id,
                    "source_section_id": section.section_id,
                    "source_field": source_field,
                    "book_plan_ownership": "unchanged",
                    "occurrence_role": "not decided here",
                },
                **common,
            )
        )
    return obligations


def _source_gaps(
    obligations: Iterable[TeachingObligation],
    authorized_evidence_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    if authorized_evidence_ids:
        return []
    return [
        {
            "gap_id": f"{obligation.obligation_id}:source-gap",
            "obligation_id": obligation.obligation_id,
            "issue_type": "SOURCE_GAP",
            "status": "EVIDENCE_REQUIRED",
            "missing_support": obligation.evidence_requirement,
            "reason": "required section responsibility has no authorized evidence ownership pool",
            "provenance": "deterministic blueprint evidence-pool check",
        }
        for obligation in obligations
        if obligation.required == REQUIRED and obligation.evidence_requirement
    ]


def _module_objective(section: BookSectionPlan, module_name: str) -> str:
    subject = section.title or "the planned section"
    outcome = section.expected_learning_outcome or section.current_task_action or section.section_purpose
    if module_name == "case":
        return f"Use a bounded case or activity to apply {subject}: {outcome}".strip()
    if module_name == "exercise":
        return f"Practice the observable task outcome for {subject}: {outcome}".strip()
    if module_name == "assessment":
        return f"Assess the observable outcome for {subject}: {outcome}".strip()
    return f"Use an activity to observe or discuss {subject}: {outcome}".strip()


def _value(item: Any, name: str) -> str:
    if isinstance(item, Mapping):
        return str(item.get(name) or "").strip()
    return str(getattr(item, name, "") or "").strip()


def _dedupe_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


_PARAMETER_TERMS = (
    "parameter", "condition", "constraint", "setting", "rule", "requirement", "threshold", "limit",
    "参数", "条件", "约束", "设置", "规则", "要求", "阈值", "限制",
)
_OBSERVATION_TERMS = (
    "observe", "observation", "quality", "judge", "compare", "identify", "inspect", "verify", "result",
    "观察", "质量", "判断", "比较", "识别", "检查", "验证", "结果", "现象",
)
_SAFETY_TERMS = (
    "safety", "safe", "risk", "error", "warning", "hazard", "precaution", "misconception",
    "安全", "风险", "错误", "警告", "危险", "注意", "防护", "常见问题", "缺陷",
)
