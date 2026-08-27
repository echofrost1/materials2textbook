"""Section-level authoring contracts and deterministic draft materialization.

Phase 5D changes the authoring unit from an independently rendered
occurrence to one coherent section.  Occurrences remain semantic/audit
constraints.  This module deliberately stops at a fake-writer boundary: it
does not call a model, change BookPlan/Blueprint/Packet, or export a DigitalBook.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
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
    ContentType,
    audit_rendered_claims,
    classify_content_type,
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
INVALID_EVIDENCE_REFERENCE = "INVALID_EVIDENCE_REFERENCE"
EMPTY_REQUIRED_BLOCK = "EMPTY_REQUIRED_BLOCK"
BLOCK_EVIDENCE_UNAVAILABLE = "BLOCK_EVIDENCE_UNAVAILABLE"


class SectionAuthoringError(ValueError):
    """Raised when a draft violates the immutable section authoring contract."""


def build_section_authoring_messages(
    brief: "SectionAuthoringBrief",
    packet: SectionEvidencePacket,
    evidence_chunks: Iterable[EvidenceChunk],
    *,
    factual_claim_plan: "FactualClaimPlan | None" = None,
    claim_judge: Any | None = None,
) -> list[dict[str, str]]:
    """Build the production section-writer contract.

    The model receives only the already-bound packet spans.  It declares
    ordered semantic blocks; it never owns section fields, character offsets,
    occurrence anchors, or evidence outside the packet.
    """

    evidence_chunks = tuple(evidence_chunks)
    factual_claim_plan = factual_claim_plan or brief.factual_claim_plan
    if factual_claim_plan is None:
        factual_claim_plan = build_factual_claim_plan(
            brief,
            packet,
            evidence_chunks,
            claim_judge=claim_judge,
        )
    # Compile and validate the immutable slots before building a model prompt.
    # The optional plan argument is part of this call only; the frozen Brief is
    # never mutated.
    brief_with_plan = (
        brief
        if brief.factual_claim_plan is factual_claim_plan
        else replace(brief, factual_claim_plan=factual_claim_plan)
    )
    skeleton = build_section_skeleton(brief_with_plan, packet)
    bindings = {item.obligation_id: item for item in packet.obligation_bindings}
    approved_by_obligation: dict[str, list[FactualClaimPlanItem]] = {}
    approved_by_occurrence: dict[str, list[FactualClaimPlanItem]] = {}
    for claim in factual_claim_plan.approved_claims:
        for obligation_id in claim.obligation_ids:
            approved_by_obligation.setdefault(obligation_id, []).append(claim)
        for occurrence_id in claim.occurrence_ids:
            approved_by_occurrence.setdefault(occurrence_id, []).append(claim)

    def _claim_view(claim: FactualClaimPlanItem) -> dict[str, Any]:
        # Keep the writer view compact and free of evidence identifiers.  The
        # statement is already claim-audit approved; occurrence/knowledge
        # associations tell the model which contribution the statement serves.
        return {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "occurrence_ids": list(claim.occurrence_ids),
            "target_knowledge_ids": list(claim.target_knowledge_ids),
        }
    obligations: list[dict[str, Any]] = []
    for item in brief.all_obligations:
        binding = bindings[item.obligation_id]
        approved_claims = [
            claim
            for claim in factual_claim_plan.approved_claims
            if item.obligation_id in claim.obligation_ids
        ]
        obligations.append(
            {
                "obligation_id": item.obligation_id,
                "kind": item.kind,
                "required": item.required,
                "objective": item.objective,
                "evidence_status": binding.status,
                # The writer receives only already-approved factual claims,
                # never raw evidence text or evidence identifiers.
                "approved_factual_claim_ids": [claim.claim_id for claim in approved_claims],
                "approved_factual_claims": [claim.claim_id for claim in approved_claims],
                "forbidden_scope": (
                    "all professional claims for this obligation"
                    if binding.status == SOURCE_GAP
                    else "unsupported remainder beyond the accepted evidence"
                    if binding.status == PARTIAL_OBLIGATION
                    else "claims outside the accepted evidence"
                ),
            }
        )
    planned_contracts = _planned_block_contracts(brief_with_plan)
    slots_by_id = {str(item["block_id"]): item for item in skeleton["blocks"]}
    prompt_planned_contracts = []
    for item in planned_contracts:
        slot = slots_by_id[str(item["block_id"])]
        prompt_planned_contracts.append(
            {
                **item,
                "order": slot["order"],
                "required_for_authoring": slot["required_for_authoring"],
                "evidence_binding": slot["evidence_binding_status"],
                "evidence_attached_by_system": True,
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
        "section_new_contribution": list(brief.section_new_contribution),
        "required_current_contribution": list(brief.required_current_contribution),
        "activity_guidance": build_section_activity_guidance(brief),
        "occurrence_constraints": [deepcopy(dict(item)) for item in brief.occurrence_constraints],
        "occurrence_factual_claims": [
            {
                "occurrence_id": occurrence_id,
                "approved_factual_claim_ids": [claim.claim_id for claim in claims],
            }
            for occurrence_id, claims in approved_by_occurrence.items()
        ],
        "module_requirements": {
            "case_activity": brief.case_activity_requirement,
            "exercise": brief.exercise_requirement,
            "assessment": brief.assessment_requirement,
            "summary": brief.summary_requirement,
        },
        "obligations": obligations,
        # Structural block metadata is a deterministic system contract.  The
        # model is shown the slots so it can return the corresponding ``block_id``
        # but it is not asked to reproduce obligation, occurrence, channel, or
        # evidence associations in its response.
        "planned_block_contracts": prompt_planned_contracts,
        "section_skeleton": [
            {
                key: slot[key]
                for key in (
                    "block_id",
                    "channel",
                    "order",
                    "required",
                    "required_for_authoring",
                    "intended_obligation_ids",
                    "required_obligation_ids",
                    "intended_occurrence_ids",
                    "evidence_binding_status",
                    "evidence_attached_before_writer",
                )
                if key in slot
            }
            | {"evidence_attached_before_writer": True}
            for slot in skeleton["blocks"]
        ],
        "source_gaps": [deepcopy(dict(item)) for item in brief.source_gaps],
        "factual_claim_plan": factual_claim_plan.writer_view(),
    }
    system = (
        "You are the section-level author for a vocational digital textbook. "
        "Write one coherent student-visible section from this immutable contract. "
        "Use only the approved factual claim plan supplied in the contract for "
        "technical propositions; do not use outside knowledge or broaden evidence "
        "ownership. The factual claim plan is the sole domain-fact input. Do not "
        "treat the internal claim envelope or evidence packet as additional material. "
        "Do not replan roles, "
        "facets, prerequisites, obligations, or module requirements. Internal "
        "labels such as EXPLAIN, PERFORM, ANALYZE, TEACH, APPLY, RECALL, and "
        "EXTEND must never appear in student-visible text. Do not complete "
        "missing technical details from general knowledge. If a technically "
        "plausible detail is outside the supported claim envelope, omit it. "
        "Return only one JSON "
        "object with ordered semantic blocks and no Markdown fences or commentary."
    )
    user = (
        "Return exactly this shape:\n"
         '{"blocks":[{"block_id":"b01","text":"student-visible text"}],'
         '"generation_provenance":{"writer":"section-qwen"}}\n\n'
        "The blocks array fills the complete deterministic section skeleton. Return every "
         "required_for_authoring slot in the listed order with non-empty text; optional or "
         "source-gap-only slots may be omitted. Every returned block "
         "must contain only a legal block_id from planned_block_contracts and non-empty text. "
         "Do not return channel, intended_obligation_ids, required_obligation_ids, intended_occurrence_ids, "
         "approved_claim_ids, body/summary/offset/span, or "
         "evidence_ids fields. Evidence ownership is attached by deterministic "
        "code from the selected evidence bound to the block's intended obligations; "
        "the model must not choose aliases or source IDs. Only optional or "
        "source-gap-only slots may be omitted. For PARTIAL evidence, write only "
        "the supported portion; for SOURCE_GAP, do not write a professional fact. "
          "The planned_block_contracts list is the system-owned skeleton containing the "
         "approved factual claim plan for each planned responsibility. Use the "
         "slot's approved statements to write its text; do not return any of the "
         "slot's metadata or claim IDs. "
        "Make the teaching sequence natural and concrete when the evidence "
        "supports it: move from a fact to its explanation, relation, and a "
        "student takeaway. Teach the section_new_contribution first; recap "
        "prior support only within may_recap and never re-teach forbidden content. "
        "Choose exercise and assessment forms from activity_guidance, keep them "
        "different, and make both answerable from the preceding section. Do not "
        "write system language such as source, evidence, semantic, occurrence, "
        "prerequisite, or source gap for students. Include procedures, conditions, "
        "observable results, case/activity, exercise, assessment, and summary only "
        "when the immutable contract requires them. The approved factual claim "
        "plan controls WHAT technical facts, relations, conditions, modalities, "
        "and professional judgements may be asserted; you control HOW those facts "
        "are taught. Do not turn a claim into a richer scene, "
        "complete risk chain, or complete inspection sequence.\n\n"
        "IMMUTABLE AUTHORING CONTRACT:\n" + json.dumps(contract, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _planned_block_contracts(brief: "SectionAuthoringBrief") -> list[dict[str, Any]]:
    """Compile immutable writer block slots from the section brief.

    The section writer owns only the text for a slot.  Obligation, occurrence,
    channel, claim-plan, and evidence associations are derived here and reused
    by the materializer, so a model cannot alter the structural contract by
    emitting stale or contradictory metadata.
    """

    claims = tuple(brief.factual_claim_plan.approved_claims) if brief.factual_claim_plan else ()

    def claim_view(claim: "FactualClaimPlanItem") -> dict[str, Any]:
        return {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "occurrence_ids": list(claim.occurrence_ids),
            "target_knowledge_ids": list(claim.target_knowledge_ids),
        }

    # A writer block is a discourse unit, not an obligation row.  Grouping by
    # the deterministic output channel lets one body block teach several
    # complementary body obligations (for example concept + procedure) while
    # keeping the association code-owned.  Module channels remain separate so
    # an exercise can never silently become part of the body.
    channel_order = ("body", "case_activity", "exercise", "assessment", "summary")
    grouped: dict[str, list[tuple[int, SectionAuthoringObligation]]] = {
        channel: [] for channel in channel_order
    }
    for index, item in enumerate(brief.all_obligations, start=1):
        grouped[_channel_for_obligation(item)].append((index, item))

    contracts: list[dict[str, Any]] = []
    for slot_index, channel in enumerate(channel_order, start=1):
        grouped_items = grouped[channel]
        if not grouped_items:
            continue
        obligation_items = [item for _index, item in grouped_items]
        obligation_ids = [item.obligation_id for item in obligation_items]
        occurrence_ids = list(
            dict.fromkeys(
                occurrence_id
                for item in obligation_items
                for occurrence_id in item.linked_occurrence_ids
                if occurrence_id
            )
        )
        item_claims = [
            claim
            for claim in claims
            if set(claim.obligation_ids).intersection(obligation_ids)
        ]
        contracts.append(
            {
                # ``bNN`` is the public writer slot identifier.  The historical
                # ``obligation-NN`` identifier remains an accepted input alias
                # for archived fixtures, but the attached metadata is always
                # taken from this deterministic slot.
                "block_id": f"b{slot_index:02d}",
                "legacy_block_id": f"obligation-{grouped_items[0][0]:02d}",
                "legacy_block_ids": [
                    f"obligation-{index:02d}" for index, _item in grouped_items
                ],
                "channel": channel,
                "required": any(item.required == REQUIRED for item in obligation_items),
                "intended_obligation_ids": obligation_ids,
                "required_obligation_ids": (
                    [item.obligation_id for item in obligation_items if item.required == REQUIRED]
                ),
                "intended_occurrence_ids": occurrence_ids,
                "teaching_responsibility": " ".join(
                    item.objective for item in obligation_items if item.objective
                ),
                "approved_claim_ids": [claim.claim_id for claim in item_claims],
                "approved_factual_claim_details": [claim_view(claim) for claim in item_claims],
            }
        )
    return contracts


def build_section_skeleton(
    brief: "SectionAuthoringBrief",
    packet: SectionEvidencePacket,
) -> dict[str, Any]:
    """Create the immutable block slots before invoking a writer.

    The section writer is a slot filler: it may provide student-visible text
    for a slot, but it cannot create, delete, reorder, or re-associate slots.
    This function is intentionally deterministic and performs the evidence
    binding check before any model call.  Source-gap-only responsibilities are
    retained as audit slots but are not writer-required; a required factual
    responsibility with no bound evidence fails as ``BLOCK_EVIDENCE_UNAVAILABLE``.
    """

    if packet.packet_id != brief.packet_id or packet.blueprint_id != brief.blueprint_id:
        raise SectionAuthoringError("section skeleton inputs do not belong to the same authoring contract")

    bindings = {item.obligation_id: item for item in packet.obligation_bindings}
    expected_obligation_ids = {item.obligation_id for item in brief.all_obligations}
    # A packet may retain audit-only bindings for responsibilities that a
    # focused/legacy Brief has intentionally omitted.  Every Brief obligation
    # still needs exactly one binding; extra packet rows are not writer input.
    if not expected_obligation_ids.issubset(set(bindings)):
        raise SectionAuthoringError("section skeleton obligation bindings do not match the authoring brief")

    aliases = build_evidence_alias_map(packet)
    evidence_id_to_alias = build_evidence_id_to_alias_map(packet)
    authorized_ids = set(aliases.values())
    claims = tuple(brief.factual_claim_plan.approved_claims) if brief.factual_claim_plan else ()
    contracts = _planned_block_contracts(brief)
    slots: list[dict[str, Any]] = []
    for order, contract in enumerate(contracts):
        obligation_ids = tuple(str(item) for item in contract["intended_obligation_ids"])
        required_obligation_ids = tuple(str(item) for item in contract["required_obligation_ids"])
        # Evidence ownership is read from the packet binding that is about to
        # be materialized, rather than from a copied Brief map.  This keeps the
        # pre-writer gate correct even when a stale/partial overlay is supplied.
        allowed_id_set = {
            evidence_id
            for obligation_id in obligation_ids
            for evidence_id in bindings[obligation_id].accepted_evidence_ids
            if evidence_id in authorized_ids
        }
        allowed_ids = tuple(sorted(allowed_id_set))
        allowed_aliases = tuple(
            evidence_id_to_alias[item]
            for item in allowed_ids
            if item in evidence_id_to_alias
        )
        plan_evidence_ids = tuple(
            evidence_id
            for claim in claims
            if set(claim.obligation_ids).intersection(obligation_ids)
            for evidence_id in claim.supporting_evidence_ids
            if evidence_id in authorized_ids
        )
        evidence_ids = tuple(dict.fromkeys((*allowed_ids, *plan_evidence_ids)))
        if any(evidence_id not in authorized_ids for evidence_id in evidence_ids):
            raise SectionAuthoringError(
                f"{INVALID_EVIDENCE_REFERENCE}: skeleton evidence is outside the packet for {contract['block_id']}"
            )

        obligation_by_id = {item.obligation_id: item for item in brief.all_obligations}
        required_items = [obligation_by_id[item] for item in required_obligation_ids]
        deliverable_required = any(
            item.required == REQUIRED and bindings[item.obligation_id].status != SOURCE_GAP
            for item in required_items
        )
        factual_required_without_evidence = [
            item.obligation_id
            for item in required_items
            if bindings[item.obligation_id].status != SOURCE_GAP
            and not _is_derivable_obligation(item.obligation_id, brief, bindings)
        ]
        if deliverable_required and factual_required_without_evidence and not evidence_ids:
            raise SectionAuthoringError(
                f"{BLOCK_EVIDENCE_UNAVAILABLE}: {contract['block_id']} has no authorized evidence for "
                + ", ".join(factual_required_without_evidence)
            )

        slots.append(
            {
                "block_id": str(contract["block_id"]),
                "channel": str(contract["channel"]),
                "order": order,
                # ``required`` preserves the Blueprint responsibility.  The
                # narrower flag is what controls whether a non-empty writer
                # realization is required after source-gap filtering.
                "required": bool(contract.get("required")),
                "required_for_authoring": bool(deliverable_required),
                "intended_obligation_ids": list(obligation_ids),
                "required_obligation_ids": list(required_obligation_ids),
                "intended_occurrence_ids": list(contract.get("intended_occurrence_ids") or ()),
                "approved_claim_ids": list(contract.get("approved_claim_ids") or ()),
                "evidence_ids": list(evidence_ids),
                "allowed_evidence_aliases": list(
                    evidence_id_to_alias[item]
                    for item in evidence_ids
                    if item in evidence_id_to_alias
                ),
                "allowed_evidence_ids": list(evidence_ids),
                "evidence_attached_before_writer": True,
                "evidence_binding_status": (
                    "BOUND"
                    if evidence_ids
                    else "SOURCE_GAP_ONLY"
                    if required_obligation_ids
                    else "DERIVABLE"
                ),
                "text": "",
            }
        )

    skeleton = {
        "section_id": brief.outline_node_id,
        "outline_node_id": brief.outline_node_id,
        "blueprint_id": brief.blueprint_id,
        "packet_id": brief.packet_id,
        "blocks": slots,
        "required_block_ids": [
            item["block_id"] for item in slots if item["required_for_authoring"]
        ],
        "optional_block_ids": [
            item["block_id"] for item in slots if not item["required_for_authoring"]
        ],
        "system_owned_fields": [
            "block_id",
            "channel",
            "order",
            "required",
            "required_for_authoring",
            "intended_obligation_ids",
            "required_obligation_ids",
            "intended_occurrence_ids",
            "approved_claim_ids",
            "evidence_ids",
            "allowed_evidence_aliases",
        ],
        "writer_owned_fields": ["text"],
        "provenance": {
            "generator": "phase5d-deterministic-section-skeleton-v1",
            "evidence_attached_before_writer": True,
            "writer_slot_filling": True,
            "model_authored_metadata": False,
            "book_plan_mutated": False,
            "blueprint_mutated": False,
            "packet_mutated": False,
        },
    }
    validate_section_skeleton(skeleton, brief, packet)
    return skeleton


def validate_section_skeleton(
    skeleton: Mapping[str, Any],
    brief: "SectionAuthoringBrief",
    packet: SectionEvidencePacket,
) -> None:
    """Validate a pre-writer skeleton against the immutable brief and packet."""

    if not isinstance(skeleton, Mapping):
        raise SectionAuthoringError("section skeleton must be an object")
    if skeleton.get("outline_node_id") != brief.outline_node_id:
        raise SectionAuthoringError("section skeleton outline identity mismatch")
    if skeleton.get("blueprint_id") != brief.blueprint_id or skeleton.get("packet_id") != brief.packet_id:
        raise SectionAuthoringError("section skeleton overlay identity mismatch")
    slots = skeleton.get("blocks")
    if not isinstance(slots, (list, tuple)):
        raise SectionAuthoringError("section skeleton blocks must be an ordered list")

    contracts = _planned_block_contracts(brief)
    if len(slots) != len(contracts):
        raise SectionAuthoringError(
            f"section skeleton slot count mismatch: expected {len(contracts)}, got {len(slots)}"
        )
    packet_ids = set(build_evidence_alias_map(packet).values())
    seen: set[str] = set()
    for index, (slot, contract) in enumerate(zip(slots, contracts)):
        if not isinstance(slot, Mapping):
            raise SectionAuthoringError(f"section skeleton slot {index + 1} must be an object")
        block_id = str(slot.get("block_id") or "")
        if block_id in seen or block_id != str(contract["block_id"]):
            raise SectionAuthoringError("section skeleton block order or identity mismatch")
        seen.add(block_id)
        if str(slot.get("channel") or "") != str(contract["channel"]):
            raise SectionAuthoringError(f"section skeleton channel mismatch: {block_id}")
        if int(slot.get("order", -1)) != index:
            raise SectionAuthoringError(f"section skeleton order mismatch: {block_id}")
        evidence_ids = tuple(str(item) for item in slot.get("evidence_ids") or ())
        if not set(evidence_ids).issubset(packet_ids):
            raise SectionAuthoringError(f"{INVALID_EVIDENCE_REFERENCE}: skeleton {block_id}")
        if str(slot.get("text") or ""):
            raise SectionAuthoringError("section skeleton must be empty before writer slot filling")
    if set(skeleton.get("required_block_ids") or ()) != {
        str(item["block_id"])
        for item in slots
        if item.get("required_for_authoring")
    }:
        raise SectionAuthoringError("section skeleton required block set mismatch")


def _channel_for_obligation(item: "SectionAuthoringObligation") -> str:
    """Return the deterministic student-visible channel for an obligation."""

    if item.kind == CASE_ACTIVITY:
        return "case_activity"
    if item.kind == SUMMARY:
        return "summary"
    if item.kind == "assessment_exercise":
        source = str(item.source_field or item.objective or "").casefold()
        if "assessment_purpose" in source:
            return "assessment"
        return "exercise"
    return "body"


def build_section_activity_guidance(brief: "SectionAuthoringBrief") -> list[dict[str, str]]:
    """Compile responsibility-specific activity forms without adding facts.

    This is a writer constraint only.  It selects a pedagogical operation from
    the already-authorized obligation kind; it never decides whether an
    obligation is required and never supplies domain content.
    """

    guidance = {
        CONCEPT_PRINCIPLE: ("classification_or_comparison", "identify the relationship"),
        PROCEDURE_OPERATION: ("ordering_or_missing_step", "organize the documented flow"),
        PARAMETER_CONDITION: ("parameter_identification_or_comparison", "compare the supported role of each parameter"),
        OBSERVATION_QUALITY_JUDGEMENT: ("observation_matching_or_classification", "match an observed description to the taught criterion"),
        SAFETY_COMMON_ERROR: ("risk_to_measure_matching_or_checklist", "match a taught risk with its stated measure"),
        CASE_ACTIVITY: ("bounded_scenario_application", "apply only the section's supported facts to a bounded situation"),
        "assessment_exercise": ("responsibility_check", "check the section's core responsibility without copying the exercise"),
        SUMMARY: ("relationship_summary", "state the key relation and takeaway"),
    }
    result: list[dict[str, str]] = []
    for obligation in brief.all_obligations:
        if obligation.required == "NOT_APPLICABLE":
            continue
        exercise, purpose = guidance.get(
            obligation.kind,
            ("responsibility_check", "use only the supported section responsibility"),
        )
        result.append(
            {
                "obligation_id": obligation.obligation_id,
                "kind": obligation.kind,
                "exercise_type": exercise,
                "assessment_purpose": purpose,
            }
        )
    return result


def build_evidence_alias_map(packet: SectionEvidencePacket) -> dict[str, str]:
    """Return the stable writer-local alias map for one evidence packet.

    Aliases are intentionally derived only from the packet's authorized pool,
    in packet order.  The map is a presentation boundary: real chunk IDs stay
    in deterministic audit/materialization state and are never sent to Qwen.
    """

    ordered_ids: list[str] = []
    for evidence_id in (
        *packet.authorized_primary_evidence_ids,
        *packet.authorized_reference_evidence_ids,
    ):
        value = str(evidence_id or "").strip()
        if value and value not in ordered_ids:
            ordered_ids.append(value)
    return {f"E{index}": evidence_id for index, evidence_id in enumerate(ordered_ids, start=1)}


def build_evidence_id_to_alias_map(packet: SectionEvidencePacket) -> dict[str, str]:
    """Return the inverse of :func:`build_evidence_alias_map` explicitly.

    Retry/materialization code must never infer the direction from a generic
    ``alias_map`` variable: evidence IDs and writer aliases are different
    domains even when they are both strings.
    """

    return {evidence_id: alias for alias, evidence_id in build_evidence_alias_map(packet).items()}


def allowed_evidence_aliases_for_block(
    obligation_ids: Iterable[str],
    brief: SectionAuthoringBrief,
    evidence_id_to_alias: Mapping[str, str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Compute a block whitelist in the evidence-ID domain, then alias it."""

    ids = [str(item).strip() for item in obligation_ids if str(item).strip()]
    authorized_sets = [set(brief.authorized_evidence_per_obligation.get(item, ())) for item in ids]
    # A block may synthesize several obligations.  Its evidence whitelist is
    # the union of the selected evidence for those obligations (all are still
    # packet-authorized), not an intersection that becomes empty whenever two
    # obligations rely on complementary chunks.
    allowed_ids = set.union(*authorized_sets) if authorized_sets else set()
    ordered_ids = tuple(sorted(allowed_ids))
    ordered_aliases = tuple(
        evidence_id_to_alias[evidence_id]
        for evidence_id in ordered_ids
        if evidence_id in evidence_id_to_alias
    )
    return ordered_ids, ordered_aliases


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


