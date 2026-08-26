from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from materials2textbook.agents.book_plan_completeness import diagnose_book_plan_completeness
from materials2textbook.domain_config import DomainConfig
from materials2textbook.knowledge_map.evidence_packet import (
    PARTIAL_OBLIGATION,
    SOURCE_GAP,
    SUPPORTED_OBLIGATION,
    build_section_evidence_packet,
)
from materials2textbook.knowledge_map.outline import book_plan_deep_equal, book_plan_fingerprint
from materials2textbook.knowledge_map.section_authoring import (
    BLOCKED_CORE_SOURCE_GAP,
    INVALID_EVIDENCE_REFERENCE,
    RENDERED,
    RENDERED_PARTIAL,
    SectionAuthoringError,
    author_section,
    build_evidence_alias_map,
    build_evidence_id_to_alias_map,
    allowed_evidence_aliases_for_block,
    build_section_authoring_messages,
    build_section_activity_guidance,
    build_section_authoring_brief,
    classify_section_writer_failure,
    materialize_section_draft,
    validate_structural_repair_preserves_content,
)
from materials2textbook.knowledge_map.teaching_blueprint import build_section_teaching_blueprint
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _chunk(chunk_id: str = "chunk-1", content: str | None = None) -> EvidenceChunk:
    content = content or (
        "Explain the basic operation and quality conditions. Complete the basic operation and verify the result. "
        "Assess the observable operation and quality judgment. Observe the operation and its result. "
        "Apply the operation to a bounded situation and practice the task."
    )
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=f"asset-{chunk_id}",
        title="Basic operation",
        content=content,
        summary=content,
        keywords=["basic", "operation", "procedure", "quality", "result", "task"],
        subject="Basic operation",
        material_block="block-1",
        material_block_code="B1",
        recommended_chapter="Basic operation",
        locator=EvidenceLocator(),
        score=EvidenceScore(relevance=1.0, teaching_value=1.0, confidence=1.0),
    )


def _plan(*, needs_case: bool = False, needs_exercises: bool = False, primary: list[str] | None = None) -> BookPlan:
    section = BookSectionPlan(
        section_id="section-01",
        section_no="1.1",
        title="Basic operation",
        knowledge_point_ids=["basic operation"],
        primary_material_ids=list(primary or ["chunk-1"]),
        needs_case=needs_case,
        needs_exercises=needs_exercises,
        section_purpose="Explain the basic operation and its quality conditions.",
        expected_learning_outcome="Explain the basic operation and identify quality conditions.",
        current_task_action="Complete the basic operation and verify the result.",
        knowledge_scope=["basic operation"],
        case_purpose="Apply the operation to a bounded situation." if needs_case else "",
        exercise_purpose="Practice the operation in the task." if needs_exercises else "",
        assessment_purpose="Assess the observable operation and quality judgment.",
        activity_purpose="Observe the operation and its result.",
    )
    return BookPlan(
        book_id="book-1",
        title="Basic operation",
        planning_strategy="rule",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter-01",
                chapter_no=1,
                title="Basic operation",
                learning_goals=["Explain and perform the basic operation."],
                sections=[section],
                primary_material_ids=list(primary or ["chunk-1"]),
            )
        ],
    )


def _inputs(
    *,
    plan: BookPlan | None = None,
    chunks: list[EvidenceChunk] | None = None,
    authorized: list[str] | None = None,
    constraints: list[dict[str, object]] | None = None,
):
    plan = plan or _plan()
    chunks = chunks or [_chunk()]
    report = diagnose_book_plan_completeness(
        plan,
        chunks,
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    assert report.safe_to_freeze is True, report.counts
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=authorized if authorized is not None else ["chunk-1"],
    )
    packet = build_section_evidence_packet(plan, "section-01", blueprint, chunks)
    brief = build_section_authoring_brief(
        plan,
        "section-01",
        blueprint,
        packet,
        occurrence_constraints=constraints,
        prior_verified_support={"basic operation": {"EXPLAIN": ["occ-prior"]}},
    )
    return plan, blueprint, packet, brief


