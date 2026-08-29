from __future__ import annotations

from materials2textbook.knowledge_map.execution import execute_verified_occurrences
from materials2textbook.knowledge_map.models import (
    BookPosition,
    InstructionalAvailabilityState,
    KnowledgeKind,
    KnowledgePoint,
    LearningRole,
    PlannedOccurrence,
    PrerequisiteUse,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.rendered_conformance import wrap_rendered_occurrence
from materials2textbook.knowledge_map.semantic_evaluation import (
    _apply_source_facet_ceiling,
    compile_occurrence_for_verified_availability,
)
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


class _EntailmentJudge:
    model = "fixture-entailment"

    def __init__(self, status: str) -> None:
        self.status = status
        self.call_count = 0

    def judge(self, *, claim: str, evidence: list[dict[str, str]], context: dict[str, str]) -> dict:
        self.call_count += 1
        return {
            "status": self.status,
            "supporting_evidence_ids": [item["evidence_id"] for item in evidence[:1]],
            "rationale": "fixture semantic evidence decision",
            "confidence": 1.0,
            "unsupported_part": "" if self.status == "SUPPORTED" else "unsupported fixture detail",
        }


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="generic method",
        content="The generic method is defined and explained for the current task.",
        summary="The generic method is defined and explained for the current task.",
        keywords=["generic method"],
        subject="fixture",
        material_block="fixture",
        material_block_code="fixture",
        recommended_chapter="fixture",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=1.0, confidence=1.0),
        source_type="document_segment",
        review_status="approved",
    )


def _occurrence(occurrence_id: str, ordinal: int = 1) -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id=occurrence_id,
        knowledge_id="kp:generic-method",
        source_knowledge_point_id="source:generic-method",
        position=BookPosition(1, ordinal, ordinal),
        chapter_id="chapter_01",
        section_id=f"section_{ordinal:02d}",
        context_title="generic method task",
        source_chunk_ids=["C1"],
        role=LearningRole.TEACH,
        trusted_for_state=True,
    )


def _delta(occurrence_id: str, *, new_facets: list[str], uses_prior: bool = False) -> SemanticDelta:
    return SemanticDelta(
        occurrence_id=occurrence_id,
        repeats_prior_explanation=False,
        uses_prior_knowledge=uses_prior,
        recall_needed=False,
        required_self_facets=[],
        required_self_extension_keys=[],
        cross_prerequisite_uses=[],
        new_facets=new_facets,
        new_extension_keys=[],
        new_context="current task" if uses_prior else "",
        repeated_aspects=[],
        contribution_summary="apply the method" if uses_prior else "teach the method",
        confidence=0.95,
        rationale="fixture semantic plan",
        evidence_chunk_ids=["C1"],
    )


def _source() -> SourceKnowledgePoint:
    return SourceKnowledgePoint(
        source_knowledge_point_id="source:generic-method",
        title="generic method",
        chapter_id="chapter_01",
        section_id="section_01",
        chapter_ordinal=1,
        section_ordinal=1,
        task_ordinal=1,
        source_point_ordinal=1,
        context_title="generic method task",
        source_chunk_ids=["C1"],
    )


def _point() -> KnowledgePoint:
    return KnowledgePoint(
        knowledge_id="kp:generic-method",
        title="generic method",
        aliases=[],
        kind=KnowledgeKind.METHOD,
        source_chunk_ids=["C1"],
        extraction_confidence=1.0,
    )


def _run_first(body: str, *, semantic_status: str, provenance: str = "llm"):
    occurrence = _occurrence("A1")

    def render(brief):
        return wrap_rendered_occurrence(brief, body, generation_provenance=provenance)

    return execute_verified_occurrences(
        occurrences=[occurrence],
        deltas=[_delta("A1", new_facets=["EXPLAIN"])],
        sources={"source:generic-method": _source()},
        points={"kp:generic-method": _point()},
        chunks=[_chunk()],
        render_occurrence=render,
        semantic_entailment_judge=_EntailmentJudge(semantic_status),
    )


def test_rule_template_explain_is_not_a_verified_grant() -> None:
    result = _run_first(
        "围绕当前任务，重点学习本次计划新增的内容：EXPLAIN。",
        semantic_status="SUPPORTED",
        provenance="rule_template_fallback",
    )

    assert result.transitions[0]["grant_applied"] is False
    assert result.verified_state.availability_by_knowledge == {}
    assert "fallback_body_not_instructional" in result.transitions[0]["blocked_reasons"]