WRITER_FAILURE_INVALID_JSON = "INVALID_JSON"
WRITER_FAILURE_SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
WRITER_FAILURE_TRUNCATED_OUTPUT = "TRUNCATED_OUTPUT"
WRITER_FAILURE_INVALID_ALIAS = "INVALID_ALIAS"
WRITER_FAILURE_MISSING_REQUIRED_BLOCK = "MISSING_REQUIRED_BLOCK"
WRITER_FAILURE_EMPTY_REQUIRED_BLOCK = EMPTY_REQUIRED_BLOCK
WRITER_FAILURE_BLOCK_EVIDENCE_UNAVAILABLE = BLOCK_EVIDENCE_UNAVAILABLE
WRITER_FAILURE_OTHER = "OTHER"


def classify_section_writer_failure(error: str, raw: str = "") -> str:
    """Classify a section-writer failure without changing its safety result."""

    message = str(error or "").casefold()
    text = str(raw or "").strip()
    if "block_evidence_unavailable" in message or "no authorized evidence for" in message:
        return WRITER_FAILURE_BLOCK_EVIDENCE_UNAVAILABLE
    if "empty_required_block" in message or "required skeleton slots have no text" in message:
        return WRITER_FAILURE_EMPTY_REQUIRED_BLOCK
    if "alias" in message or "evidence" in message or "invalid_evidence_reference" in message:
        return WRITER_FAILURE_INVALID_ALIAS
    if "missing" in message and ("required" in message or "module" in message or "block" in message):
        return WRITER_FAILURE_MISSING_REQUIRED_BLOCK
    if "invalid json" in message or "jsondecodeerror" in message:
        # A response that never closes its object/array is a distinct model
        # truncation signal; it is eligible for a syntax-only repair attempt.
        if text and (
            not text.rstrip().endswith(("}", "]"))
            or text.count("{") > text.count("}")
            or text.count("[") > text.count("]")
        ):
            return WRITER_FAILURE_TRUNCATED_OUTPUT
        return WRITER_FAILURE_INVALID_JSON
    if "must return" in message or "must be" in message or "schema" in message or "object" in message:
        return WRITER_FAILURE_SCHEMA_MISMATCH
    return WRITER_FAILURE_OTHER