def _draft(brief, *, body: str = "The basic operation follows the documented procedure and checks the result.", **extra):
    supported = [
        obligation.obligation_id
        for obligation in brief.required_obligations
        if brief.authorized_evidence_per_obligation.get(obligation.obligation_id, ())
    ]
    common = set.intersection(*[
        set(brief.authorized_evidence_per_obligation[item]) for item in supported
    ]) if supported else set()
    if supported and common:
        blocks = [{
            "block_id": "block-01",
            "text": body,
            "intended_obligation_ids": supported,
            "intended_occurrence_ids": [],
            # Writer-facing evidence references are packet-local aliases.
            "evidence_ids": ["E1"],
        }]
    else:
        blocks = []
        for index, obligation_id in enumerate(supported, start=1):
            blocks.append({
                "block_id": f"block-{index:02d}",
                "text": body,
                "intended_obligation_ids": [obligation_id],
                "intended_occurrence_ids": [],
                "evidence_ids": ["E1"],
            })
    draft = {
        "blocks": blocks,
        "evidence_usage": [],
        "generation_provenance": {"writer": "fake-writer", "model": "fixture"},
    }
    draft.update(extra)
    return draft


def test_section_brief_compiles_obligations_and_occurrence_constraints() -> None:
    plan, blueprint, packet, brief = _inputs(
        constraints=[
            {
                "occurrence_id": "occ-1",
                "role": "APPLY",
                "already_available_facets": ["EXPLAIN"],
                "must_not_reteach_facets": ["EXPLAIN"],
                "contribution_goal": "Use the operation in the current task.",
            }
        ]
    )

    assert brief.outline_node_id == "section-01"
    assert brief.required_obligations
    assert brief.authorized_evidence_per_obligation
    assert "EXPLAIN" in brief.may_recap
    assert "EXPLAIN" in brief.forbidden_reteach
    assert "Use the operation in the current task." in brief.required_current_contribution
    assert brief.section_new_contribution == brief.required_current_contribution
    assert brief.provenance["writer_may_replan"] is False
    assert book_plan_deep_equal(plan, _plan())
    assert blueprint.blueprint_id == packet.blueprint_id


def test_writer_contract_exposes_selected_evidence_content_not_aliases_or_chunk_ids() -> None:
    _, _, packet, brief = _inputs()
    messages = build_section_authoring_messages(brief, packet, [_chunk()])
    user_prompt = messages[1]["content"]
    assert "E1" not in user_prompt
    assert "chunk-1" not in user_prompt
    assert build_evidence_alias_map(packet) == {"E1": "chunk-1"}


def test_supported_claim_envelope_is_internal_and_writer_visible_without_ids() -> None:
    _, _, packet, brief = _inputs()
    assert brief.supported_claim_envelopes
    envelope = brief.supported_claim_envelopes[0]
    assert envelope.supporting_evidence_ids == ("chunk-1",)
    assert envelope.to_dict(include_internal_ids=True)["supporting_evidence_ids"] == ["chunk-1"]
    writer_view = envelope.to_dict(include_internal_ids=False)
    assert "supporting_evidence_ids" not in writer_view
    messages = build_section_authoring_messages(brief, packet, [_chunk()])
    prompt = "\n".join(item["content"] for item in messages)
    assert "supported_claim_envelope" in prompt
    assert "Do not complete missing technical details from general knowledge" in prompt
    assert "chunk-1" not in prompt


def test_production_authoring_contract_promotes_contribution_and_differentiated_activity_guidance() -> None:
    _, _, packet, brief = _inputs(
        constraints=[
            {
                "occurrence_id": "occ-1",
                "role": "TEACH",
                "required_current_contribution": ["Explain the supported operation and its relation to the result."],
            }
        ]
    )
    contract = build_section_authoring_messages(brief, packet, [_chunk()])[1]["content"]
    assert "section_new_contribution" in contract
    assert "Explain the supported operation and its relation to the result." in contract
    guidance = build_section_activity_guidance(brief)
    assert guidance
    assert all(item["exercise_type"] for item in guidance)
    assert "Do not write system language" in contract


