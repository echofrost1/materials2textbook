from __future__ import annotations

from dataclasses import replace
import pytest

from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.knowledge_map.models import (
    BookPosition, KnowledgeMap, LearningTrajectory, PlannedOccurrence, Prerequisite, PrerequisiteUse, SemanticDelta,
)
from materials2textbook.knowledge_map.planning_evidence_gate import (
    ClaimSupportType, EvidenceSupportStatus, apply_planning_evidence_gate, evaluate_planning_evidence,
    evaluate_planning_evidence_from_payload, resolve_evidence_coverage_from_payload,
)
from materials2textbook.knowledge_map.recall_capsules import (
    RecallCapsuleDraft, execute_recall_capsule, plan_recall_capsule,
)
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus, extract_rendered_occurrences, wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.safe_auto_repair import RepairAttemptStatus
from materials2textbook.knowledge_map.semantic_evaluation import SemanticPlanningEvaluation
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief, WritingBriefCoverage, build_writing_brief_coverage_from_payload
from materials2textbook.knowledge_map.writing_briefs import RejectedPlanOccurrence
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


class ScriptedRecallGenerator:
    model_id = "phase3d-scripted"
    prompt_version = "phase3d-scripted.v1"

    def __init__(self, text: str, ids: tuple[str, ...] = ("C001",)) -> None:
        self.text = text
        self.ids = ids
        self.calls = 0

    def generate(self, **kwargs) -> RecallCapsuleDraft:
        self.calls += 1
        return RecallCapsuleDraft(self.text, self.ids, self.model_id, self.prompt_version)


def _evidence(text: str = "The arc source explains how it affects the current task.") -> dict[str, EvidenceChunk]:
    chunk = EvidenceChunk(
        "C001", "A1", "Arc source", text, text, [], "approved", "fixture", "fixture", "fixture",
        EvidenceLocator(), EvidenceScore(),
    )
    return {chunk.chunk_id: chunk}


def _brief(*, occurrence_id: str, role: str, task: int, required: list[str] | None = None) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id, source_knowledge_point_id=f"source:{occurrence_id}", canonical_knowledge_id="kp:arc",
        source_title="Arc source", canonical_title="Arc source", chapter_id="chapter:1", section_id=f"section:{task}",
        role=role, already_available_facets=["EXPLAIN"] if role == "RECALL" else [], required_facets=required or [],
        must_teach_facets=[], must_not_reteach_facets=["EXPLAIN"] if role == "RECALL" else [], extension_keys=[],
        repeated_aspects_to_avoid=[], prerequisite_context=[], contribution_goal="", source_chunk_ids=["C001"],
        writing_contract="immutable role", semantic_delta_evidence_ids=[], task_ordinal=task, occurrence_ordinal=task,
        allowed_content=["minimal context"], forbidden_content=["definition"], max_recap_sentences=2,
        must_include_points=[], must_avoid_patterns=["definition", "complete procedure", "parameter/method rule"],
    )


def _occurrence(*, occurrence_id: str, role: str, task: int, grants: list[str], required: list[str] | None = None) -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id=occurrence_id, knowledge_id="kp:arc", source_knowledge_point_id=f"source:{occurrence_id}",
        position=BookPosition(1, task, task), chapter_id="chapter:1", section_id=f"section:{task}", context_title=role,
        source_chunk_ids=["C001"], role=role, required_self_facets=required or [], intended_grants=grants,
        trusted_for_state=True,
    )


def _records(brief: OccurrenceWritingBrief, body: str = ""):
    [markdown] = extract_rendered_occurrences(wrap_rendered_occurrence(brief, body))
    return markdown, replace(markdown, render_target="digital_book")