def test_evidence_backed_explain_with_semantic_support_grants_explain() -> None:
    result = _run_first(
        "The generic method is defined and explained for the current task. Evidence: C1",
        semantic_status="SUPPORTED",
    )

    assert result.transitions[0]["conformance"] == "MATCH"
    assert result.transitions[0]["evidence"] == "SUPPORTED"
    assert result.transitions[0]["grant_applied"] is True
    assert result.verified_state.availability_by_knowledge["kp:generic-method"].available_facets == ["EXPLAIN"]


def test_semantic_partial_does_not_establish_full_explain() -> None:
    result = _run_first(
        "The generic method is defined and explained for the current task. Evidence: C1",
        semantic_status="PARTIALLY_SUPPORTED",
    )

    assert result.transitions[0]["conformance"] == "MATCH"
    assert result.transitions[0]["evidence"] == "UNCERTAIN"
    assert result.transitions[0]["grant_applied"] is False
    assert result.verified_state.availability_by_knowledge == {}


def test_semantic_unsupported_does_not_establish_explain_or_downstream_apply() -> None:
    first = _occurrence("A1", 1)
    second = _occurrence("A2", 2)
    calls: list[str] = []

    def render(brief):
        calls.append(brief.occurrence_id)
        body = "The generic method is defined and explained for the current task. Evidence: C1"
        return wrap_rendered_occurrence(brief, body, generation_provenance="llm")

    result = execute_verified_occurrences(
        occurrences=[first, second],
        deltas=[_delta("A1", new_facets=["EXPLAIN"]), _delta("A2", new_facets=[], uses_prior=True)],
        sources={"source:generic-method": _source()},
        points={"kp:generic-method": _point()},
        chunks=[_chunk()],
        render_occurrence=render,
        semantic_entailment_judge=_EntailmentJudge("UNSUPPORTED"),
    )

    assert calls == ["A1"]
    assert result.transitions[0]["grant_applied"] is False
    assert any(item["issue_code"] == "PRIOR_TEACHING_NOT_VERIFIED" for item in result.blocked_occurrences)


def test_untrusted_cross_prerequisite_is_audited_but_cannot_block_runtime() -> None:
    current = _occurrence("current")
    delta = _delta("current", new_facets=["EXPLAIN"])
    delta.cross_prerequisite_uses = [PrerequisiteUse(
        knowledge_id="kp:workplace",
        required_facets=["EXPLAIN"],
        relation="HARD",
        use_type="DIRECT",
        rationale="",
        evidence_chunk_ids=[],
        confidence=0.0,
        trusted_for_runtime=True,
    )]

    compiled = compile_occurrence_for_verified_availability(
        seed=current,
        delta=delta,
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="TEACH: current method",
        first_position={"kp:workplace": BookPosition(1, 0, 0)},
    )

    assert compiled.executable is True
    assert compiled.compiled_occurrence is not None
    assert compiled.compiled_occurrence.required_prerequisites == []
    assert any(item["classification"] == "UNTRUSTED_PREREQUISITE_PROPOSAL" for item in compiled.audit)


def test_source_bounded_facet_ceiling_clamps_requested_facet_without_rewriting_task_intent() -> None:
    occurrence = _occurrence("A1")
    delta = _delta("A1", new_facets=["EXPLAIN"])
    audit: list[dict] = []
    capped = _apply_source_facet_ceiling(
        delta,
        occurrence,
        {
            "obligation_calibrations": [{
                "obligation_id": "section_01:obligation:concept_principle:01",
                "calibrated_status": "SOURCE_SUPPORTED_REQUIRED",
                "max_source_supported_facet": "ORIENTED",
                "supporting_evidence_ids": ["C1"],
            }]
        },
        audit,
    )
    assert capped.task_required_facet == "EXPLAIN"
    assert capped.max_source_supported_facet == "ORIENTED"
    assert capped.effective_required_facet == "ORIENTED"
    assert capped.new_facets == ["ORIENTED"]
    assert audit and audit[0]["classification"] == "SOURCE_BOUNDED_FACET_CEILING_APPLIED"