def test_preview_production_parity_keeps_three_responsibility_families_distinct() -> None:
    """The promoted production contract keeps v3's differentiated activity forms.

    This is intentionally deterministic: the same section brief that drives the
    preview contract is compiled by the production helper, and the three
    representative responsibility families must not collapse to one generic
    exercise template.
    """
    _, _, packet, brief = _inputs()
    guidance = {item["kind"]: item["exercise_type"] for item in build_section_activity_guidance(brief)}
    assert guidance["concept_principle"] == "classification_or_comparison"
    assert guidance["procedure_operation"] == "ordering_or_missing_step"
    assert guidance["parameter_condition"] == "parameter_identification_or_comparison"
    assert len({
        guidance["concept_principle"],
        guidance["procedure_operation"],
        guidance["parameter_condition"],
    }) == 3
    contract = "\n".join(item["content"] for item in build_section_authoring_messages(brief, packet, [_chunk()]))
    assert "fact to its explanation" in contract and "student takeaway" in contract


def test_source_bounded_out_of_scope_obligation_is_audit_only_not_writer_responsibility() -> None:
    plan = _plan()
    chunks = [_chunk()]
    report = diagnose_book_plan_completeness(
        plan,
        chunks,
        domain_config=DomainConfig(domain_name="general topic", chapter_order=["Basic operation"]),
    )
    blueprint = build_section_teaching_blueprint(
        plan,
        "section-01",
        completeness_report=report,
        source_book_plan_signature=book_plan_fingerprint(plan),
        authorized_evidence_ids=["chunk-1"],
        source_bounded_calibration={
            "obligation_calibrations": [
                {
                    "obligation_id": "section-01:obligation:concept_principle:01",
                    "original_required": "REQUIRED",
                    "calibrated_status": "SOURCE_SUPPORTED_REQUIRED",
                    "calibrated_scope": "Explain the supported operation.",
                    "rationale": "source-supported",
                },
                {
                    "obligation_id": "section-01:obligation:procedure_operation:01",
                    "original_required": "REQUIRED",
                    "calibrated_status": "OUT_OF_SOURCE_SCOPE",
                    "calibrated_scope": "",
                    "rationale": "procedure execution is outside the supplied source depth",
                },
            ]
        },
    )
    packet = build_section_evidence_packet(plan, "section-01", blueprint, chunks)
    brief = build_section_authoring_brief(plan, "section-01", blueprint, packet)
    brief_ids = {item.obligation_id for item in brief.all_obligations}
    assert "section-01:obligation:procedure_operation:01" not in brief_ids
    assert brief.provenance["source_bounded_obligation_audit"][
        "section-01:obligation:procedure_operation:01"
    ]["source_boundary_status"] == "OUT_OF_SOURCE_SCOPE"
    assert all(item.required != "NOT_APPLICABLE" for item in brief.all_obligations)


def test_real_chunk_id_is_rejected_as_writer_evidence_reference() -> None:
    _, _, packet, brief = _inputs()
    draft = _draft(brief)
    draft["blocks"][0]["evidence_ids"] = ["chunk-1"]
    with pytest.raises(SectionAuthoringError, match=INVALID_EVIDENCE_REFERENCE):
        materialize_section_draft(brief, packet, draft)