def test_planning_evidence_gate_rejects_unsupported_current_limit_before_writer() -> None:
    occurrence = _occurrence(occurrence_id="occ:current-limit", role="EXTEND", task=2, grants=[])
    occurrence.intended_extension_keys = ["constraint:current_limit"]
    occurrence.intended_contribution = "Apply thin plate work in the current task."
    delta = SemanticDelta(
        occurrence_id=occurrence.occurrence_id, repeats_prior_explanation=False, uses_prior_knowledge=True,
        recall_needed=False, required_self_facets=[], required_self_extension_keys=[], cross_prerequisite_uses=[],
        new_facets=[], new_extension_keys=["constraint:current_limit"], new_context="thin plate",
        repeated_aspects=[], contribution_summary=occurrence.intended_contribution, confidence=0.9,
        rationale="Source supports thin plate context only.", evidence_chunk_ids=["C001"],
    )
    knowledge_map = KnowledgeMap("fixture", "sig", [], [], [], [], [occurrence], [LearningTrajectory("kp:arc", [occurrence.occurrence_id])], [], [])
    evaluation = SemanticPlanningEvaluation(knowledge_map, semantic_deltas=[delta])
    evidence = list(_evidence("Thin plate work is discussed, but no electrical setting is stated.").values())

    report = evaluate_planning_evidence(evaluation=evaluation, chunks=evidence)

    decision = report.decisions[0]
    assert decision.status == EvidenceSupportStatus.UNSUPPORTED
    assert not decision.writer_eligible
    assert next(item for item in decision.findings if item.claim_type == "new_extension_keys").unsupported_values == ("constraint:current_limit",)
    coverage = apply_planning_evidence_gate(coverage=WritingBriefCoverage(briefs=[_brief(occurrence_id="occ:current-limit", role="EXTEND", task=2)]), report=report)
    assert not coverage.briefs
    assert coverage.rejected_plan_occurrences[0].reason == "unsupported_planning_claim"


def test_planning_evidence_gate_accepts_directly_supported_plan() -> None:
    occurrence = _occurrence(occurrence_id="occ:teach", role="TEACH", task=1, grants=["EXPLAIN"])
    occurrence.intended_contribution = "Arc source explains the task."
    delta = SemanticDelta(
        occurrence_id=occurrence.occurrence_id, repeats_prior_explanation=False, uses_prior_knowledge=False,
        recall_needed=False, required_self_facets=[], required_self_extension_keys=[], cross_prerequisite_uses=[],
        new_facets=["EXPLAIN"], new_extension_keys=[], new_context="", repeated_aspects=[],
        contribution_summary=occurrence.intended_contribution, confidence=0.9, rationale="The evidence explains the source.", evidence_chunk_ids=["C001"],
    )
    knowledge_map = KnowledgeMap("fixture", "sig", [], [], [], [], [occurrence], [LearningTrajectory("kp:arc", [occurrence.occurrence_id])], [], [])
    report = evaluate_planning_evidence(evaluation=SemanticPlanningEvaluation(knowledge_map, semantic_deltas=[delta]), chunks=list(_evidence().values()))
    assert report.decisions[0].status == EvidenceSupportStatus.SUPPORTED
    assert report.decisions[0].writer_eligible


def test_writer_fails_closed_for_a_gate_rejected_plan_instead_of_using_fallback() -> None:
    rejected = RejectedPlanOccurrence(
        occurrence_id="occ:unsupported", source_knowledge_point_id="source:unsupported", canonical_knowledge_id="kp:unsupported",
        chapter_id="chapter:1", section_id="section:1", task_ordinal=1, occurrence_ordinal=1,
        reason="unsupported_planning_claim", evidence_status="UNSUPPORTED", allowed_evidence_ids=("C001",),
    )
    with pytest.raises(ValueError, match="Planning Evidence Gate rejected"):
        TextbookWriterAgent().run([], [], "fixture", rejected_plan_occurrences=[rejected])