def test_source_bounded_facet_ceiling_preserves_highest_supported_lower_facet() -> None:
    occurrence = _occurrence("A1")
    delta = _delta("A1", new_facets=["ANALYZE"])
    audit: list[dict] = []
    capped = _apply_source_facet_ceiling(
        delta,
        occurrence,
        {
            "obligation_calibrations": [{
                "obligation_id": "section_01:obligation:concept_principle:01",
                "calibrated_status": "SOURCE_SUPPORTED_REQUIRED",
                "max_source_supported_facet": "EXPLAIN",
            }]
        },
        audit,
    )
    assert capped.task_required_facet == "ANALYZE"
    assert capped.max_source_supported_facet == "EXPLAIN"
    assert capped.effective_required_facet == "EXPLAIN"
    assert capped.new_facets == ["EXPLAIN"]


def test_prerequisite_minimum_facet_is_independent_of_upstream_planned_facet() -> None:
    current = _occurrence("B1")
    delta = _delta("B1", new_facets=["EXPLAIN"])
    delta.cross_prerequisite_uses = [PrerequisiteUse(
        knowledge_id="kp:generic-method",
        required_facets=["EXPLAIN"],
        minimum_required_facet="ORIENTED",
        necessity="HARD",
        relation="HARD",
        use_type="DIRECT",
        rationale="The task only needs recognition of the earlier equipment.",
        evidence_chunk_ids=["C1"],
        provenance="task analysis",
        confidence=0.95,
        trusted_for_runtime=True,
    )]
    state = InstructionalAvailabilityState()
    state.availability_by_knowledge["kp:generic-method"] = type(
        "Record", (), {"available_facets": ["ORIENTED"], "available_extension_keys": []}
    )()
    compiled = compile_occurrence_for_verified_availability(
        seed=current,
        delta=delta,
        verified_before=state,
        has_previous=False,
        source_context="equipment setup",
        first_position={"kp:generic-method": BookPosition(1, 0, 0)},
    )
    assert compiled.executable is True
    assert compiled.compiled_occurrence is not None
    assert compiled.compiled_occurrence.required_prerequisites[0].required_facets == ["ORIENTED"]


def test_source_bounded_prerequisite_overlay_uses_effective_graph_not_candidate_facets() -> None:
    current = _occurrence("B1")
    delta = _delta("B1", new_facets=["EXPLAIN"])
    delta.cross_prerequisite_uses = [
        PrerequisiteUse(
            knowledge_id="kp:equipment",
            required_facets=["EXPLAIN"],
            relation="HARD",
            use_type="DIRECT",
            rationale="planner candidate",
            evidence_chunk_ids=["C1"],
            provenance="planner",
            confidence=0.95,
            trusted_for_runtime=True,
        ),
        PrerequisiteUse(
            knowledge_id="kp:ppe",
            required_facets=["EXPLAIN"],
            relation="HARD",
            use_type="DIRECT",
            rationale="planner candidate",
            evidence_chunk_ids=["C1"],
            provenance="planner",
            confidence=0.95,
            trusted_for_runtime=True,
        ),
    ]
    state = InstructionalAvailabilityState()
    state.availability_by_knowledge["kp:equipment"] = type(
        "Record", (), {"available_facets": ["EXPLAIN"], "available_extension_keys": []}
    )()
    overlay = {
        "constraints": [
            {
                "downstream_occurrence_id": "B1",
                "prerequisite_knowledge_id": "kp:equipment",
                "necessity": "HARD",
                "minimum_required_facet": "ORIENTED",
                "rationale": "The task needs recognition of the earlier equipment.",
                "provenance": "source-bounded task audit",
                "supporting_evidence_ids": ["C1"],
            },
            {
                "downstream_occurrence_id": "B1",
                "prerequisite_knowledge_id": "kp:ppe",
                "necessity": "NOT_REQUIRED",
                "minimum_required_facet": "",
                "rationale": "PPE selection is not a prerequisite for this task.",
                "provenance": "source-bounded task audit",
                "supporting_evidence_ids": ["C1"],
            },
        ]
    }
    compiled = compile_occurrence_for_verified_availability(
        seed=current,
        delta=delta,
        verified_before=state,
        has_previous=False,
        source_context="equipment setup",
        first_position={"kp:equipment": BookPosition(1, 0, 0), "kp:ppe": BookPosition(1, 0, 1)},
        source_bounded_prerequisite_calibration=overlay,
    )
    assert compiled.executable is True
    assert compiled.compiled_occurrence is not None
    assert [item.knowledge_id for item in compiled.compiled_occurrence.required_prerequisites] == ["kp:equipment"]
    assert compiled.compiled_occurrence.required_prerequisites[0].required_facets == ["ORIENTED"]
    assert any(item["classification"] == "SOURCE_BOUNDED_PREREQUISITE_CALIBRATION_APPLIED" for item in compiled.audit)