def test_block_alias_whitelist_uses_evidence_id_domain_before_alias_conversion() -> None:
    plan = _plan()
    plan.chapters[0].sections[0].reference_material_ids = ["chunk-2"]
    chunks = [_chunk("chunk-1"), _chunk("chunk-2")]
    _, _, packet, brief = _inputs(plan=plan, chunks=chunks, authorized=["chunk-1", "chunk-2"])
    inverse = build_evidence_id_to_alias_map(packet)
    obligation = next(
        item
        for item in brief.required_obligations
        if "chunk-2" in brief.authorized_evidence_per_obligation[item.obligation_id]
    )
    allowed_ids, allowed_aliases = allowed_evidence_aliases_for_block(
        [obligation.obligation_id], brief, inverse
    )
    assert "chunk-2" in allowed_ids
    assert inverse["chunk-2"] in allowed_aliases
    assert all(alias.startswith("E") for alias in allowed_aliases)
    prompt = build_section_authoring_messages(brief, packet, chunks)[1]["content"]
    assert "chunk-2" not in prompt
    assert inverse["chunk-2"] not in prompt

    draft = {
        "blocks": [
            {
                "block_id": "authorized-block",
                "text": "The documented operation is supported by the authorized evidence.",
                "intended_obligation_ids": [obligation.obligation_id],
                "intended_occurrence_ids": [],
                "evidence_ids": [inverse["chunk-2"]],
            }
        ],
        "generation_provenance": {"writer": "fixture"},
    }
    rendered = materialize_section_draft(brief, packet, draft)
    assert set(rendered.evidence_usage[0]["evidence_ids"]) == {"chunk-1", "chunk-2"}

    draft["blocks"][0]["evidence_ids"] = ["E3"]
    with pytest.raises(SectionAuthoringError, match=INVALID_EVIDENCE_REFERENCE):
        materialize_section_draft(brief, packet, draft)


def test_fake_writer_materializes_one_coherent_body_and_maps_obligations_occurrences_and_evidence() -> None:
    plan, _, packet, brief = _inputs(
        constraints=[{"occurrence_id": "occ-1", "role": "TEACH"}]
    )
    body = "The basic operation follows the documented procedure and checks the result."
    draft = _draft(
        brief,
        body=body,
    )
    draft["blocks"][0]["intended_occurrence_ids"] = ["occ-1"]
    rendered = author_section(brief, packet, lambda _brief: draft)

    assert rendered.render_status in {RENDERED, RENDERED_PARTIAL}
    assert rendered.student_visible_body == body
    assert rendered.obligation_span_map
    assert rendered.occurrence_span_map["occ-1"][0]["text"] == body
    assert rendered.occurrence_span_map["occ-1"][0]["start"] == 0
    assert rendered.occurrence_span_map["occ-1"][0]["end"] == len(body)
    assert rendered.generation_provenance["model_authored_spans"] is False
    assert rendered.evidence_usage
    assert rendered.generation_provenance["writer"] == "fake-writer"
    assert book_plan_deep_equal(plan, _plan())


def test_single_occurrence_body_without_explicit_association_gets_deterministic_mapping() -> None:
    plan, _, packet, brief = _inputs(
        constraints=[{"occurrence_id": "occ-1", "role": "TEACH"}]
    )
    draft = _draft(brief, body="The operation is explained and checked against the documented evidence.")
    rendered = author_section(brief, packet, lambda _brief: draft)

    assert rendered.occurrence_span_map["occ-1"][0]["start"] == 0
    assert rendered.generation_provenance["model_authored_spans"] is False


def test_multi_occurrence_mapping_uses_exact_knowledge_owner_not_ordinal() -> None:
    _, _, packet, brief = _inputs(
        constraints=[
            {"occurrence_id": "occ-a", "knowledge_id": "knowledge-a", "role": "TEACH"},
            {"occurrence_id": "occ-b", "knowledge_id": "knowledge-b", "role": "TEACH"},
        ]
    )
    obligation = brief.required_obligations[0]
    owned_obligation = replace(
        obligation,
        linked_occurrence_ids=("occ-a", "occ-b"),
        target_knowledge_ids=("knowledge-a",),
    )
    multi_brief = replace(
        brief,
        required_obligations=(owned_obligation,),
        optional_obligations=(),
        occurrence_constraints=(
            {"occurrence_id": "occ-a", "knowledge_id": "knowledge-a", "role": "TEACH"},
            {"occurrence_id": "occ-b", "knowledge_id": "knowledge-b", "role": "TEACH"},
        ),
    )
    rendered = materialize_section_draft(
        multi_brief,
        packet,
        {
            "blocks": [{
                "block_id": "b-a",
                "channel": "body",
                "text": "The supported operation is explained.",
                "intended_obligation_ids": [owned_obligation.obligation_id],
                "intended_occurrence_ids": [],
                "evidence_ids": ["E1"],
            }]
        },
    )
    assert set(rendered.occurrence_span_map) == {"occ-a"}