def test_planning_evidence_gate_checks_bound_cross_prerequisite_rationale() -> None:
    occurrence = _occurrence(occurrence_id="occ:cross", role="APPLY", task=2, grants=[])
    occurrence.required_prerequisites = [PrerequisiteUse("kp:base", ["EXPLAIN"], edge_id="edge:base-to-arc")]
    delta = SemanticDelta(
        occurrence_id=occurrence.occurrence_id, repeats_prior_explanation=False, uses_prior_knowledge=True,
        recall_needed=False, required_self_facets=[], required_self_extension_keys=[], cross_prerequisite_uses=occurrence.required_prerequisites,
        new_facets=[], new_extension_keys=[], new_context="", repeated_aspects=[], contribution_summary="", confidence=.9,
        rationale="The base principle is required here.", evidence_chunk_ids=["C001"],
    )
    edge = Prerequisite("edge:base-to-arc", "kp:base", "kp:arc", ["EXPLAIN"], rationale="The source uses the base principle.", confidence=.9, evidence_chunk_ids=["C001"])
    knowledge_map = KnowledgeMap("fixture", "sig", [], [], [], [edge], [occurrence], [LearningTrajectory("kp:arc", [occurrence.occurrence_id])], [], [])
    report = evaluate_planning_evidence(evaluation=SemanticPlanningEvaluation(knowledge_map, semantic_deltas=[delta]), chunks=list(_evidence().values()))
    finding = next(item for item in report.decisions[0].findings if item.claim_type == "cross_prerequisite_rationale")
    assert finding.status == EvidenceSupportStatus.SUPPORTED


def test_trajectory_contribution_does_not_require_a_literal_source_sentence() -> None:
    teach = _occurrence(occurrence_id="occ:teach", role="TEACH", task=1, grants=["EXPLAIN"])
    apply = _occurrence(occurrence_id="occ:apply", role="APPLY", task=2, grants=[])
    apply.intended_contribution = "Use the prior explanation in this task."
    delta = SemanticDelta(
        occurrence_id="occ:apply", repeats_prior_explanation=False, uses_prior_knowledge=True, recall_needed=False,
        required_self_facets=["EXPLAIN"], required_self_extension_keys=[], cross_prerequisite_uses=[],
        new_facets=[], new_extension_keys=[], new_context="task", repeated_aspects=[],
        contribution_summary=apply.intended_contribution, confidence=.9, rationale="Task use follows prior teaching.", evidence_chunk_ids=["C001"],
    )
    knowledge_map = KnowledgeMap("fixture", "sig", [], [], [], [], [teach, apply], [LearningTrajectory("kp:arc", ["occ:teach", "occ:apply"])], [], [])
    report = evaluate_planning_evidence(evaluation=SemanticPlanningEvaluation(knowledge_map, semantic_deltas=[delta]), chunks=list(_evidence("A source fact only.").values()))
    finding = next(item for item in report.decisions[1].findings if item.claim_type == "intended_contribution")
    assert finding.support_type == ClaimSupportType.TRAJECTORY_FACT
    assert finding.status == EvidenceSupportStatus.SUPPORTED
    assert finding.support_references == ("occ:teach",)


def test_payload_binding_retrieves_only_other_slices_of_an_authorised_source_asset() -> None:
    payload = {
        "semantic_deltas": [{"occurrence_id": "occ:gas", "evidence_chunk_ids": ["C001"]}],
        "knowledge_map": {
            "knowledge_points": [{"knowledge_id": "kp:gas", "title": "Shielding gas", "source_chunk_ids": ["C001"]}],
            "source_knowledge_points": [{"source_knowledge_point_id": "source:gas", "title": "Shielding gas", "source_chunk_ids": ["C001"]}],
            "mappings": [], "prerequisites": [],
            "planned_occurrences": [{
                "occurrence_id": "occ:gas", "knowledge_id": "kp:gas", "source_knowledge_point_id": "source:gas",
                "chapter_id": "c", "section_id": "s", "position": {"chapter_ordinal": 1, "task_ordinal": 1, "occurrence_ordinal": 1},
                "role": "TEACH", "intended_grants": ["EXPLAIN"], "intended_extension_keys": [], "intended_contribution": "Teach shielding gas.",
                "source_chunk_ids": ["C001"], "required_prerequisites": [], "trusted_for_state": True,
            }],
        },
    }
    first = _evidence("Overview only.")["C001"]
    sibling = replace(first, chunk_id="C002", summary="The shielding gas principle explains the effect.")
    report = evaluate_planning_evidence_from_payload(payload=payload, chunks=[first, sibling])
    audit = report.decisions[0].binding_audit
    assert audit and audit.retrieved_candidate_ids == ("C002",)
    assert audit.accepted_evidence_ids == ("C001", "C002")


