"""Section-level authoring contracts and deterministic draft materialization.

Phase 5D changes the authoring unit from an independently rendered
occurrence to one coherent section.  Occurrences remain semantic/audit
constraints.  This module deliberately stops at a fake-writer boundary: it
does not call a model, change BookPlan/Blueprint/Packet, or export a DigitalBook.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
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
from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (
    CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
    ClaimStatus,
    audit_rendered_claims,
)
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
from materials2textbook.schemas import (
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


RENDERED = "RENDERED"
RENDERED_PARTIAL = "RENDERED_PARTIAL"
BLOCKED_CORE_SOURCE_GAP = "BLOCKED_CORE_SOURCE_GAP"


class SectionAuthoringError(ValueError):
    """Raised when a draft violates the immutable section authoring contract."""


def build_section_authoring_messages(
    brief: "SectionAuthoringBrief",
    packet: SectionEvidencePacket,
    evidence_chunks: Iterable[EvidenceChunk],
) -> list[dict[str, str]]:
    """Build the production section-writer contract.

    The model receives only the already-bound packet spans.  It declares
    ordered semantic blocks; it never owns section fields, character offsets,
    occurrence anchors, or evidence outside the packet.
    """

    chunk_by_id = {item.chunk_id: item for item in evidence_chunks}
    bindings = {item.obligation_id: item for item in packet.obligation_bindings}
    obligations: list[dict[str, Any]] = []
    for item in brief.all_obligations:
        binding = bindings[item.obligation_id]
        spans = [
            str(span.get("text") or "").strip()
            for span in binding.accepted_evidence_spans
            if str(span.get("text") or "").strip()
        ]
        # A packet span is authoritative.  Falling back to the supplied chunk
        # text is only useful for packets produced by older deterministic
        # fixtures that did not persist an excerpt; IDs remain the whitelist.
        if not spans:
            spans = [
                str(chunk_by_id[evidence_id].content or chunk_by_id[evidence_id].summary or "").strip()
                for evidence_id in binding.accepted_evidence_ids
                if evidence_id in chunk_by_id
            ]
        obligations.append(
            {
                "obligation_id": item.obligation_id,
                "kind": item.kind,
                "required": item.required,
                "objective": item.objective,
                "evidence_status": binding.status,
                "authorized_evidence_ids": list(binding.accepted_evidence_ids),
                "evidence_spans": spans,
                "forbidden_scope": (
                    "all professional claims for this obligation"
                    if binding.status == SOURCE_GAP
                    else "unsupported remainder beyond the accepted evidence"
                    if binding.status == PARTIAL_OBLIGATION
                    else "claims outside the accepted evidence"
                ),
            }
        )
    contract = {
        "section_identity": {
            "outline_node_id": brief.outline_node_id,
            "chapter_id": brief.chapter_id,
            "section_no": brief.section_no,
            "title": brief.section_title,
        },
        "section_purpose": brief.section_purpose,
        "expected_learning_outcome": brief.expected_learning_outcome,
        "current_task_action": brief.current_task_action,
        "prior_verified_support": deepcopy(dict(brief.prior_verified_support)),
        "must_teach": list(brief.must_teach),
        "may_recap": list(brief.may_recap),
        "forbidden_reteach": list(brief.forbidden_reteach),
        "required_current_contribution": list(brief.required_current_contribution),
        "occurrence_constraints": [deepcopy(dict(item)) for item in brief.occurrence_constraints],
        "module_requirements": {
            "case_activity": brief.case_activity_requirement,
            "exercise": brief.exercise_requirement,
            "assessment": brief.assessment_requirement,
            "summary": brief.summary_requirement,
        },
        "obligations": obligations,
        "source_gaps": [deepcopy(dict(item)) for item in brief.source_gaps],
    }
    system = (
        "You are the section-level author for a vocational digital textbook. "
        "Write one coherent student-visible section from this immutable contract. "
        "Use only the evidence spans and IDs supplied in the contract; do not use "
        "outside knowledge or broaden evidence ownership. Do not replan roles, "
        "facets, prerequisites, obligations, or module requirements. Internal "
        "labels such as EXPLAIN, PERFORM, ANALYZE, TEACH, APPLY, RECALL, and "
        "EXTEND must never appear in student-visible text. Return only one JSON "
        "object with ordered semantic blocks and no Markdown fences or commentary."
    )
    user = (
        "Return exactly this shape:\n"
        '{"blocks":[{"block_id":"b01","channel":"body|case_activity|exercise|assessment|summary",'
        '"text":"student-visible text","intended_obligation_ids":[],"intended_occurrence_ids":[],"evidence_ids":[]}],'
        '"generation_provenance":{"writer":"section-qwen"}}\n\n'
        "The blocks array is the complete section in reading order. Every block "
        "must contain all six block keys. Do not return body/summary/offset/span "
        "fields outside blocks. Evidence IDs on a block must be authorized for "
        "every obligation named by that block. For PARTIAL evidence, write only "
        "the supported portion; for SOURCE_GAP, do not write a professional fact. "
        "Make the teaching sequence natural and concrete when the evidence "
        "supports it, including procedures, conditions, observable results, "
        "case/activity, exercise, assessment, and summary only when required.\n\n"
        "IMMUTABLE AUTHORING CONTRACT:\n" + json.dumps(contract, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_section_authoring_response(raw: str) -> dict[str, Any]:
    """Parse a model response without accepting a second prose representation."""

    cleaned = str(raw or "").strip()
    if not cleaned:
        raise SectionAuthoringError("section writer returned empty output")
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise SectionAuthoringError("section writer returned invalid JSON")
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise SectionAuthoringError("section writer returned invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise SectionAuthoringError("section writer response must be a JSON object")
    return dict(value)


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
        "evidence_claim_scope": {
            item.obligation_id: {
                "status": item.evidence_status,
                "allowed_evidence_ids": list(item.authorized_evidence_ids),
                "supported_spans": [
                    deepcopy(dict(span))
                    for span in binding_by_id[item.obligation_id].accepted_evidence_spans
                ],
                "forbidden_scope": (
                    "all professional claims for this obligation"
                    if item.evidence_status == SOURCE_GAP
                    else "unsupported remainder beyond accepted evidence"
                    if item.evidence_status == PARTIAL_OBLIGATION
                    else "claims outside authorized evidence"
                ),
            }
            for item in obligations
        },
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
    *,
    claim_judge: Any | None = None,
) -> RenderedSection:
    """Run a fake/wrapped writer and deterministically materialize its draft."""

    draft = writer(brief)
    if not isinstance(draft, Mapping):
        raise SectionAuthoringError("section writer must return a mapping draft")
    return materialize_section_draft(brief, packet, draft, claim_judge=claim_judge)


def materialize_section_draft(
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    draft: Mapping[str, Any],
    *,
    claim_judge: Any | None = None,
) -> RenderedSection:
    """Validate and materialize a constrained block draft.

    The writer owns only semantic associations.  It never owns character
    offsets or the final span maps.  ``blocks`` are ordered and code-owned
    materialization computes every visible span from the resulting body.
    """

    if packet.packet_id != brief.packet_id or packet.blueprint_id != brief.blueprint_id:
        raise SectionAuthoringError("draft inputs do not belong to the SectionAuthoringBrief")
    if "obligation_span_map" in draft or "occurrence_span_map" in draft:
        raise SectionAuthoringError(
            "model-authored exact span maps are not accepted; return ordered blocks"
        )

    raw_blocks = draft.get("blocks")
    if raw_blocks is None:
        raw_blocks = []
    if not isinstance(raw_blocks, (list, tuple)):
        raise SectionAuthoringError("section writer must return an ordered blocks list")
    model_owned_fields = {
        "body": draft.get("body") or draft.get("student_visible_body"),
        "case_activity": draft.get("case_activity"),
        "assessment": draft.get("assessment"),
        "summary": draft.get("summary"),
    }
    if any(str(value or "").strip() for value in model_owned_fields.values()):
        raise SectionAuthoringError("student-visible text must be represented by ordered blocks")
    if draft.get("exercises"):
        raise SectionAuthoringError("student-visible exercises must be represented by ordered blocks")
    if draft.get("evidence_usage"):
        raise SectionAuthoringError("evidence usage spans are materializer-owned; return block evidence_ids only")

    allowed_obligation_ids = {item.obligation_id for item in brief.all_obligations}
    allowed_occurrence_ids = {
        _constraint_value(item, "occurrence_id")
        for item in brief.occurrence_constraints
        if _constraint_value(item, "occurrence_id")
    }
    binding_by_id = {item.obligation_id: item for item in packet.obligation_bindings}
    blocks = _normalize_writer_blocks(
        raw_blocks,
        allowed_obligation_ids=allowed_obligation_ids,
        allowed_occurrence_ids=allowed_occurrence_ids,
        brief=brief,
        binding_by_id=binding_by_id,
    )

    # Blocks are the only source for every student-visible section field.
    body_parts = [item["text"] for item in blocks if item["channel"] == "body"]
    body = "\n\n".join(body_parts).strip()
    block_case_activity = [item["text"] for item in blocks if item["channel"] == "case_activity"]
    block_exercises = [item["text"] for item in blocks if item["channel"] == "exercise"]
    block_assessment = [item["text"] for item in blocks if item["channel"] == "assessment"]
    block_summary = [item["text"] for item in blocks if item["channel"] == "summary"]
    case_activity = "\n\n".join(block_case_activity).strip()
    exercises = tuple(block_exercises)
    assessment = "\n\n".join(block_assessment).strip()
    summary = "\n\n".join(block_summary).strip()
    visible_segments = [item for item in [body, case_activity, *exercises, assessment, summary] if item]
    visible_text = "\n\n".join(visible_segments).strip()
    _reject_internal_labels(visible_text)
    _reject_forbidden_reteach(visible_text, brief.forbidden_reteach)

    obligation_map, occurrence_map, span_evidence_usage = _compute_deterministic_maps(
        blocks, body=body, visible_text=visible_text, brief=brief, binding_by_id=binding_by_id
    )
    coverage = _check_obligation_coverage(brief, binding_by_id, blocks)
    coverage_violations = tuple(
        item.obligation_id
        for item in brief.required_obligations
        if not item.source_gap and coverage.get(item.obligation_id, "VIOLATION") == "VIOLATION"
    )

    _validate_module_requirements(brief, binding_by_id, case_activity, exercises, assessment, summary)
    if not body and _has_deliverable_required_obligation(brief, binding_by_id):
        raise SectionAuthoringError("section body is empty while supported required teaching remains")

    evidence_usage = tuple(span_evidence_usage)
    claim_audit = _run_local_claim_audit(
        brief=brief,
        packet=packet,
        blocks=blocks,
        judge=claim_judge,
    )
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
    unsupported_claims = tuple(
        str(item.get("claim_id") or "")
        for item in claim_audit
        if item.get("final_status") == ClaimStatus.UNSUPPORTED
    )
    partial_claims = tuple(
        str(item.get("claim_id") or "")
        for item in claim_audit
        if item.get("final_status") == ClaimStatus.PARTIALLY_SUPPORTED
    )
    blocked_reasons = core_source_gaps + local_gaps + tuple(
        f"OBLIGATION_COVERAGE_VIOLATION:{item}" for item in coverage_violations
    ) + tuple(
        f"UNSUPPORTED_RENDERED_CLAIM:{item}" for item in unsupported_claims if item
    )
    blocked = bool(core_source_gaps or coverage_violations or unsupported_claims)
    render_status = BLOCKED_CORE_SOURCE_GAP if core_source_gaps else (
        RENDERED_PARTIAL if local_gaps or brief.partial_obligations or partial_claims or unsupported_claims else RENDERED
    )
    required_coverage_verified = all(
        item.source_gap or coverage.get(item.obligation_id) == "MATCH"
        for item in brief.required_obligations
    )
    provenance = {
        **dict(draft_provenance),
        "materializer": "phase5d-deterministic-section-materializer-v1",
        "brief_id": brief.brief_id,
        "packet_id": packet.packet_id,
        "evidence_scope_expanded": False,
        "internal_labels_rendered": False,
        "writer_replanned": False,
        "writer_contract": "ordered_blocks_only",
        "model_authored_spans": False,
        "span_coordinate_space": "materialized_student_visible_sequence",
        "obligation_coverage": coverage,
        "local_claim_evidence_audit": claim_audit,
        "local_claim_status_counts": _status_counts_from_claim_audit(claim_audit),
        "required_obligations_verified": required_coverage_verified,
        "verified_availability_eligible": not blocked and required_coverage_verified and not partial_claims,
        "grant_order": "materialization -> obligation coverage -> conformance -> local claim evidence audit",
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
        block_reasons=blocked_reasons,
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


def _normalize_writer_blocks(
    raw_blocks: Iterable[Any],
    *,
    allowed_obligation_ids: set[str],
    allowed_occurrence_ids: set[str],
    brief: SectionAuthoringBrief,
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
) -> list[dict[str, Any]]:
    """Validate the model's semantic block declarations.

    This is deliberately the only model-facing structure.  Character
    positions, span IDs and evidence excerpts are all produced later by
    deterministic code.
    """

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    channel_order = {"body": 0, "case_activity": 1, "exercise": 2, "assessment": 3, "summary": 4}
    last_channel = -1
    for index, raw in enumerate(raw_blocks, start=1):
        if not isinstance(raw, Mapping):
            raise SectionAuthoringError(f"writer block {index} must be an object")
        block_id = str(raw.get("block_id") or "").strip()
        text = str(raw.get("text") or "").strip()
        if not block_id or not text:
            raise SectionAuthoringError(f"writer block {index} requires block_id and non-empty text")
        if block_id in seen:
            raise SectionAuthoringError(f"duplicate writer block_id: {block_id}")
        seen.add(block_id)
        obligation_ids = _block_id_list(raw.get("intended_obligation_ids"), "intended_obligation_ids", block_id)
        occurrence_ids = _block_id_list(raw.get("intended_occurrence_ids"), "intended_occurrence_ids", block_id)
        evidence_ids = tuple(_clean_strings(raw.get("evidence_ids") or ()))
        unknown_obligations = sorted(set(obligation_ids) - allowed_obligation_ids)
        if unknown_obligations:
            raise SectionAuthoringError(f"block maps unknown obligations: {unknown_obligations}")
        unknown_occurrences = sorted(set(occurrence_ids) - allowed_occurrence_ids)
        if unknown_occurrences:
            raise SectionAuthoringError(f"block maps unknown occurrences: {unknown_occurrences}")
        if not obligation_ids and evidence_ids:
            raise SectionAuthoringError(f"discourse-only block cannot claim evidence: {block_id}")
        if obligation_ids:
            if any(binding_by_id[item].status == SOURCE_GAP for item in obligation_ids):
                raise SectionAuthoringError(
                    "source-gap obligation cannot receive generated content: "
                    + ", ".join(item for item in obligation_ids if binding_by_id[item].status == SOURCE_GAP)
                )
            authorized_sets = [
                set(brief.authorized_evidence_per_obligation.get(item, ()))
                for item in obligation_ids
            ]
            common_authorized = set.intersection(*authorized_sets) if authorized_sets else set()
            if not evidence_ids:
                raise SectionAuthoringError(f"teaching block must identify authorized evidence: {block_id}")
            if not set(evidence_ids).issubset(common_authorized):
                raise SectionAuthoringError(f"block uses unauthorized evidence: {block_id}")
        channel = str(raw.get("channel") or "body").strip().lower()
        if channel not in {"body", "case_activity", "exercise", "assessment", "summary"}:
            raise SectionAuthoringError(f"unknown writer block channel: {channel}")
        if channel_order[channel] < last_channel:
            raise SectionAuthoringError(
                "writer blocks must follow deterministic channel order: body, case_activity, exercise, assessment, summary"
            )
        last_channel = channel_order[channel]
        result.append(
            {
                "block_id": block_id,
                "text": text,
                "intended_obligation_ids": tuple(obligation_ids),
                "intended_occurrence_ids": tuple(occurrence_ids),
                "evidence_ids": evidence_ids,
                "channel": channel,
            }
        )
    return result


def _block_id_list(value: Any, field_name: str, block_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        raise SectionAuthoringError(f"{field_name} must be an array in block {block_id}")
    return _clean_strings(value)


def _compute_deterministic_maps(
    blocks: list[dict[str, Any]],
    *,
    body: str,
    visible_text: str,
    brief: SectionAuthoringBrief,
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
) -> tuple[dict[str, tuple[dict[str, Any], ...]], dict[str, tuple[dict[str, Any], ...]], list[dict[str, Any]]]:
    obligation_map: dict[str, list[dict[str, Any]]] = {}
    occurrence_map: dict[str, list[dict[str, Any]]] = {}
    evidence_usage: list[dict[str, Any]] = []
    channel_order = ("body", "case_activity", "exercise", "assessment", "summary")
    ordered_blocks = [block for channel in channel_order for block in blocks if block["channel"] == channel]
    materialized_visible = "\n\n".join(block["text"] for block in ordered_blocks).strip()
    if materialized_visible != visible_text:
        raise SectionAuthoringError("deterministic block materialization changed the visible section unexpectedly")
    cursor = 0
    for index, block in enumerate(ordered_blocks):
        if index:
            cursor += 2
        channel = block["channel"]
        start = cursor
        end = start + len(block["text"])
        cursor = end
        base = {
            "span_id": block["block_id"],
            "block_id": block["block_id"],
            "channel": channel,
            "start": start,
            "end": end,
            "text": block["text"],
            "evidence_ids": list(block["evidence_ids"]),
        }
        for obligation_id in block["intended_obligation_ids"]:
            status = binding_by_id[obligation_id].status
            entry = {**base, "coverage_status": "PARTIAL" if status == PARTIAL_OBLIGATION else "MATCH"}
            obligation_map.setdefault(obligation_id, []).append(entry)
            evidence_usage.append(
                {
                    "obligation_id": obligation_id,
                    "evidence_ids": list(block["evidence_ids"]),
                    "span_id": block["block_id"],
                    "support_status": status,
                    "channel": channel,
                    "start": start,
                    "end": end,
                    "text": block["text"],
                }
            )
        for occurrence_id in block["intended_occurrence_ids"]:
            occurrence_map.setdefault(occurrence_id, []).append(dict(base))
    # A body span is expected to be exact in the materialized body.  This
    # check catches accidental changes in the deterministic join operation.
    materialized_body = "\n\n".join(block["text"] for block in blocks if block["channel"] == "body").strip()
    if materialized_body != body:
        raise SectionAuthoringError("deterministic block materialization changed the body unexpectedly")
    return (
        {key: tuple(value) for key, value in obligation_map.items()},
        {key: tuple(value) for key, value in occurrence_map.items()},
        evidence_usage,
    )


def _check_obligation_coverage(
    brief: SectionAuthoringBrief,
    bindings: Mapping[str, ObligationEvidenceBinding],
    blocks: list[dict[str, Any]],
) -> dict[str, str]:
    coverage: dict[str, str] = {}
    by_id = {item.obligation_id: item for item in brief.all_obligations}
    for obligation_id, obligation in by_id.items():
        binding = bindings[obligation_id]
        matching = [item for item in blocks if obligation_id in item["intended_obligation_ids"]]
        if binding.status == SOURCE_GAP:
            coverage[obligation_id] = "NOT_APPLICABLE"
            continue
        if not matching:
            coverage[obligation_id] = "VIOLATION"
            continue
        text = " ".join(item["text"] for item in matching)
        if not _objective_signal(obligation.objective, text):
            coverage[obligation_id] = "VIOLATION"
        elif binding.status == PARTIAL_OBLIGATION:
            coverage[obligation_id] = "PARTIAL"
        else:
            coverage[obligation_id] = "MATCH"
    return coverage


def _objective_signal(objective: str, text: str) -> bool:
    objective_terms = _semantic_terms(objective)
    text_terms = _semantic_terms(text)
    if not objective_terms:
        return bool(text.strip())
    overlap = objective_terms & text_terms
    # A block must carry at least one objective signal; association alone is
    # never enough.  Requiring the full objective would reject valid prose
    # paraphrases and is not a semantic entailment check.
    return bool(overlap)


def _semantic_terms(text: str) -> set[str]:
    lowered = str(text or "").casefold()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]*", lowered))
    for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(segment) >= 2:
            terms.add(segment)
            terms.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return terms


def _run_local_claim_audit(
    *,
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    blocks: list[dict[str, Any]],
    judge: Any | None,
) -> list[dict[str, Any]]:
    """Reuse the Phase 4B claim auditor on block-local authorized evidence."""

    if not blocks:
        return []
    span_by_id: dict[str, dict[str, Any]] = {}
    for binding in packet.obligation_bindings:
        for span in binding.accepted_evidence_spans:
            evidence_id = str(span.get("evidence_id") or "").strip()
            if evidence_id and evidence_id not in span_by_id:
                span_by_id[evidence_id] = dict(span)
    evidence_by_id = {
        evidence_id: _evidence_chunk_from_span(evidence_id, span)
        for evidence_id, span in span_by_id.items()
    }
    markdown_parts: list[str] = []
    briefs: list[dict[str, Any]] = []
    for block in blocks:
        if not block["evidence_ids"]:
            continue
        occurrence_id = f"{brief.outline_node_id}:{block['block_id']}"
        markdown_parts.append(
            f'<!-- occurrence:start id="{occurrence_id}" chapter="{brief.chapter_id}" '
            f'section="{brief.outline_node_id}" task="section-block" -->\n'
            f"{block['text']}\n<!-- occurrence:end id=\"{occurrence_id}\" -->"
        )
        briefs.append(
            {
                "occurrence_id": occurrence_id,
                "section_id": brief.outline_node_id,
                "role": "SECTION",
                "source_chunk_ids": list(block["evidence_ids"]),
            }
        )
    if not markdown_parts:
        return []
    report = audit_rendered_claims(
        markdown="\n\n".join(markdown_parts),
        briefs=briefs,
        evidence_by_id=evidence_by_id,
        judge=judge,
        artifact_root=f"section:{brief.outline_node_id}",
        semantic_routing_categories=CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
    )
    return [item.to_dict() for item in report.records]


def _evidence_chunk_from_span(evidence_id: str, span: Mapping[str, Any]) -> EvidenceChunk:
    text = str(span.get("text") or "").strip()
    return EvidenceChunk(
        chunk_id=evidence_id,
        asset_id=f"section-packet:{evidence_id}",
        title="authorized section evidence",
        content=text,
        summary=text,
        keywords=[],
        subject="",
        material_block="",
        material_block_code="",
        recommended_chapter="",
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )


def _status_counts_from_claim_audit(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    result = {ClaimStatus.SUPPORTED: 0, ClaimStatus.PARTIALLY_SUPPORTED: 0, ClaimStatus.UNSUPPORTED: 0}
    for item in records:
        status = str(item.get("final_status") or "")
        if status in result:
            result[status] += 1
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