def validate_structural_repair_preserves_content(original_raw: str, repaired: Mapping[str, Any]) -> bool:
    """Accept only a syntax/shape repair that preserves model-authored text.

    The original response may be truncated, so it cannot be parsed as JSON.
    We conservatively collect complete JSON string values that are present in
    the raw response and require every repaired block's text and identity to
    have already appeared there.  This prevents a "structural repair" call from
    quietly becoming a semantic rewrite.
    """

    blocks = repaired.get("blocks") if isinstance(repaired, Mapping) else None
    if not isinstance(blocks, (list, tuple)) or not blocks:
        return False
    raw_values: set[str] = set()
    for match in re.finditer(r'"(?:text|block_id)"\s*:\s*"((?:\\.|[^"\\])*)"', str(original_raw or "")):
        try:
            raw_values.add(str(json.loads('"' + match.group(1) + '"')))
        except json.JSONDecodeError:
            continue
    if not raw_values:
        return False
    for block in blocks:
        if not isinstance(block, Mapping):
            return False
        text = str(block.get("text") or "").strip()
        block_id = str(block.get("block_id") or "").strip()
        if not text or text not in raw_values or block_id not in raw_values:
            return False
    return True


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
    original_necessity: str = ""
    calibrated_necessity: str = ""
    source_boundary_status: str = ""
    calibration_provenance: Mapping[str, Any] = field(default_factory=dict)
    max_source_supported_facet: str = ""
    facet_calibration_provenance: Mapping[str, Any] = field(default_factory=dict)

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
            "original_necessity": self.original_necessity,
            "calibrated_necessity": self.calibrated_necessity,
            "source_boundary_status": self.source_boundary_status,
            "calibration_provenance": deepcopy(dict(self.calibration_provenance)),
            "max_source_supported_facet": self.max_source_supported_facet,
            "facet_calibration_provenance": deepcopy(dict(self.facet_calibration_provenance)),
        }


@dataclass(frozen=True)
class SupportedClaimEnvelope:
    """Deterministic factual boundary for one planned authoring block.

    The envelope is not student-facing prose and is not a replacement for
    the rendered-claim auditor.  It is the narrow contract between the
    obligation/evidence binding and the writer: the writer may choose how to
    teach the supported propositions, but may not add a new technical
    proposition, condition, modality, or professional judgement.
    ``supporting_evidence_ids`` is audit metadata only; it is deliberately not
    sent as a writer-selected alias/ID field.
    """

    block_id: str
    obligation_ids: tuple[str, ...]
    supported_propositions: tuple[str, ...] = ()
    supported_relations: tuple[str, ...] = ()
    permitted_scope: tuple[str, ...] = ()
    permitted_modality: tuple[str, ...] = ()
    unsupported_extensions: tuple[str, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_internal_ids: bool = True) -> dict[str, Any]:
        value = {
            "block_id": self.block_id,
            "obligation_ids": list(self.obligation_ids),
            "supported_propositions": list(self.supported_propositions),
            "supported_relations": list(self.supported_relations),
            "permitted_scope": list(self.permitted_scope),
            "permitted_modality": list(self.permitted_modality),
            "unsupported_extensions": list(self.unsupported_extensions),
            "provenance": deepcopy(dict(self.provenance)),
        }
        if include_internal_ids:
            value["supporting_evidence_ids"] = list(self.supporting_evidence_ids)
        return value


@dataclass(frozen=True)
class FactualClaimPlanItem:
    """One already-audited factual proposition available to the writer.

    The plan is deliberately narrower than an Evidence Packet.  Evidence
    ownership remains internal metadata, while the writer receives only the
    approved proposition text and its obligation association.  A plan item is
    eligible for prose only when ``status`` is ``SUPPORTED``.
    """

    claim_id: str
    block_id: str
    obligation_ids: tuple[str, ...]
    statement: str
    status: str
    occurrence_ids: tuple[str, ...] = ()
    target_knowledge_ids: tuple[str, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    content_type: str = ContentType.SOURCE_FACT
    rationale: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_internal_ids: bool = True) -> dict[str, Any]:
        value = {
            "claim_id": self.claim_id,
            "block_id": self.block_id,
            "obligation_ids": list(self.obligation_ids),
            "statement": self.statement,
            "status": self.status,
            "occurrence_ids": list(self.occurrence_ids),
            "target_knowledge_ids": list(self.target_knowledge_ids),
            "content_type": self.content_type,
            "rationale": self.rationale,
            "provenance": deepcopy(dict(self.provenance)),
        }
        if include_internal_ids:
            value["supporting_evidence_ids"] = list(self.supporting_evidence_ids)
        return value