def test_evidence_bounded_contraction_drops_goal_without_remaining_increment() -> None:
    payload = {
        "semantic_deltas": [{"occurrence_id": "occ:extend", "evidence_chunk_ids": ["C001"]}],
        "knowledge_map": {
            "knowledge_points": [{"knowledge_id": "kp:thin", "title": "Thin plate", "source_chunk_ids": ["C001"]}],
            "source_knowledge_points": [{"source_knowledge_point_id": "source:thin", "title": "Thin plate", "source_chunk_ids": ["C001"]}],
            "mappings": [], "prerequisites": [],
            "planned_occurrences": [{
                "occurrence_id": "occ:extend", "knowledge_id": "kp:thin", "source_knowledge_point_id": "source:thin",
                "chapter_id": "c", "section_id": "s", "position": {"chapter_ordinal": 1, "task_ordinal": 1, "occurrence_ordinal": 1},
                "role": "EXTEND", "intended_grants": ["ANALYZE"], "intended_extension_keys": ["constraint:current_limit"],
                "intended_contribution": "Analyze the current limit.", "source_chunk_ids": ["C001"], "required_prerequisites": [], "trusted_for_state": True,
            }],
        },
    }
    resolution = resolve_evidence_coverage_from_payload(payload=payload, chunks=list(_evidence("Thin plate context only.").values()))
    assert resolution.contractions[0].status == "DROP_OCCURRENCE_GOAL"
    assert resolution.contractions[0].review_kind == "SYSTEM_RESOLVABLE"
    assert resolution.contracted_payload["knowledge_map"]["planned_occurrences"][0]["trusted_for_state"] is False
    assert payload["knowledge_map"]["planned_occurrences"][0]["intended_extension_keys"] == ["constraint:current_limit"]
    coverage = build_writing_brief_coverage_from_payload(resolution.contracted_payload)
    assert not coverage.fallback_occurrences
    assert [item.occurrence_id for item in coverage.dropped_occurrence_goals] == ["occ:extend"]


def test_evidence_bounded_contraction_retypes_explanation_to_supported_performance() -> None:
    payload = {
        "semantic_deltas": [{"occurrence_id": "occ:setting", "evidence_chunk_ids": ["C001"]}],
        "knowledge_map": {
            "knowledge_points": [{"knowledge_id": "kp:setting", "title": "Setting", "source_chunk_ids": ["C001"]}],
            "source_knowledge_points": [{"source_knowledge_point_id": "source:setting", "title": "Setting", "source_chunk_ids": ["C001"]}],
            "mappings": [], "prerequisites": [],
            "planned_occurrences": [{
                "occurrence_id": "occ:setting", "knowledge_id": "kp:setting", "source_knowledge_point_id": "source:setting",
                "chapter_id": "c", "section_id": "s", "position": {"chapter_ordinal": 1, "task_ordinal": 1, "occurrence_ordinal": 1},
                "role": "TEACH", "intended_grants": ["EXPLAIN"], "intended_extension_keys": [], "intended_contribution": "Explain setting.",
                "context_title": "Parameter setting", "source_chunk_ids": ["C001"], "required_prerequisites": [], "trusted_for_state": True,
            }],
        },
    }
    resolution = resolve_evidence_coverage_from_payload(payload=payload, chunks=list(_evidence("Operate and adjust the setting.").values()))
    occurrence = resolution.contracted_payload["knowledge_map"]["planned_occurrences"][0]
    assert resolution.contractions[0].status == "EVIDENCE_BOUNDED_AUTO_CONTRACTION"
    assert occurrence["role"] == "TEACH"
    assert occurrence["intended_grants"] == ["PERFORM"]