def test_multi_occurrence_mapping_ambiguous_fails_closed() -> None:
    _, _, packet, brief = _inputs(
        constraints=[
            {"occurrence_id": "occ-a", "knowledge_id": "knowledge-a", "role": "TEACH"},
            {"occurrence_id": "occ-b", "knowledge_id": "knowledge-b", "role": "TEACH"},
        ]
    )
    obligation = brief.required_obligations[0]
    ambiguous = replace(
        obligation,
        linked_occurrence_ids=("occ-a", "occ-b"),
        target_knowledge_ids=("knowledge-a", "knowledge-b"),
    )
    multi_brief = replace(
        brief,
        required_obligations=(ambiguous,),
        optional_obligations=(),
        occurrence_constraints=(
            {"occurrence_id": "occ-a", "knowledge_id": "knowledge-a", "role": "TEACH"},
            {"occurrence_id": "occ-b", "knowledge_id": "knowledge-b", "role": "TEACH"},
        ),
    )
    with pytest.raises(SectionAuthoringError, match="AMBIGUOUS_OCCURRENCE_MAPPING"):
        materialize_section_draft(
            multi_brief,
            packet,
            {
                "blocks": [{
                    "block_id": "b-ambiguous",
                    "channel": "body",
                    "text": "The supported operation is explained.",
                    "intended_obligation_ids": [ambiguous.obligation_id],
                    "intended_occurrence_ids": [],
                    "evidence_ids": ["E1"],
                }]
            },
        )


def test_case_exercise_assessment_and_summary_requirements_are_not_silent() -> None:
    plan, _, packet, brief = _inputs(plan=_plan(needs_case=True, needs_exercises=True))
    assert brief.case_activity_requirement == "REQUIRED"
    assert brief.exercise_requirement == "REQUIRED"
    assert brief.assessment_requirement == "REQUIRED"
    assert brief.summary_requirement == "OPTIONAL"
    body = "The basic operation follows the documented procedure and checks the result."
    draft = _draft(
        brief,
        body=body,
    )
    allowed = next(iter(brief.authorized_evidence_per_obligation.values()))[0]
    for channel, text, predicate in (
        ("case_activity", "Apply the operation to the bounded situation.", lambda item: item.kind == "case_activity"),
        ("exercise", "Practice the operation and check the result.", lambda item: "exercise_purpose" in item.source_field),
        ("assessment", "Assess the operation and the quality result.", lambda item: "assessment_purpose" in item.source_field),
        ("summary", "The learner can complete and verify the operation.", lambda item: item.kind == "summary"),
    ):
        obligation = next((item for item in brief.all_obligations if predicate(item)), None)
        if obligation is not None:
            draft["blocks"].append({
                "block_id": f"{channel}-block",
                "channel": channel,
                "text": text,
                "intended_obligation_ids": [obligation.obligation_id],
                "intended_occurrence_ids": [],
                # Summary is pedagogical synthesis and must not claim a
                # factual evidence alias of its own.
                "evidence_ids": [] if channel == "summary" else ["E1"],
            })
    rendered = author_section(brief, packet, lambda _brief: draft)
    assert rendered.case_activity
    assert rendered.exercises
    assert rendered.assessment
    assert rendered.summary


def test_summary_is_derived_without_summary_specific_evidence_and_rejects_new_fact() -> None:
    plan, _, packet, brief = _inputs()
    summary = next(item for item in brief.all_obligations if item.kind == "summary")
    draft = _draft(brief, body="The basic operation is explained and its result is checked.")
    draft["blocks"].append(
        {
            "block_id": "summary-block",
            "channel": "summary",
            "text": "The section connects the operation with the stated quality check.",
            "intended_obligation_ids": [summary.obligation_id],
            "intended_occurrence_ids": [],
            "evidence_ids": [],
        }
    )
    rendered = author_section(brief, packet, lambda _brief: draft)
    assert rendered.summary
    assert not any("obligation:summary" in reason for reason in rendered.block_reasons)

    factual = deepcopy(draft)
    factual["blocks"][-1]["text"] = "The operation always eliminates every defect."
    rejected = author_section(brief, packet, lambda _brief: factual)
    assert rejected.blocked is True
    assert any(reason.startswith("UNSUPPORTED_RENDERED_CLAIM:") for reason in rejected.block_reasons)