@dataclass(frozen=True)
class FactualClaimPlan:
    """Deterministic, claim-level bridge between evidence and prose."""

    plan_id: str
    claims: tuple[FactualClaimPlanItem, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def approved_claims(self) -> tuple[FactualClaimPlanItem, ...]:
        return tuple(item for item in self.claims if item.status == ClaimStatus.SUPPORTED)

    def to_dict(self, *, include_internal_ids: bool = True) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "claims": [
                item.to_dict(include_internal_ids=include_internal_ids)
                for item in self.claims
            ],
            "provenance": deepcopy(dict(self.provenance)),
        }

    def writer_view(self) -> dict[str, Any]:
        """Return the plan view safe to place in a model prompt."""

        return {
            "plan_id": self.plan_id,
            "approved_claims": [
                {
                    "claim_id": item.claim_id,
                    "block_id": item.block_id,
                    "obligation_ids": list(item.obligation_ids),
                    "statement": item.statement,
                    "status": item.status,
                    "occurrence_ids": list(item.occurrence_ids),
                    "target_knowledge_ids": list(item.target_knowledge_ids),
                }
                for item in self.approved_claims
            ],
            "provenance": {
                "source_bounded": bool(self.provenance.get("source_bounded")),
                "approval_rule": self.provenance.get("approval_rule", ""),
            },
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
    section_new_contribution: tuple[str, ...] = ()
    required_current_contribution: tuple[str, ...] = ()
    case_activity_requirement: str = "NOT_APPLICABLE"
    exercise_requirement: str = "NOT_APPLICABLE"
    assessment_requirement: str = "NOT_APPLICABLE"
    summary_requirement: str = "NOT_APPLICABLE"
    supported_claim_envelopes: tuple[SupportedClaimEnvelope, ...] = ()
    factual_claim_plan: FactualClaimPlan | None = None
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
            "section_new_contribution": list(self.section_new_contribution),
            "required_current_contribution": list(self.required_current_contribution),
            "case_activity_requirement": self.case_activity_requirement,
            "exercise_requirement": self.exercise_requirement,
            "assessment_requirement": self.assessment_requirement,
            "summary_requirement": self.summary_requirement,
            "supported_claim_envelopes": [
                item.to_dict(include_internal_ids=True)
                for item in self.supported_claim_envelopes
            ],
            "factual_claim_plan": (
                self.factual_claim_plan.to_dict(include_internal_ids=True)
                if self.factual_claim_plan is not None
                else None
            ),
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
    # NOT_APPLICABLE source-bounded obligations remain in the immutable
    # Blueprint/Packet audit, but must not become writer responsibilities.
    optional = tuple(item for item in obligations if item.required == OPTIONAL)
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
    supported_claim_envelopes = _build_supported_claim_envelopes(
        obligations,
        binding_by_id,
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
        "source_bounded_obligation_audit": {
            item.obligation_id: {
                "original_necessity": item.original_necessity,
                "calibrated_necessity": item.calibrated_necessity,
                "source_boundary_status": item.source_boundary_status,
                "max_source_supported_facet": item.max_source_supported_facet,
                "facet_calibration_provenance": deepcopy(dict(item.facet_calibration_provenance)),
                "calibration_provenance": deepcopy(dict(item.calibration_provenance)),
            }
            for item in obligations
            if item.source_boundary_status
        },
        "supported_claim_envelopes": [
            item.to_dict(include_internal_ids=True)
            for item in supported_claim_envelopes
        ],
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
        section_new_contribution=contributions,
        required_current_contribution=contributions,
        case_activity_requirement=requirements["case_activity"],
        exercise_requirement=requirements["exercise"],
        assessment_requirement=requirements["assessment"],
        summary_requirement=requirements["summary"],
        supported_claim_envelopes=supported_claim_envelopes,
        provenance=provenance,
    )


def attach_factual_claim_plan(
    brief: SectionAuthoringBrief,
    factual_claim_plan: FactualClaimPlan,
) -> SectionAuthoringBrief:
    """Attach a pre-audited claim plan without mutating the immutable brief."""

    return replace(brief, factual_claim_plan=factual_claim_plan)


def build_factual_claim_plan(
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    evidence_chunks: Iterable[EvidenceChunk],
    *,
    claim_judge: Any | None = None,
    max_claims_per_obligation: int = 6,
    max_total_claims: int = 24,
) -> FactualClaimPlan:
    """Compile and audit the factual propositions available to a section writer.

    Candidate propositions come only from an obligation's already accepted
    packet spans (or explicit packet support metadata).  Exact source
    propositions are checked by the existing deterministic claim auditor;
    non-literal propositions may use the supplied semantic judge.  Only
    ``SUPPORTED`` items are exposed to prose generation.  This function never
    retrieves evidence, changes obligations, or broadens ownership.
    """

    chunk_by_id = {item.chunk_id: item for item in evidence_chunks}
    binding_by_id = {item.obligation_id: item for item in packet.obligation_bindings}
    envelope_by_obligation = {
        obligation_id: envelope
        for envelope in brief.supported_claim_envelopes
        for obligation_id in envelope.obligation_ids
    }
    occurrence_context = _occurrence_evidence_context(brief, packet, chunk_by_id)
    claims: list[FactualClaimPlanItem] = []
    counts: dict[str, int] = {}
    candidate_total = 0
    for obligation in brief.all_obligations:
        if len(claims) >= max_total_claims:
            break
        binding = binding_by_id.get(obligation.obligation_id)
        if binding is None or binding.status == SOURCE_GAP:
            continue
        candidates = _factual_claim_candidates(
            binding,
            obligation,
            chunk_by_id=chunk_by_id,
            occurrence_context=occurrence_context,
        )
        candidate_total += len(candidates)
        for index, (statement, evidence_ids, occurrence_ids) in enumerate(
            candidates[: min(max_claims_per_obligation, max_total_claims - len(claims))],
            start=1,
        ):
            claim_id = f"{brief.outline_node_id}:{obligation.obligation_id}:fc:{index}"
            envelope = envelope_by_obligation.get(obligation.obligation_id)
            audit = _audit_factual_plan_candidate(
                claim_id=claim_id,
                statement=statement,
                evidence_ids=evidence_ids,
                binding=binding,
                envelope=envelope,
                chunk_by_id=chunk_by_id,
                claim_judge=claim_judge,
            )
            status = _plan_status(audit)
            counts[status] = counts.get(status, 0) + 1
            claims.append(
                FactualClaimPlanItem(
                    claim_id=claim_id,
                    block_id=f"obligation-{brief.all_obligations.index(obligation) + 1:02d}",
                    obligation_ids=(obligation.obligation_id,),
                    statement=statement,
                    status=status,
                    occurrence_ids=tuple(occurrence_ids),
                    target_knowledge_ids=tuple(obligation.target_knowledge_ids),
                    supporting_evidence_ids=tuple(evidence_ids),
                    content_type=ContentType.SOURCE_FACT,
                    rationale=str(audit.get("rationale") or ""),
                    provenance={
                        "audit_resolution": audit.get("resolution", ""),
                        "deterministic_result": audit.get("deterministic_result", ""),
                        "semantic_result": audit.get("semantic_result", ""),
                        "source_bounded": True,
                        "occurrence_local": bool(occurrence_ids),
                        "occurrence_evidence_authorized": all(
                            evidence_id in occurrence_context["authorized_ids"]
                            for evidence_id in evidence_ids
                        ),
                    },
                )
            )
    approved = sum(1 for item in claims if item.status == ClaimStatus.SUPPORTED)
    return FactualClaimPlan(
        plan_id=f"factual-claim-plan:{brief.brief_id}",
        claims=tuple(claims),
        provenance={
            "generator": "phase5d-deterministic-factual-claim-plan-v2",
            "source_bounded": True,
            "approval_rule": "only existing claim-audit SUPPORTED propositions are exposed",
            "candidate_count": candidate_total,
            "approved_count": approved,
            "status_counts": counts,
            "evidence_scope_expanded": False,
            "occurrence_local": True,
        },
    )


def _factual_claim_candidates(
    binding: ObligationEvidenceBinding,
    obligation: SectionAuthoringObligation,
    *,
    chunk_by_id: Mapping[str, EvidenceChunk] | None = None,
    occurrence_context: Mapping[str, Any] | None = None,
) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Return source propositions interleaved by linked occurrence.

    A section-level obligation can be linked to several occurrences.  Using the
    first accepted spans for the obligation alone can starve one occurrence of
    its own teaching facts (for example, safety spans can crowd out equipment
    facts).  We therefore add only contribution chunks that are already in the
    packet whitelist, and interleave them by occurrence before the shared
    obligation spans.  No retrieval or ownership expansion happens here.
    """
    chunk_by_id = chunk_by_id or {}
    context = occurrence_context or {}
    occurrence_to_ids = context.get("occurrence_to_ids", {})
    occurrence_terms = context.get("occurrence_terms", {})
    evidence_to_occurrences = context.get("evidence_to_occurrences", {})
    linked_occurrences = tuple(
        occurrence_id
        for occurrence_id in obligation.linked_occurrence_ids
        if occurrence_id in occurrence_to_ids
    )

    rows: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    # Contribution evidence is source material already authorized for this
    # section.  Round-robin order makes the plan retain a small factual slice
    # for each linked occurrence instead of consuming the cap with one topic.
    per_occurrence: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]] = {}
    for occurrence_id in linked_occurrences:
        # Do not let the first packet chunk consume the entire per-obligation
        # claim budget.  A section packet commonly contains a broad safety
        # chunk followed by a more specific equipment/procedure chunk.  Rank
        # statements by their deterministic identity-term overlap, then
        # round-robin across evidence chunks so the plan can retain distinct,
        # source-backed propositions without expanding ownership.
        terms = tuple(occurrence_terms.get(occurrence_id, ()))
        by_evidence: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]] = {}
        for evidence_id in occurrence_to_ids.get(occurrence_id, ()):
            chunk = chunk_by_id.get(evidence_id)
            if chunk is None:
                continue
            statements = _split_source_claims(
                str(chunk.content or chunk.summary or chunk.title or "")
            )
            scored_statements = sorted(
                statements,
                key=lambda statement: (
                    -_statement_relevance_score(statement, terms),
                    -len(statement),
                ),
            )
            by_evidence[evidence_id] = [
                (statement, (evidence_id,), (occurrence_id,))
                for statement in scored_statements
            ]
        values: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        evidence_order = sorted(
            by_evidence,
            key=lambda evidence_id: (
                -max(
                    (_statement_relevance_score(item[0], terms) for item in by_evidence[evidence_id]),
                    default=0,
                ),
                evidence_id,
            ),
        )
        cursor = 0
        while evidence_order:
            progressed = False
            for evidence_id in evidence_order:
                rows_for_evidence = by_evidence[evidence_id]
                if cursor < len(rows_for_evidence):
                    values.append(rows_for_evidence[cursor])
                    progressed = True
            if not progressed:
                break
            cursor += 1
        per_occurrence[occurrence_id] = values
    cursor = 0
    while per_occurrence and any(per_occurrence.values()):
        progressed = False
        for occurrence_id in linked_occurrences:
            values = per_occurrence.get(occurrence_id, [])
            if cursor < len(values):
                rows.append(values[cursor])
                progressed = True
        if not progressed:
            break
        cursor += 1

    support = binding.provenance.get("evidence_set_support", {})
    support = support if isinstance(support, Mapping) else {}
    raw_values = support.get("supported_propositions") or support.get("supported_requirements") or ()
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    explicit = [
        str(value).strip()
        for value in raw_values
        if str(value).strip()
        and "authorized source proposition from the bound evidence" not in str(value)
    ]
    evidence_spans = [
        (str(span.get("evidence_id") or "").strip(), str(span.get("text") or "").strip())
        for span in binding.accepted_evidence_spans
        if str(span.get("evidence_id") or "").strip()
        and str(span.get("text") or "").strip()
    ]
    evidence_ids = tuple(dict.fromkeys(item[0] for item in evidence_spans))
    candidates: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = list(rows)
    for statement in explicit:
        matching = tuple(
            dict.fromkeys(
                evidence_id
                for evidence_id, span in evidence_spans
                if statement in span
            )
        ) or evidence_ids
        matching_occurrences = tuple(
            dict.fromkeys(
                occurrence_id
                for evidence_id in matching
                for occurrence_id in evidence_to_occurrences.get(evidence_id, ())
            )
        )
        candidates.append((statement[:360], matching, matching_occurrences))
    for evidence_id, span in evidence_spans:
        for statement in _split_source_claims(span):
            candidates.append(
                (
                    statement,
                    (evidence_id,),
                    tuple(evidence_to_occurrences.get(evidence_id, ())),
                )
            )
    deduped: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    index_by_key: dict[str, int] = {}
    for statement, ids, occurrence_ids in candidates:
        key = re.sub(r"\s+", "", statement)
        if not key:
            continue
        if key in index_by_key:
            index = index_by_key[key]
            old_statement, old_ids, old_occurrence_ids = deduped[index]
            deduped[index] = (
                old_statement,
                tuple(dict.fromkeys((*old_ids, *ids))),
                tuple(dict.fromkeys((*old_occurrence_ids, *occurrence_ids))),
            )
            continue
        index_by_key[key] = len(deduped)
        deduped.append((statement, tuple(dict.fromkeys(ids)), tuple(dict.fromkeys(occurrence_ids))))
    return deduped


def _occurrence_evidence_context(
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    chunk_by_id: Mapping[str, EvidenceChunk],
) -> dict[str, Any]:
    """Compile occurrence contribution evidence within the packet whitelist."""

    authorized_ids = {
        str(item).strip()
        for item in (
            *packet.authorized_primary_evidence_ids,
            *packet.authorized_reference_evidence_ids,
        )
        if str(item).strip()
    }
    occurrence_to_ids: dict[str, tuple[str, ...]] = {}
    occurrence_terms: dict[str, tuple[str, ...]] = {}
    evidence_to_occurrences: dict[str, list[str]] = {}
    # The packet is the stable section-level evidence contract.  SemanticDelta
    # evidence lists are model proposals and may legitimately differ between
    # a focused replay and the same occurrence in a full-book run.  Build a
    # deterministic occurrence-local pool from the packet bindings first so
    # those proposals cannot change factual-claim-plan membership or claim IDs.
    packet_ids_by_occurrence: dict[str, set[str]] = {}
    binding_by_id = {item.obligation_id: item for item in packet.obligation_bindings}
    for obligation in brief.all_obligations:
        bound_ids = {
            str(evidence_id).strip()
            for evidence_id in (
                binding_by_id.get(obligation.obligation_id).accepted_evidence_ids
                if binding_by_id.get(obligation.obligation_id) is not None
                else ()
            )
            if str(evidence_id).strip() in authorized_ids
            and str(evidence_id).strip() in chunk_by_id
        }
        for occurrence_id in obligation.linked_occurrence_ids:
            if occurrence_id:
                packet_ids_by_occurrence.setdefault(str(occurrence_id), set()).update(bound_ids)
    for raw in brief.occurrence_constraints:
        occurrence_id = str(raw.get("occurrence_id") or "").strip()
        if not occurrence_id:
            continue
        raw_ids = raw.get("contribution_evidence_chunk_ids") or raw.get("planning_evidence_chunk_ids") or ()
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        # Keep the planner's IDs for audit context only.  They must not alter
        # the candidate pool: the frozen packet binding is the authority for
        # factual realization, and using raw semantic IDs here reintroduces
        # focused/full production-path drift.
        explicit_ids = [
            str(item).strip()
            for item in raw_ids
            if str(item).strip() in authorized_ids and str(item).strip() in chunk_by_id
        ]
        # The planner's contribution list can be narrower than the already
        # authorized section packet.  Recover only packet-owned chunks whose
        # title/content matches this occurrence's knowledge context; this is
        # bounded candidate selection, not evidence-scope expansion.
        # Match the concrete canonical knowledge identity only.  Section-level
        # contribution prose is intentionally not used as a retrieval query:
        # it is broad enough to make generic safety slides outrank the actual
        # equipment proposition needed by this occurrence.
        context_text = " ".join(
            str(raw.get(name) or "")
            for name in ("knowledge_id", "canonical_knowledge_id")
        )
        context_terms = _knowledge_context_terms(context_text)
        occurrence_terms[occurrence_id] = tuple(
            term for term in context_terms if len(term) >= 2
        )
        ranked_matches: list[tuple[int, str]] = []
        specific_terms = tuple(term for term in context_terms if len(term) >= 3)
        deterministic_pool = set(packet_ids_by_occurrence.get(occurrence_id, ()))
        # Rank the complete authorized packet deterministically.  Accepted
        # binding IDs remain a mandatory seed, but a packet may contain a
        # stronger occurrence-specific proposition in its reference pool
        # (for example the equipment definition needed by this occurrence).
        # Scanning the packet whitelist—not the raw semantic proposal—recovers
        # that proposition without expanding ownership and keeps focused/full
        # runs identical.
        scan_ids = set(authorized_ids)
        for evidence_id in sorted(scan_ids):
            chunk = chunk_by_id.get(evidence_id)
            if chunk is None:
                continue
            # Do not let a repeated material title (for example, every slide
            # being titled ``焊接设备与安全``) outrank the actual proposition
            # text.  Content-level matches identify the contribution; metadata
            # remains only a fallback through the explicit planner list.
            chunk_text = " ".join(str(value or "") for value in (chunk.content, chunk.summary))
            # Longer identity matches carry more weight than generic words
            # such as ``焊接`` or ``设备``; this keeps an exact ``焊接设备``
            # proposition ahead of unrelated safety chunks with the same
            # material title.
            score = sum(len(term) for term in specific_terms if term and term in chunk_text)
            if score:
                ranked_matches.append((score, evidence_id))
        ranked_matches.sort(key=lambda item: (-item[0], item[1]))
        matched_ids = [evidence_id for _score, evidence_id in ranked_matches[:12]]
        # Planner evidence IDs are an unordered semantic proposal.  Never let
        # their model order reorder the deterministic content-ranked packet
        # candidates: doing so changes claim IDs between focused and full
        # production paths even when the authorized packet is identical.
        ids = tuple(dict.fromkeys((*matched_ids, *sorted(deterministic_pool))))
        occurrence_to_ids[occurrence_id] = ids
        for evidence_id in ids:
            evidence_to_occurrences.setdefault(evidence_id, []).append(occurrence_id)
    return {
        "authorized_ids": authorized_ids,
        "occurrence_to_ids": occurrence_to_ids,
        "evidence_to_occurrences": evidence_to_occurrences,
        "occurrence_terms": occurrence_terms,
    }


def _knowledge_context_terms(text: str) -> tuple[str, ...]:
    """Return bounded Chinese/Latin terms for packet-local occurrence matching."""

    value = re.sub(r"\s+", "", str(text or ""))
    terms: set[str] = set()
    for segment in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(segment) >= 2:
            terms.add(segment)
            # Include short n-grams so a multi-character knowledge title can
            # match an authorized source title without fuzzy retrieval.
            for width in (2, 3, 4):
                if len(segment) < width:
                    continue
                terms.update(segment[index : index + width] for index in range(len(segment) - width + 1))
    terms.update(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", value.casefold()))
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))


def _statement_relevance_score(statement: str, terms: Iterable[str]) -> int:
    """Score source wording only by deterministic identity-term overlap.

    This is a candidate-ordering signal, not a support decision.  It keeps a
    packet-local claim plan from spending its bounded slots on a generic chunk
    when an authorized chunk contains the concrete knowledge identity.  The
    statement itself remains verbatim source wording and is still audited
    before it can reach the writer.
    """

    value = re.sub(r"\s+", "", str(statement or ""))
    return sum(len(term) for term in terms if term and term in value)


def _split_source_claims(text: str) -> list[str]:
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        return []
    # Transcript chunks may have timestamp markers after whitespace
    # normalization.  Make each timestamped segment a separate proposition.
    value = re.sub(
        r"\[?\d{1,2}:\d{2}:\d{2}\s*[-–—]+>\s*\d{1,2}:\d{2}:\d{2}\]?",
        "\n",
        value,
    )
    # Keep source wording intact; only split at explicit sentence/clause
    # boundaries so the plan never invents a proposition by paraphrasing.
    parts = re.split(r"(?<=[。！？；.!?;])\s*|\n+", value)
    result: list[str] = []
    for part in parts:
        part = part.strip(" \t\r\n-•")
        if len(part) < 6:
            continue
        if len(part) > 240:
            part = part[:240].rsplit("，", 1)[0].strip() or part[:240]
        if part:
            result.append(part)
    return result


def _audit_factual_plan_candidate(
    *,
    claim_id: str,
    statement: str,
    evidence_ids: tuple[str, ...],
    binding: ObligationEvidenceBinding,
    envelope: SupportedClaimEnvelope | None,
    chunk_by_id: Mapping[str, EvidenceChunk],
    claim_judge: Any | None,
) -> dict[str, Any]:
    allowed_chunks = [chunk_by_id[item] for item in evidence_ids if item in chunk_by_id]
    if not allowed_chunks:
        return {"final_status": ClaimStatus.UNSUPPORTED, "rationale": "no authorized evidence"}
    rendered_id = f"claim-plan:{claim_id}"
    markdown = (
        f'<!-- occurrence:start id="{rendered_id}" chapter="factual-claim-plan" '
        f'section="factual-claim-plan" task="claim-plan" -->\n'
        f"{statement}\n"
        f'<!-- occurrence:end id="{rendered_id}" -->'
    )
    record = audit_rendered_claims(
        markdown=markdown,
        briefs=[
            {
                "occurrence_id": rendered_id,
                "section_id": "factual-claim-plan",
                "role": "PLAN",
                "source_chunk_ids": list(evidence_ids),
                "content_channel": "body",
                "content_type": ContentType.SOURCE_FACT,
                "supported_claim_envelope": (
                    envelope.to_dict(include_internal_ids=False) if envelope else {}
                ),
            }
        ],
        evidence_by_id={item.chunk_id: item for item in allowed_chunks},
        # Keep the same calibrated semantic routing used by the final claim
        # audit.  Exact source wording still passes the deterministic layer;
        # the injected judge is only consulted when that routing requires it.
        judge=claim_judge,
        artifact_root="factual-claim-plan",
        # A fixture without a semantic judge may still approve an exact
        # source proposition through the deterministic layer.  Production
        # passes the real judge and retains the calibrated routing categories.
        semantic_routing_categories=(
            CALIBRATED_SEMANTIC_ROUTING_CATEGORIES if claim_judge is not None else ()
        ),
    )
    if not record.records:
        return {"final_status": ClaimStatus.UNSUPPORTED, "rationale": "claim auditor returned no record"}
    statuses = [item.final_status for item in record.records]
    if ClaimStatus.UNSUPPORTED in statuses:
        selected = next(item for item in record.records if item.final_status == ClaimStatus.UNSUPPORTED)
    elif ClaimStatus.PARTIALLY_SUPPORTED in statuses:
        selected = next(item for item in record.records if item.final_status == ClaimStatus.PARTIALLY_SUPPORTED)
    else:
        selected = record.records[0]
    return selected.to_dict()


def _plan_status(audit: Mapping[str, Any]) -> str:
    status = str(audit.get("final_status") or ClaimStatus.UNSUPPORTED)
    if status in {ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED, ClaimStatus.UNSUPPORTED}:
        return status
    return ClaimStatus.UNSUPPORTED


def author_section(
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    writer: Callable[[SectionAuthoringBrief], Mapping[str, Any]],
    *,
    claim_judge: Any | None = None,
) -> RenderedSection:
    """Run a fake/wrapped writer and deterministically materialize its draft."""

    # Admission happens before the callback so a writer is never asked to
    # invent a missing structural slot or compensate for absent evidence.
    build_section_skeleton(brief, packet)
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
    skeleton = build_section_skeleton(brief, packet)
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
    evidence_aliases = build_evidence_alias_map(packet)
    blocks = _normalize_writer_blocks(
        raw_blocks,
        allowed_obligation_ids=allowed_obligation_ids,
        allowed_occurrence_ids=allowed_occurrence_ids,
        brief=brief,
        binding_by_id=binding_by_id,
        evidence_aliases=evidence_aliases,
        skeleton=skeleton,
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
    factual_plan_audit = _run_factual_plan_conformance(
        brief=brief,
        blocks=blocks,
        claim_audit=claim_audit,
        claim_judge=claim_judge,
    )
    plan_outside_claims = tuple(
        str(item.get("claim_id") or "")
        for item in factual_plan_audit
        if item.get("status") == "PLAN_OUTSIDE"
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
    ) + tuple(
        f"FACTUAL_PLAN_OUTSIDE:{item}" for item in plan_outside_claims if item
    )
    blocked = bool(core_source_gaps or coverage_violations or unsupported_claims or plan_outside_claims)
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
        "writer_evidence_aliases": dict(evidence_aliases),
        "internal_labels_rendered": False,
        "writer_replanned": False,
        "writer_contract": "ordered_blocks_only",
        "section_skeleton": deepcopy(skeleton),
        "model_authored_spans": False,
        "deterministic_association_fallbacks": sum(
            1 for item in blocks if item.get("_deterministic_association_fallback")
        ),
        "deterministic_contract_normalizations": [
            {
                "block_id": str(item.get("block_id") or ""),
                "normalizations": list(item.get("_contract_normalizations") or ()),
            }
            for item in blocks
            if item.get("_contract_normalizations")
        ],
        "span_coordinate_space": "materialized_student_visible_sequence",
        "obligation_coverage": coverage,
        "local_claim_evidence_audit": claim_audit,
        "local_claim_status_counts": _status_counts_from_claim_audit(claim_audit),
        "local_claim_content_type_counts": _content_type_counts_from_claim_audit(claim_audit),
        "factual_plan_conformance": factual_plan_audit,
        "factual_plan_outside_claims": list(plan_outside_claims),
        "supported_claim_envelopes": [
            item.to_dict(include_internal_ids=True)
            for item in brief.supported_claim_envelopes
        ],
        "factual_claim_plan": (
            brief.factual_claim_plan.to_dict(include_internal_ids=True)
            if brief.factual_claim_plan is not None
            else None
        ),
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


def _build_supported_claim_envelopes(
    obligations: Iterable[SectionAuthoringObligation],
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
) -> tuple[SupportedClaimEnvelope, ...]:
    """Compile block-local factual envelopes from existing packet judgements.

    This helper never calls a model and never searches evidence.  Semantic
    calibration metadata is preferred; when older packets do not carry
    proposition labels, the envelope records the exact authorized span as an
    internal proposition reference.  The writer still receives only the
    non-ID view, while deterministic audit state keeps the evidence IDs.
    """

    envelopes: list[SupportedClaimEnvelope] = []
    for index, obligation in enumerate(obligations, start=1):
        binding = binding_by_id[obligation.obligation_id]
        support = binding.provenance.get("evidence_set_support", {})
        if not isinstance(support, Mapping):
            support = {}

        def _values(*names: str) -> tuple[str, ...]:
            values: list[str] = []
            for name in names:
                raw = support.get(name) or binding.provenance.get(name) or ()
                if isinstance(raw, str):
                    raw = [raw]
                if isinstance(raw, Iterable):
                    values.extend(str(item).strip() for item in raw if str(item).strip())
            return tuple(dict.fromkeys(values))

        propositions = _values("supported_propositions", "supported_requirements")
        if not propositions:
            propositions = tuple(
                "authorized source proposition from the bound evidence"
                for span in binding.accepted_evidence_spans
                if str(span.get("evidence_id") or "").strip()
            )
        relations = _values("supported_relations")
        scope = _values("permitted_scope", "supported_scope") or (
            (obligation.objective.strip(),) if obligation.objective.strip() else ()
        )
        modality = _values("permitted_modality", "supported_modality") or (
            ("retain the modality and conditions stated by the authorized evidence",)
            if binding.accepted_evidence_ids
            else ()
        )
        unsupported = list(_values("unsupported_extensions", "unsupported_requirements"))
        if binding.status == SOURCE_GAP:
            unsupported.append("no professional fact may be asserted for this source-gap obligation")
        elif binding.status == PARTIAL_OBLIGATION:
            unsupported.append("do not strengthen or complete the unsupported remainder")
        else:
            unsupported.append("new technical facts, conditions, relations, or judgements outside this envelope")
        envelopes.append(
            SupportedClaimEnvelope(
                block_id=f"obligation-{index:02d}",
                obligation_ids=(obligation.obligation_id,),
                supported_propositions=propositions,
                supported_relations=relations,
                permitted_scope=scope,
                permitted_modality=modality,
                unsupported_extensions=tuple(dict.fromkeys(unsupported)),
                supporting_evidence_ids=tuple(binding.accepted_evidence_ids),
                provenance={
                    "status": binding.status,
                    "obligation_kind": obligation.kind,
                    "source_bounded": bool(obligation.source_boundary_status),
                    "semantic_evidence_judgement": bool(
                        binding.provenance.get("source_bounded_semantic_support")
                    ),
                },
            )
        )
    return tuple(envelopes)


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
        original_necessity=str(obligation.provenance.get("original_necessity") or obligation.required),
        calibrated_necessity=str(
            obligation.provenance.get("source_bounded_calibrated_necessity") or obligation.required
        ),
        source_boundary_status=str(obligation.provenance.get("source_boundary_status") or ""),
        calibration_provenance=deepcopy(
            dict(obligation.provenance.get("calibration_provenance") or {})
        ),
        max_source_supported_facet=str(
            obligation.max_source_supported_facet
            or obligation.provenance.get("max_source_supported_facet")
            or ""
        ),
        facet_calibration_provenance=deepcopy(
            dict(
                obligation.facet_calibration_provenance
                or obligation.provenance.get("facet_calibration_provenance")
                or {}
            )
        ),
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


def _normalize_deterministic_writer_blocks(
    raw_blocks: Iterable[Any],
    *,
    brief: SectionAuthoringBrief,
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
    evidence_aliases: Mapping[str, str],
    skeleton: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach all immutable block metadata from the deterministic slot plan.

    New section-writer responses contain only ``block_id`` and ``text``.  The
    writer cannot choose a channel, obligation/occurrence ownership, claim-plan
    IDs, or evidence IDs.  Those values are compiled from the same grouped slot
    plan shown in :func:`build_section_authoring_messages`.

    A small amount of legacy input tolerance is intentional: archived fixtures
    may use ``obligation-NN``/``block-NN`` aliases or include redundant metadata.
    Known redundant fields are ignored (and recorded), while unknown IDs and
    unauthorized evidence aliases still fail closed.
    """

    contracts = _planned_block_contracts(brief)
    skeleton_slots = {
        str(item.get("block_id") or ""): item
        for item in (skeleton or {}).get("blocks", ())
        if isinstance(item, Mapping)
    }
    if not contracts:
        return []
    raw_block_list = list(raw_blocks)
    channel_order = {"body": 0, "case_activity": 1, "exercise": 2, "assessment": 3, "summary": 4}
    slot_by_alias: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        aliases = {
            str(contract["block_id"]),
            str(contract.get("legacy_block_id") or ""),
        }
        aliases.update(str(item) for item in contract.get("legacy_block_ids") or ())
        # Human-readable aliases are retained only for old deterministic
        # fixtures.  They never change the attached metadata.
        slot_index = int(str(contract["block_id"])[1:])
        aliases.update({f"block-{slot_index:02d}", f"block-{slot_index}"})
        for alias in aliases:
            if alias:
                slot_by_alias.setdefault(alias, contract)
    channel_slots: dict[str, list[dict[str, Any]]] = {}
    for contract in contracts:
        channel_slots.setdefault(str(contract["channel"]), []).append(contract)
    for channel, values in channel_slots.items():
        if len(values) == 1:
            slot_by_alias.setdefault(f"{channel}-block", values[0])

    evidence_id_to_alias = {value: alias for alias, value in evidence_aliases.items()}
    normalized: list[dict[str, Any]] = []
    seen_slots: set[str] = set()
    for index, raw in enumerate(raw_block_list, start=1):
        if not isinstance(raw, Mapping):
            raise SectionAuthoringError(f"writer block {index} must be an object")
        raw_block_id = str(raw.get("block_id") or "").strip()
        text = str(raw.get("text") or "").strip()
        if not raw_block_id or not text:
            raise SectionAuthoringError(f"writer block {index} requires block_id and non-empty text")
        contract = slot_by_alias.get(raw_block_id)
        if contract is None:
            raise SectionAuthoringError(f"illegal writer block_id: {raw_block_id}")
        canonical_id = str(contract["block_id"])
        if canonical_id in seen_slots:
            raise SectionAuthoringError(f"duplicate writer block_id: {raw_block_id}")
        seen_slots.add(canonical_id)
        slot = skeleton_slots.get(canonical_id)
        obligation_ids = tuple(
            str(item)
            for item in (slot or {}).get("intended_obligation_ids", contract["intended_obligation_ids"])
        )
        required_obligation_ids = tuple(
            str(item)
            for item in (slot or {}).get("required_obligation_ids", contract["required_obligation_ids"])
        )
        occurrence_ids = tuple(
            str(item)
            for item in (slot or {}).get("intended_occurrence_ids", contract["intended_occurrence_ids"])
        )
        approved_claim_ids = tuple(
            str(item)
            for item in (slot or {}).get("approved_claim_ids", contract.get("approved_claim_ids") or ())
        )
        normalizations: list[str] = []

        # Redundant model fields are assertions at most; they can never replace
        # the deterministic slot metadata.  Validate unknown IDs so malformed
        # output cannot silently associate a block with an out-of-scope object.
        supplied_obligations = _block_id_list(raw.get("intended_obligation_ids"), "intended_obligation_ids", raw_block_id)
        if supplied_obligations:
            unknown = sorted(set(supplied_obligations) - set(binding_by_id))
            if unknown:
                raise SectionAuthoringError(f"block maps unknown obligations: {unknown}")
            if tuple(supplied_obligations) != obligation_ids:
                normalizations.append("model_intended_obligation_ids_ignored")
        supplied_required = _block_id_list(raw.get("required_obligation_ids"), "required_obligation_ids", raw_block_id)
        if supplied_required and tuple(supplied_required) != required_obligation_ids:
            normalizations.append("model_required_obligation_ids_ignored")
        elif raw.get("required_obligation_ids") is not None and tuple(supplied_required) != required_obligation_ids:
            normalizations.append("required_obligation_ids_from_intended")
            normalizations.append("required_obligation_ids_from_deterministic_plan")
        supplied_occurrences = _block_id_list(raw.get("intended_occurrence_ids"), "intended_occurrence_ids", raw_block_id)
        allowed_occurrences = {
            _constraint_value(item, "occurrence_id")
            for item in brief.occurrence_constraints
            if _constraint_value(item, "occurrence_id")
        }
        unknown_occurrences = sorted(set(supplied_occurrences) - allowed_occurrences)
        if unknown_occurrences:
            raise SectionAuthoringError(f"block maps unknown occurrences: {unknown_occurrences}")
        if supplied_occurrences and tuple(supplied_occurrences) != occurrence_ids:
            normalizations.append("model_intended_occurrence_ids_ignored")
        supplied_claims = tuple(_clean_strings(raw.get("approved_claim_ids") or ()))
        unknown_claims = sorted(set(supplied_claims) - set(approved_claim_ids))
        if unknown_claims:
            raise SectionAuthoringError(
                f"FACTUAL_PLAN_CLAIM_REFERENCE_INVALID: block {raw_block_id} contains {unknown_claims}"
            )
        if supplied_claims and supplied_claims != approved_claim_ids:
            normalizations.append("model_approved_claim_ids_ignored")

        supplied_channel = str(raw.get("channel") or "").strip().lower()
        if supplied_channel and supplied_channel not in channel_order:
            raise SectionAuthoringError(f"unknown writer block channel: {supplied_channel}")
        if supplied_channel and supplied_channel != str(contract["channel"]):
            normalizations.append("model_channel_ignored")

        legacy_aliases = tuple(_clean_strings(raw.get("evidence_ids") or ()))
        unknown_aliases = sorted(set(legacy_aliases) - set(evidence_aliases))
        if unknown_aliases:
            raise SectionAuthoringError(
                f"{INVALID_EVIDENCE_REFERENCE}: block {raw_block_id} contains unknown aliases {unknown_aliases}"
            )
        if slot is not None:
            allowed_ids = tuple(str(item) for item in slot.get("allowed_evidence_ids") or ())
            evidence_ids = tuple(str(item) for item in slot.get("evidence_ids") or ())
        else:
            allowed_ids, _allowed_aliases = allowed_evidence_aliases_for_block(
                obligation_ids,
                brief,
                evidence_id_to_alias,
            )
            plan_evidence_ids = tuple(
                evidence_id
                for claim in (brief.factual_claim_plan.approved_claims if brief.factual_claim_plan else ())
                if set(claim.obligation_ids).intersection(obligation_ids)
                for evidence_id in claim.supporting_evidence_ids
                if evidence_id in evidence_id_to_alias
            )
            evidence_ids = tuple(dict.fromkeys((*allowed_ids, *plan_evidence_ids)))
        if legacy_aliases:
            legacy_ids = tuple(evidence_aliases[alias] for alias in legacy_aliases)
            if not set(legacy_ids).issubset(set(evidence_ids)):
                raise SectionAuthoringError(f"block uses unauthorized evidence: {raw_block_id}")
            normalizations.append("model_evidence_ids_ignored")

        if not evidence_ids and not all(
            _is_derivable_obligation(item, brief, binding_by_id) for item in obligation_ids
        ):
            raise SectionAuthoringError(f"teaching block has no deterministically bound evidence: {raw_block_id}")
        normalized.append(
            {
                "block_id": canonical_id,
                "text": text,
                "intended_obligation_ids": obligation_ids,
                "required_obligation_ids": required_obligation_ids,
                "intended_occurrence_ids": occurrence_ids,
                "approved_claim_ids": approved_claim_ids,
                "_implicit_approved_claim_ids": True,
                "_contract_normalizations": tuple(normalizations),
                "evidence_ids": evidence_ids,
                "channel": str(contract["channel"]),
            }
        )

    # Required slots are deterministic, but source-gap-only slots are not
    # deliverable writer responsibilities.  Optional slots may be omitted.
    if skeleton_slots:
        missing_required = [
            block_id
            for block_id, slot in skeleton_slots.items()
            if slot.get("required_for_authoring") and block_id not in seen_slots
        ]
    else:
        missing_required = [
            str(contract["block_id"])
            for contract in contracts
            if contract.get("required")
            and any(
                binding_by_id[item].status != SOURCE_GAP
                for item in contract["required_obligation_ids"]
            )
            and str(contract["block_id"]) not in seen_slots
        ]
    if missing_required:
        raise SectionAuthoringError(
            f"{EMPTY_REQUIRED_BLOCK}: required skeleton slots have no text: "
            + ", ".join(missing_required)
        )
    return sorted(
        normalized,
        key=lambda item: (
            channel_order[item["channel"]],
            int(str(item["block_id"])[1:]),
        ),
    )


def _normalize_writer_blocks(
    raw_blocks: Iterable[Any],
    *,
    allowed_obligation_ids: set[str],
    allowed_occurrence_ids: set[str],
    brief: SectionAuthoringBrief,
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
    evidence_aliases: Mapping[str, str],
    skeleton: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate the model's semantic block declarations.

    This is deliberately the only model-facing structure.  Character
    positions, span IDs and evidence excerpts are all produced later by
    deterministic code.
    """

    # New production responses are intentionally minimal.  Keep the legacy
    # branch below for archived fixtures that use arbitrary block IDs and
    # model-declared associations, but route every legal slot (including a
    # response that redundantly includes old metadata) through the
    # deterministic compiler above.
    raw_block_list = list(raw_blocks)
    contracts = _planned_block_contracts(brief)
    legal_slot_ids: set[str] = set()
    for contract in contracts:
        legal_slot_ids.add(str(contract["block_id"]))
        legal_slot_ids.add(str(contract.get("legacy_block_id") or ""))
        legal_slot_ids.update(str(item) for item in contract.get("legacy_block_ids") or ())
        slot_index = int(str(contract["block_id"])[1:])
        legal_slot_ids.update({f"block-{slot_index:02d}", f"block-{slot_index}"})
    channel_counts: dict[str, int] = {}
    for contract in contracts:
        channel_counts[str(contract["channel"])] = channel_counts.get(str(contract["channel"]), 0) + 1
    for contract in contracts:
        if channel_counts.get(str(contract["channel"])) == 1:
            legal_slot_ids.add(f"{contract['channel']}-block")
    structural_fields = {
        "channel",
        "intended_obligation_ids",
        "required_obligation_ids",
        "intended_occurrence_ids",
        "approved_claim_ids",
        "evidence_ids",
    }
    all_ids_are_slots = all(
        isinstance(raw, Mapping) and str(raw.get("block_id") or "").strip() in legal_slot_ids
        for raw in raw_block_list
    )
    has_redundant_metadata = any(
        isinstance(raw, Mapping) and bool(structural_fields.intersection(raw.keys()))
        for raw in raw_block_list
    )
    if all_ids_are_slots or not has_redundant_metadata:
        return _normalize_deterministic_writer_blocks(
            raw_block_list,
            brief=brief,
            binding_by_id=binding_by_id,
            evidence_aliases=evidence_aliases,
            skeleton=skeleton,
        )

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    channel_order = {"body": 0, "case_activity": 1, "exercise": 2, "assessment": 3, "summary": 4}
    last_channel = -1
    for index, raw in enumerate(raw_block_list, start=1):
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
        required_obligation_ids = _block_id_list(
            raw.get("required_obligation_ids", obligation_ids),
            "required_obligation_ids",
            block_id,
        )
        contract_normalizations: list[str] = []
        if set(required_obligation_ids) != set(obligation_ids):
            # ``required_obligation_ids`` is a redundant declaration: the
            # immutable intended-obligation association is authoritative.  A
            # model may omit or stale-copy this mirror field; normalize it
            # deterministically instead of spending the only bounded writer
            # retry on a non-semantic formatting mismatch.
            required_obligation_ids = list(obligation_ids)
            contract_normalizations.append("required_obligation_ids_from_intended")
        occurrence_ids = _block_id_list(raw.get("intended_occurrence_ids"), "intended_occurrence_ids", block_id)
        unknown_obligations = sorted(set(obligation_ids) - allowed_obligation_ids)
        if unknown_obligations:
            raise SectionAuthoringError(f"block maps unknown obligations: {unknown_obligations}")
        unknown_occurrences = sorted(set(occurrence_ids) - allowed_occurrence_ids)
        if unknown_occurrences:
            raise SectionAuthoringError(f"block maps unknown occurrences: {unknown_occurrences}")
        plan_claims = (
            tuple(brief.factual_claim_plan.approved_claims)
            if brief.factual_claim_plan is not None
            else ()
        )
        allowed_claim_ids = {
            claim.claim_id
            for claim in plan_claims
            if set(claim.obligation_ids).intersection(obligation_ids)
            and (
                not occurrence_ids
                or not claim.occurrence_ids
                or set(claim.occurrence_ids).intersection(occurrence_ids)
            )
        }
        supplied_claim_ids = tuple(_clean_strings(raw.get("approved_claim_ids") or ()))
        # Existing deterministic fixtures predate the explicit block contract.
        # Derive their claim set from immutable obligation associations, but
        # mark the fallback so production telemetry can distinguish it from a
        # model-declared association.
        implicit_claim_ids = not bool(raw.get("approved_claim_ids"))
        approved_claim_ids = supplied_claim_ids or tuple(sorted(allowed_claim_ids))
        unknown_claims = sorted(set(approved_claim_ids) - allowed_claim_ids)
        if unknown_claims:
            raise SectionAuthoringError(
                f"FACTUAL_PLAN_CLAIM_REFERENCE_INVALID: block {block_id} contains {unknown_claims}"
            )
        if not obligation_ids and approved_claim_ids:
            raise SectionAuthoringError(f"discourse-only block cannot claim factual plan claims: {block_id}")
        legacy_aliases = tuple(_clean_strings(raw.get("evidence_ids") or ()))
        unknown_aliases = sorted(set(legacy_aliases) - set(evidence_aliases))
        if unknown_aliases:
            raise SectionAuthoringError(
                f"{INVALID_EVIDENCE_REFERENCE}: block {block_id} contains unknown aliases {unknown_aliases}"
            )
        if not obligation_ids and legacy_aliases:
            raise SectionAuthoringError(f"discourse-only block cannot claim evidence: {block_id}")
        if obligation_ids:
            non_derivable_source_gaps = [
                item
                for item in obligation_ids
                if binding_by_id[item].status == SOURCE_GAP
                and not _is_derivable_obligation(item, brief, binding_by_id)
            ]
            if non_derivable_source_gaps:
                raise SectionAuthoringError(
                    "source-gap obligation cannot receive generated content: "
                    + ", ".join(non_derivable_source_gaps)
                )
            evidence_id_to_alias = {value: alias for alias, value in evidence_aliases.items()}
            common_authorized, _common_aliases = allowed_evidence_aliases_for_block(
                obligation_ids, brief, evidence_id_to_alias
            )
            # A factual claim plan may have validated a proposition against an
            # authorized packet chunk that was not selected by the coarse
            # obligation binding.  Promote only those already-audited plan
            # evidence IDs for this block; never leave the packet whitelist.
            plan_evidence_ids = tuple(
                claim_evidence_id
                for claim in (
                    brief.factual_claim_plan.approved_claims
                    if brief.factual_claim_plan is not None
                    else ()
                )
                if set(claim.obligation_ids).intersection(obligation_ids)
                for claim_evidence_id in claim.supporting_evidence_ids
                if claim_evidence_id in evidence_id_to_alias
            )
            common_authorized = tuple(dict.fromkeys((*common_authorized, *plan_evidence_ids)))
            # Evidence ownership is code-owned.  New section-writer output
            # does not contain evidence_ids; attach the deterministic union
            # selected for the block's intended obligations.  A legacy fixture
            # may still provide aliases, but they are validated and never
            # allowed to expand or replace the deterministic ownership set.
            if legacy_aliases:
                legacy_ids = tuple(evidence_aliases[alias] for alias in legacy_aliases)
                if not set(legacy_ids).issubset(set(common_authorized)):
                    raise SectionAuthoringError(f"block uses unauthorized evidence: {block_id}")
            evidence_ids = tuple(common_authorized)
            if not evidence_ids and not all(_is_derivable_obligation(item, brief, binding_by_id) for item in obligation_ids):
                raise SectionAuthoringError(f"teaching block has no deterministically bound evidence: {block_id}")
        else:
            evidence_ids = ()
        channel = str(raw.get("channel") or "body").strip().lower()
        if channel not in {"body", "case_activity", "exercise", "assessment", "summary"}:
            raise SectionAuthoringError(f"unknown writer block channel: {channel}")
        if channel_order[channel] < last_channel:
            contract_normalizations.append("channel_order_deferred_to_materializer")
        last_channel = channel_order[channel]
        result.append(
            {
                "block_id": block_id,
                "text": text,
                "intended_obligation_ids": tuple(obligation_ids),
                "required_obligation_ids": tuple(required_obligation_ids),
                "intended_occurrence_ids": tuple(occurrence_ids),
                "approved_claim_ids": tuple(approved_claim_ids),
                "_implicit_approved_claim_ids": implicit_claim_ids,
                "_contract_normalizations": tuple(contract_normalizations),
                "evidence_ids": evidence_ids,
                "channel": channel,
            }
        )
    # Channels define the student-visible projection order.  Sorting here is
    # deterministic and preserves each block's text/semantic associations;
    # it does not invent, delete, or reassign teaching content.
    return sorted(result, key=lambda item: (channel_order[item["channel"]], result.index(item)))


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
    allowed_occurrence_ids = {
        _constraint_value(item, "occurrence_id")
        for item in brief.occurrence_constraints
        if _constraint_value(item, "occurrence_id")
    }
    # A section with exactly one executable occurrence has an unambiguous
    # ownership fallback: ordinary body blocks that carry no explicit
    # occurrence association belong to that occurrence.  This repairs the
    # production case where the section writer correctly produced a body but
    # omitted a redundant one-item association.  It is deliberately not
    # applied to multi-occurrence sections, where guessing would hide a
    # materialization error.
    sole_occurrence_id = next(iter(allowed_occurrence_ids)) if len(allowed_occurrence_ids) == 1 else None
    constraint_by_occurrence = {
        _constraint_value(item, "occurrence_id"): item
        for item in brief.occurrence_constraints
        if _constraint_value(item, "occurrence_id")
    }
    obligation_by_id = {item.obligation_id: item for item in brief.all_obligations}
    body_has_explicit_obligations = any(
        block["channel"] == "body" and block.get("intended_obligation_ids")
        for block in ordered_blocks
    )
    fallback_body_assigned = False
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
        occurrence_ids = block["intended_occurrence_ids"]
        if not occurrence_ids:
            occurrence_ids = _infer_block_occurrence_ids(
                block,
                allowed_occurrence_ids=allowed_occurrence_ids,
                constraint_by_occurrence=constraint_by_occurrence,
                obligation_by_id=obligation_by_id,
            )
        if not occurrence_ids and sole_occurrence_id and channel == "body":
            occurrence_ids = (sole_occurrence_id,)

        # A real single-occurrence section may contain a body block whose
        # model association is omitted while the section still has explicit
        # case/exercise blocks.  Preserve the existing deterministic
        # sole-occurrence rule, but also bind the first such body block to the
        # required obligations linked to that occurrence.  The evidence IDs
        # come only from those accepted packet bindings; no new evidence is
        # retrieved and multi-occurrence sections never use this fallback.
        if (
            sole_occurrence_id
            and channel == "body"
            and len(occurrence_ids) == 1
            and occurrence_ids[0] == sole_occurrence_id
            and not block.get("intended_obligation_ids")
            and not body_has_explicit_obligations
            and not fallback_body_assigned
        ):
            linked_obligations = tuple(
                item.obligation_id
                for item in obligation_by_id.values()
                if sole_occurrence_id in tuple(item.linked_occurrence_ids)
                and not item.source_gap
            )
            if linked_obligations:
                block["intended_obligation_ids"] = linked_obligations
                fallback_ids = tuple(
                    evidence_id
                    for obligation_id in linked_obligations
                    for evidence_id in obligation_by_id[obligation_id].authorized_evidence_ids
                    if evidence_id not in block["evidence_ids"]
                )
                block["evidence_ids"] = tuple(dict.fromkeys((*block["evidence_ids"], *fallback_ids)))
                block["_deterministic_association_fallback"] = True
                fallback_body_assigned = True

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
        for occurrence_id in occurrence_ids:
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


def _infer_block_occurrence_ids(
    block: Mapping[str, Any],
    *,
    allowed_occurrence_ids: set[str],
    constraint_by_occurrence: Mapping[str, Mapping[str, Any]],
    obligation_by_id: Mapping[str, SectionAuthoringObligation],
) -> tuple[str, ...]:
    """Resolve block ownership from immutable semantic metadata.

    This deliberately has no ordinal or block-position fallback.  A block is
    owned by an occurrence only when its intended obligations or target
    knowledge identify exactly one occurrence.  Multiple valid owners are an
    auditable ambiguity and fail closed in the caller.
    """

    linked: set[str] = set()
    target_knowledge: set[str] = set()
    for obligation_id in block.get("intended_obligation_ids", ()):
        obligation = obligation_by_id.get(str(obligation_id))
        if obligation is None:
            continue
        linked.update(str(item) for item in obligation.linked_occurrence_ids if str(item))
        target_knowledge.update(str(item) for item in obligation.target_knowledge_ids if str(item))
    linked &= set(allowed_occurrence_ids)
    if len(linked) == 1:
        return (next(iter(linked)),)

    # Contribution/planning evidence is execution metadata, not a fuzzy text
    # guess.  If the block's authorized evidence belongs to exactly one
    # occurrence, that owner is deterministic even when the section contains
    # several occurrences with the same obligation kind.
    block_evidence = {str(item) for item in block.get("evidence_ids", ()) if str(item)}
    if block_evidence:
        evidence_matches = tuple(
            occurrence_id
            for occurrence_id in (linked or allowed_occurrence_ids)
            if block_evidence.intersection(
                {
                    str(item)
                    for item in (
                        constraint_by_occurrence.get(occurrence_id, {}).get("contribution_evidence_chunk_ids", ())
                        or constraint_by_occurrence.get(occurrence_id, {}).get("planning_evidence_chunk_ids", ())
                        or ()
                    )
                    if str(item)
                }
            )
        )
        if len(evidence_matches) == 1:
            return evidence_matches
        if evidence_matches:
            linked = set(evidence_matches)

    # A shared obligation may link several occurrences.  An exact canonical
    # knowledge owner resolves that case without relying on order or text
    # similarity.
    if target_knowledge:
        exact = tuple(
            occurrence_id
            for occurrence_id in (linked or allowed_occurrence_ids)
            if str(
                constraint_by_occurrence.get(occurrence_id, {}).get("knowledge_id")
                or constraint_by_occurrence.get(occurrence_id, {}).get("canonical_knowledge_id")
                or ""
            ) in target_knowledge
        )
        if len(exact) == 1:
            return exact
        if exact:
            linked = set(exact)

    # Finally use the explicit contribution target supplied by the semantic
    # plan.  This is a deterministic metadata association, not an ordinal
    # fallback.  A unique positive overlap is accepted; ties remain ambiguous
    # and fail closed.
    if len(linked) != 1:
        block_terms = _metadata_bigrams(str(block.get("text") or ""))
        scored = []
        for occurrence_id in (linked or allowed_occurrence_ids):
            constraint = constraint_by_occurrence.get(occurrence_id, {})
            target = " ".join(
                str(constraint.get(name) or "")
                for name in ("intended_contribution", "contribution_goal", "new_context")
            )
            score = len(block_terms & _metadata_bigrams(target))
            scored.append((score, occurrence_id))
        scored.sort(reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return (scored[0][1],)

    if len(linked) > 1:
        raise SectionAuthoringError(
            "AMBIGUOUS_OCCURRENCE_MAPPING: block has multiple immutable owners "
            + ", ".join(sorted(linked))
        )
    return ()


def _metadata_bigrams(value: str) -> set[str]:
    """Return conservative word/character features for metadata ownership."""

    lowered = str(value or "").casefold()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]*", lowered))
    for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
        terms.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return {item for item in terms if len(item) >= 2}


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
    # Claim-plan propositions can be bound to an authorized packet chunk that
    # was not present in that obligation's coarse accepted-span list.  Expose
    # the already-audited proposition as the local span for that same evidence
    # ID; this is not new evidence and cannot expand the packet whitelist.
    authorized_packet_ids = {
        str(item).strip()
        for item in (
            *packet.authorized_primary_evidence_ids,
            *packet.authorized_reference_evidence_ids,
        )
        if str(item).strip()
    }
    if brief.factual_claim_plan is not None:
        plan_spans: dict[str, list[str]] = {}
        for claim in brief.factual_claim_plan.approved_claims:
            for evidence_id in claim.supporting_evidence_ids:
                if evidence_id in authorized_packet_ids and claim.statement:
                    plan_spans.setdefault(evidence_id, []).append(claim.statement)
        for evidence_id, statements in plan_spans.items():
            if evidence_id not in span_by_id:
                span_by_id[evidence_id] = {
                    "evidence_id": evidence_id,
                    "text": "\n".join(dict.fromkeys(statements)),
                }
    evidence_by_id = {
        evidence_id: _evidence_chunk_from_span(evidence_id, span)
        for evidence_id, span in span_by_id.items()
    }
    markdown_parts: list[str] = []
    briefs: list[dict[str, Any]] = []
    envelope_by_obligation = {
        obligation_id: envelope.to_dict(include_internal_ids=False)
        for envelope in brief.supported_claim_envelopes
        for obligation_id in envelope.obligation_ids
    }
    for block in blocks:
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
                # The section contract already knows whether a block is a
                # learner task or a student-facing body.  Pass that
                # deterministic classification into the shared claim audit;
                # the audit still re-checks the statement itself and keeps
                # source-fact claims on the evidence gate.
                "content_channel": block["channel"],
                # Do not classify an entire mixed body block once and reuse
                # that answer for every sentence.  Only task channels receive
                # the deterministic derived-instruction hint; body/summary
                # claims are classified sentence-by-sentence by the shared
                # audit so a factual sentence cannot be hidden by connective
                # prose in the same block.
                "content_type": (
                    ContentType.DERIVED_INSTRUCTION
                    if block["channel"] in {"case_activity", "exercise", "assessment"}
                    else ContentType.PEDAGOGICAL_SYNTHESIS
                    if block["channel"] == "summary"
                    else ""
                ),
                "supported_claim_envelope": next(
                    (
                        envelope_by_obligation[obligation_id]
                        for obligation_id in block.get("intended_obligation_ids", ())
                        if obligation_id in envelope_by_obligation
                    ),
                    {},
                ),
                "factual_claim_plan": (
                    brief.factual_claim_plan.writer_view()
                    if brief.factual_claim_plan is not None
                    else {}
                ),
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
    result: list[dict[str, Any]] = []
    for item in report.records:
        record = item.to_dict()
        occurrence_id = str(record.get("occurrence_id") or "")
        if ":" in occurrence_id:
            record["block_id"] = occurrence_id.rsplit(":", 1)[-1]
        result.append(record)
    return result


def _run_factual_plan_conformance(
    *,
    brief: SectionAuthoringBrief,
    blocks: list[dict[str, Any]],
    claim_audit: Iterable[Mapping[str, Any]],
    claim_judge: Any | None = None,
) -> list[dict[str, Any]]:
    """Check generated source facts against the approved block-local plan.

    This is intentionally separate from the evidence auditor: the latter asks
    whether an approved proposition is supported by authorized source, while
    this layer asks whether the writer introduced a proposition that was never
    approved for this block.  Pedagogical synthesis and derived instruction
    remain free to organize the approved facts without entering this gate.
    """
    plan = brief.factual_claim_plan
    if plan is None:
        return []
    claims_by_id = {item.claim_id: item for item in plan.approved_claims}
    block_claims = {
        str(block.get("block_id") or ""): tuple(str(item) for item in block.get("approved_claim_ids") or ())
        for block in blocks
    }
    results: list[dict[str, Any]] = []
    for record in claim_audit:
        content_type = str(record.get("content_type") or "")
        if content_type not in {ContentType.SOURCE_FACT, ContentType.UNSUPPORTED_DOMAIN_CLAIM}:
            continue
        block_id = str(record.get("block_id") or "")
        claim_text = str(record.get("claim_text") or record.get("source_span") or "").strip()
        allowed_ids = tuple(item for item in block_claims.get(block_id, ()) if item in claims_by_id)
        matched: list[str] = []
        for claim_id in allowed_ids:
            if _factual_claim_is_within_plan(claim_text, claims_by_id[claim_id].statement):
                matched.append(claim_id)
        semantic_rationale = ""
        if not matched and claim_judge is not None and allowed_ids:
            # A student-facing paraphrase may be semantically entailed by an
            # approved proposition without sharing its literal wording.  The
            # second layer is still plan-only: the judge sees the generated
            # claim and the approved statements, never raw evidence.  Any
            # returned support ID must be one of the block's approved claim
            # IDs, otherwise the mapping remains fail-closed.
            try:
                proposal = claim_judge.judge(
                    claim=claim_text,
                    evidence=[
                        {
                            "evidence_id": item,
                            "span": claims_by_id[item].statement,
                        }
                        for item in allowed_ids
                    ],
                    context={
                        "role": "FACTUAL_CLAIM_PLAN",
                        "section_id": brief.outline_node_id,
                        "current_occurrence": ",".join(
                            str(value)
                            for block in blocks
                            if str(block.get("block_id") or "") == block_id
                            for value in (block.get("intended_occurrence_ids") or ())
                        ),
                        "supported_claim_envelope": {},
                    },
                )
                if (
                    str(proposal.get("status") or "") == ClaimStatus.SUPPORTED
                    and set(str(item) for item in (proposal.get("supporting_evidence_ids") or ())).issubset(set(allowed_ids))
                    and bool(proposal.get("supporting_evidence_ids"))
                ):
                    matched = [
                        str(item)
                        for item in proposal.get("supporting_evidence_ids") or ()
                        if str(item) in set(allowed_ids)
                    ]
                    semantic_rationale = str(proposal.get("rationale") or "")
            except Exception as exc:
                semantic_rationale = f"plan-only semantic mapping failed closed: {type(exc).__name__}"
        status = "SUPPORTED" if matched else "PLAN_OUTSIDE"
        results.append({
            "claim_id": str(record.get("claim_id") or ""),
            "block_id": block_id,
            "claim_text": claim_text,
            "approved_claim_ids": list(allowed_ids),
            "matched_claim_ids": matched,
            "status": status,
            "reason": "claim is covered by the block-local approved factual plan" if matched else (
                "SOURCE_FACT proposition is not covered by any approved claim for this block"
            ),
            "semantic_rationale": semantic_rationale,
        })
    return results


def _factual_claim_is_within_plan(claim_text: str, approved_statement: str) -> bool:
    left = " ".join(str(claim_text or "").casefold().split())
    right = " ".join(str(approved_statement or "").casefold().split())
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    claim_terms = _semantic_terms(left)
    statement_terms = _semantic_terms(right)
    if not claim_terms or not statement_terms:
        return False
    # Require nearly every claim term to be represented in the approved
    # proposition.  This deliberately rejects causal/modality expansions that
    # happen to share a topic word with an approved fact.
    coverage = len(claim_terms & statement_terms) / max(len(claim_terms), 1)
    return coverage >= 0.78


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


def _is_derivable_obligation(
    obligation_id: str,
    brief: SectionAuthoringBrief,
    binding_by_id: Mapping[str, ObligationEvidenceBinding],
) -> bool:
    """Whether an obligation may be synthesized from accepted section text.

    This is intentionally narrow.  The first allowed case is the summary
    obligation explicitly marked by the Section Evidence Packet.  It does not
    make any factual teaching, procedure, parameter, or safety obligation
    derivable by implication.
    """

    obligation = next(
        (item for item in brief.all_obligations if item.obligation_id == obligation_id),
        None,
    )
    binding = binding_by_id.get(obligation_id)
    return bool(
        obligation
        and binding
        and obligation.kind == SUMMARY
        and binding.provenance.get("derivable_from_verified_content") is True
    )


def _content_type_counts_from_claim_audit(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in records:
        content_type = str(item.get("content_type") or "SOURCE_FACT")
        result[content_type] = result.get(content_type, 0) + 1
    return result


def _constraint_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return deepcopy(dict(item))
    names = (
        "occurrence_id", "section_id", "knowledge_id", "canonical_knowledge_id", "role", "already_available_facets", "available_facets", "required_facets",
        "must_teach_facets", "must_not_reteach_facets", "extension_keys", "repeated_aspects_to_avoid",
        "prerequisite_context", "contribution_goal", "intended_contribution", "new_facets",
        "new_context", "contribution_evidence_chunk_ids", "planning_evidence_chunk_ids",
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