def test_invalid_source_bounded_prerequisite_overlay_fails_closed() -> None:
    current = _occurrence("B1")
    delta = _delta("B1", new_facets=["EXPLAIN"])
    delta.cross_prerequisite_uses = [PrerequisiteUse(
        knowledge_id="kp:equipment",
        required_facets=["EXPLAIN"],
        relation="HARD",
        use_type="DIRECT",
        rationale="planner candidate",
        evidence_chunk_ids=["C1"],
        provenance="planner",
        confidence=0.95,
        trusted_for_runtime=True,
    )]
    state = InstructionalAvailabilityState()
    state.availability_by_knowledge["kp:equipment"] = type(
        "Record", (), {"available_facets": ["ORIENTED"], "available_extension_keys": []}
    )()
    compiled = compile_occurrence_for_verified_availability(
        seed=current,
        delta=delta,
        verified_before=state,
        has_previous=False,
        source_context="equipment setup",
        first_position={"kp:equipment": BookPosition(1, 0, 0)},
        source_bounded_prerequisite_calibration={"constraints": [{
            "downstream_occurrence_id": "B1",
            "prerequisite_knowledge_id": "kp:equipment",
            "necessity": "HARD",
            "minimum_required_facet": "",
            "rationale": "",
            "provenance": "",
        }]},
    )
    assert compiled.executable is True
    assert compiled.compiled_occurrence is not None
    assert compiled.compiled_occurrence.required_prerequisites == []
    assert any(item["classification"] == "INVALID_SOURCE_BOUNDED_PREREQUISITE_CALIBRATION" for item in compiled.audit)


def test_complete_prerequisite_calibration_rejects_unlisted_planner_edge() -> None:
    current = _occurrence("B1")
    delta = _delta("B1", new_facets=["EXPLAIN"])
    delta.cross_prerequisite_uses = [
        PrerequisiteUse(
            knowledge_id="kp:equipment",
            required_facets=["EXPLAIN"],
            relation="HARD",
            use_type="DIRECT",
            rationale="planner candidate",
            evidence_chunk_ids=["C1"],
            provenance="planner",
            confidence=0.95,
            trusted_for_runtime=True,
        ),
        PrerequisiteUse(
            knowledge_id="kp:workplace",
            required_facets=["EXPLAIN"],
            relation="HARD",
            use_type="DIRECT",
            rationale="planner candidate",
            evidence_chunk_ids=["C1"],
            provenance="planner",
            confidence=0.95,
            trusted_for_runtime=True,
        ),
    ]
    state = InstructionalAvailabilityState()
    state.availability_by_knowledge["kp:equipment"] = type(
        "Record", (), {"available_facets": ["ORIENTED"], "available_extension_keys": []}
    )()
    compiled = compile_occurrence_for_verified_availability(
        seed=current,
        delta=delta,
        verified_before=state,
        has_previous=False,
        source_context="equipment setup",
        first_position={
            "kp:equipment": BookPosition(1, 0, 0),
            "kp:workplace": BookPosition(1, 0, 1),
        },
        source_bounded_prerequisite_calibration={
            "complete_for_occurrence_ids": ["B1"],
            "constraints": [{
                "downstream_occurrence_id": "B1",
                "prerequisite_knowledge_id": "kp:equipment",
                "necessity": "HARD",
                "minimum_required_facet": "ORIENTED",
                "rationale": "The task needs recognition of the equipment.",
                "provenance": "source-bounded task audit",
                "supporting_evidence_ids": ["C1"],
            }],
        },
    )
    assert compiled.compiled_occurrence is not None
    assert [item.knowledge_id for item in compiled.compiled_occurrence.required_prerequisites] == [
        "kp:equipment"
    ]
    assert any(
        item["classification"] == "UNCALIBRATED_PREREQUISITE_EDGE"
        and item["required_prerequisite_knowledge_id"] == "kp:workplace"
        for item in compiled.audit
    )