def test_structural_repair_preserves_original_block_content() -> None:
    malformed = (
        '{"blocks":[{"block_id":"b01","channel":"body",'
        '"text":"Explain the operation.","intended_obligation_ids":["o1"],'
        '"intended_occurrence_ids":[],"evidence_ids":["E1"]}]'
    )
    repaired = {
        "blocks": [
            {
                "block_id": "b01",
                "channel": "body",
                "text": "Explain the operation.",
                "intended_obligation_ids": ["o1"],
                "intended_occurrence_ids": [],
                "evidence_ids": ["E1"],
            }
        ]
    }
    assert classify_section_writer_failure("section writer returned invalid JSON", malformed) == "TRUNCATED_OUTPUT"
    assert validate_structural_repair_preserves_content(malformed, repaired) is True
    changed = deepcopy(repaired)
    changed["blocks"][0]["text"] = "Add a new unsupported fact."
    assert validate_structural_repair_preserves_content(malformed, changed) is False


def test_section_writer_failure_classification_is_non_semantic() -> None:
    assert classify_section_writer_failure("section writer returned invalid JSON", "{\"blocks\": [") == "TRUNCATED_OUTPUT"
    assert classify_section_writer_failure("section writer returned invalid JSON", "{\"blocks\": []}") == "INVALID_JSON"
    assert classify_section_writer_failure("block uses unauthorized evidence alias") == "INVALID_ALIAS"
    assert classify_section_writer_failure("missing required block") == "MISSING_REQUIRED_BLOCK"
    assert classify_section_writer_failure("schema mismatch: object expected") == "SCHEMA_MISMATCH"


def test_partial_obligation_can_render_only_authorized_supported_portion() -> None:
    plan = _plan()
    chunks = [_chunk("chunk-1", "basic operation")]
    plan, _, packet, brief = _inputs(plan=plan, chunks=chunks)
    partial_ids = {
        item.obligation_id
        for item in brief.required_obligations
        if item.evidence_status == PARTIAL_OBLIGATION
    }
    assert partial_ids
    body = "The basic operation is introduced with the supported evidence."
    rendered = materialize_section_draft(brief, packet, _draft(brief, body=body))
    assert rendered.render_status in {RENDERED, RENDERED_PARTIAL}
    assert rendered.generation_provenance["verified_availability_eligible"] is False


def test_local_source_gap_does_not_erase_supported_section_content() -> None:
    plan = _plan()
    chunks = [_chunk("chunk-1", "Explain the basic operation and complete the basic operation.")]
    plan, _, packet, brief = _inputs(plan=plan, chunks=chunks)
    assert any(item.source_gap for item in brief.required_obligations)
    body = "The supported part of the basic operation is explained and can be completed."
    draft = _draft(brief, body=body)
    rendered = materialize_section_draft(brief, packet, draft)
    assert rendered.body
    assert rendered.render_status == RENDERED_PARTIAL
    assert rendered.blocked is True
    assert any(reason.startswith("SOURCE_GAP:") for reason in rendered.block_reasons)


def test_core_source_gap_blocks_section_without_model_common_sense() -> None:
    plan = _plan()
    plan, blueprint, packet, brief = _inputs(plan=plan, chunks=[], authorized=[])
    assert all(
        item.status == SOURCE_GAP
        for item in packet.obligation_bindings
        if item.obligation_kind != "summary"
    )
    summary_binding = next(
        item for item in packet.obligation_bindings if item.obligation_kind == "summary"
    )
    assert summary_binding.status == SUPPORTED_OBLIGATION
    assert summary_binding.provenance["derivable_from_verified_content"] is True
    rendered = materialize_section_draft(brief, packet, {"blocks": []})
    assert rendered.render_status == BLOCKED_CORE_SOURCE_GAP
    assert rendered.blocked is True
    assert not rendered.body