def test_recall_capsule_restores_only_verified_teach_context_and_matches_both_targets() -> None:
    teach = _occurrence(occurrence_id="occ:teach", role="TEACH", task=1, grants=["EXPLAIN"])
    recall = _occurrence(occurrence_id="occ:recall", role="RECALL", task=5, grants=[], required=["EXPLAIN"])
    teach_brief = _brief(occurrence_id="occ:teach", role="TEACH", task=1)
    recall_brief = _brief(occurrence_id="occ:recall", role="RECALL", task=5, required=["EXPLAIN"])
    resolution = plan_recall_capsule(
        recall_occurrence=recall, recall_brief=recall_brief, all_occurrences=[teach, recall], briefs=[teach_brief, recall_brief],
        verified_occurrence_ids={"occ:teach"},
    )
    assert resolution.status == "READY"
    assert resolution.plan.source_occurrence_ids == ("occ:teach",)
    markdown, digital = _records(recall_brief)
    result = execute_recall_capsule(
        plan=resolution.plan, brief=recall_brief, markdown_rendered=markdown, digital_book_rendered=digital,
        evidence_by_id=_evidence(), generator=ScriptedRecallGenerator("Recall that the arc source affects the current task."),
    )
    assert result.attempt.status == RepairAttemptStatus.ACCEPTED
    assert result.attempt.post_conformance["markdown"].overall == ConformanceStatus.MATCH
    assert result.markdown_candidate.markdown == result.digital_book_candidate.markdown


def test_recall_capsule_rolls_back_long_teach_new_facet_and_evidence_mismatch() -> None:
    teach = _occurrence(occurrence_id="occ:teach", role="TEACH", task=1, grants=["EXPLAIN"])
    recall = _occurrence(occurrence_id="occ:recall", role="RECALL", task=5, grants=[], required=["EXPLAIN"])
    teach_brief = _brief(occurrence_id="occ:teach", role="TEACH", task=1)
    recall_brief = _brief(occurrence_id="occ:recall", role="RECALL", task=5, required=["EXPLAIN"])
    plan = plan_recall_capsule(
        recall_occurrence=recall, recall_brief=recall_brief, all_occurrences=[teach, recall], briefs=[teach_brief, recall_brief], verified_occurrence_ids={"occ:teach"},
    ).plan
    markdown, digital = _records(recall_brief)
    long = execute_recall_capsule(
        plan=plan, brief=recall_brief, markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(),
        generator=ScriptedRecallGenerator("Recall the arc source. It affects the task. Use it now."),
    )
    assert long.attempt.rollback_reason == "NON_MINIMAL_RECALL_CAPSULE"
    new_facet = execute_recall_capsule(
        plan=plan, brief=recall_brief, markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(),
        generator=ScriptedRecallGenerator("Recall that the arc source affects the task; analyze the source."),
    )
    assert new_facet.attempt.rollback_reason == "RECALL_INTRODUCED_NEW_FACET"
    mismatch = execute_recall_capsule(
        plan=replace(plan, allowed_evidence_ids=("C001", "C999")), brief=recall_brief, markdown_rendered=markdown, digital_book_rendered=digital,
        evidence_by_id=_evidence(), generator=ScriptedRecallGenerator("Recall that the arc source affects the task."),
    )
    assert mismatch.attempt.rollback_reason == "SOURCE_EVIDENCE_MISMATCH"


def test_recall_plan_requires_prior_verified_instructional_source() -> None:
    recall = _occurrence(occurrence_id="occ:recall", role="RECALL", task=5, grants=[], required=["EXPLAIN"])
    brief = _brief(occurrence_id="occ:recall", role="RECALL", task=5, required=["EXPLAIN"])
    resolution = plan_recall_capsule(
        recall_occurrence=recall, recall_brief=brief, all_occurrences=[recall], briefs=[brief], verified_occurrence_ids=set(),
    )
    assert resolution.status == "MANUAL_REVIEW"
    assert resolution.reason == "REQUIRED_FACET_NOT_PREVIOUSLY_VERIFIED"