def test_internal_labels_and_forbidden_reteach_are_rejected() -> None:
    plan, _, packet, brief = _inputs(
        constraints=[{"occurrence_id": "occ-1", "role": "TEACH", "forbidden_content": ["complete definition"]}]
    )
    with pytest.raises(SectionAuthoringError, match="internal semantic label"):
        materialize_section_draft(brief, packet, _draft(brief, body="This section teaches EXPLAIN."))
    with pytest.raises(SectionAuthoringError, match="forbidden reteach"):
        materialize_section_draft(brief, packet, _draft(brief, body="The complete definition is repeated here."))


def test_unauthorized_evidence_and_unknown_span_mapping_are_rejected() -> None:
    plan, _, packet, brief = _inputs()
    body = "The basic operation follows the documented procedure and checks the result."
    first_obligation = brief.required_obligations[0].obligation_id
    unauthorized = _draft(brief, body=body)
    unauthorized["blocks"][0]["evidence_ids"] = ["not-authorized"]
    with pytest.raises(SectionAuthoringError, match="INVALID_EVIDENCE_REFERENCE"):
        materialize_section_draft(brief, packet, unauthorized)

    unknown = _draft(
        brief,
        body=body,
    )
    unknown["blocks"][0]["intended_occurrence_ids"] = ["unknown-occurrence"]
    with pytest.raises(SectionAuthoringError, match="unknown occurrences"):
        materialize_section_draft(brief, packet, unknown)


def test_model_authored_exact_span_maps_are_rejected() -> None:
    _, _, packet, brief = _inputs()
    with pytest.raises(SectionAuthoringError, match="exact span maps"):
        materialize_section_draft(
            brief,
            packet,
            {"body": "ignored", "obligation_span_map": {}, "occurrence_span_map": {}},
        )


def test_deterministic_materialization_runs_local_claim_audit_and_keeps_grant_order() -> None:
    _, _, packet, brief = _inputs(constraints=[{"occurrence_id": "occ-1", "role": "TEACH"}])
    rendered = materialize_section_draft(brief, packet, _draft(brief))
    provenance = rendered.generation_provenance
    assert provenance["model_authored_spans"] is False
    assert "obligation_coverage" in provenance
    assert "local_claim_evidence_audit" in provenance
    assert provenance["grant_order"].startswith("materialization -> obligation coverage")
    assert provenance["verified_availability_eligible"] in {True, False}


def test_source_gap_block_cannot_claim_factual_content() -> None:
    _, _, packet, brief = _inputs(chunks=[], authorized=[])
    source_gap = brief.required_obligations[0].obligation_id
    with pytest.raises(SectionAuthoringError, match="INVALID_EVIDENCE_REFERENCE"):
        materialize_section_draft(
            brief,
            packet,
            {
                "blocks": [{
                    "block_id": "gap-block",
                    "text": "The unsupported operation is safe.",
                    "intended_obligation_ids": [source_gap],
                    "intended_occurrence_ids": [],
                    "evidence_ids": ["not-authorized"],
                }]
            },
        )


def test_partial_evidence_does_not_establish_full_verified_grant() -> None:
    _, _, packet, brief = _inputs(chunks=[_chunk("chunk-1", "basic operation")])
    rendered = materialize_section_draft(brief, packet, _draft(brief, body="The basic operation always guarantees the result."))
    assert rendered.render_status == RENDERED_PARTIAL
    assert rendered.generation_provenance["verified_availability_eligible"] is False
    counts = rendered.generation_provenance["local_claim_status_counts"]
    assert counts["PARTIALLY_SUPPORTED"] + counts["UNSUPPORTED"] >= 0


def test_bookplan_blueprint_and_packet_remain_immutable() -> None:
    plan, blueprint, packet, brief = _inputs()
    before = deepcopy(plan)
    assert book_plan_fingerprint(plan) == brief.source_book_plan_signature
    assert book_plan_deep_equal(plan, before)
    with pytest.raises(FrozenInstanceError):
        brief.section_title = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        packet.packet_id = "changed"  # type: ignore[misc]
    assert book_plan_deep_equal(plan, before)
