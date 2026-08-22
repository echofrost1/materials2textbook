from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from materials2textbook.io_utils import write_jsonl
from materials2textbook.knowledge_map.execution import execute_verified_occurrences
from materials2textbook.knowledge_map.models import (
    BookPosition,
    KnowledgeKind,
    KnowledgePoint,
    LearningRole,
    PlannedOccurrence,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.rendered_conformance import wrap_rendered_occurrence
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.orchestrator import TextbookWorkflow


class _AlwaysSupportedJudge:
    model = "fixture-entailment"

    def __init__(self) -> None:
        self.call_count = 0

    def judge(self, *, claim: str, evidence: list[dict[str, str]], context: dict[str, str]) -> dict:
        self.call_count += 1
        return {
            "status": "SUPPORTED",
            "supporting_evidence_ids": [item["evidence_id"] for item in evidence[:1]],
            "rationale": "fixture evidence supports the claim",
            "confidence": 1.0,
        }


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="C1",
        asset_id="asset-1",
        title="generic method",
        content="The generic method is defined and explained for the current task.",
        summary="The generic method is defined and explained for the current task.",
        keywords=["generic method"],
        subject="fixture",
        material_block="fixture",
        material_block_code="fixture",
        recommended_chapter="generic",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=1.0, confidence=1.0),
        source_type="document_segment",
        review_status="approved",
    )


def _occurrence(occurrence_id: str, ordinal: int, context: str) -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id=occurrence_id,
        knowledge_id="kp:generic-method",
        source_knowledge_point_id="source:generic-method",
        position=BookPosition(1, ordinal, ordinal),
        chapter_id="chapter_01",
        section_id=f"section_{ordinal:02d}",
        context_title=context,
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
        contribution_summary="apply the generic method" if uses_prior else "teach the generic method",
        confidence=0.95,
        rationale="domain-neutral Phase 1C production regression",
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
        context_title="generic method",
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


def _run_pair(first_body: str, *, second_body: str = "Apply the generic method in the current task. Evidence: C1"):
    a1 = _occurrence("A1", 1, "TEACH: introduce generic method")
    a2 = _occurrence("A2", 2, "APPLY: use generic method in current task")
    calls: list[str] = []

    def render(brief):
        calls.append(brief.occurrence_id)
        body = first_body if brief.occurrence_id == "A1" else second_body
        return wrap_rendered_occurrence(brief, body)

    return execute_verified_occurrences(
        occurrences=[a1, a2],
        deltas=[
            _delta("A1", new_facets=["EXPLAIN"]),
            _delta("A2", new_facets=[], uses_prior=True),
        ],
        sources={"source:generic-method": _source()},
        points={"kp:generic-method": _point()},
        chunks=[_chunk()],
        render_occurrence=render,
        semantic_entailment_judge=_AlwaysSupportedJudge(),
    ), calls


def test_phase1c_case_f_production_execution_provenance() -> None:
    result, calls = _run_pair("The generic method is defined and explained. Evidence: C1")
    assert calls == ["A1", "A2"]
    assert result.transitions[0]["grant_applied"] is True
    assert result.transitions[0]["granted_facets"] == ["EXPLAIN"]
    assert result.transitions[1]["grant_applied"] is True
    assert result.transitions[1]["granted_facets"] == []
    assert result.coverage.briefs[-1].role == LearningRole.APPLY
    assert result.coverage.briefs[-1].availability_source_occurrence_ids == ["A1"]


def test_phase1c_case_g_failed_teach_blocks_later_apply() -> None:
    result, calls = _run_pair("")
    assert calls == ["A1"]
    assert result.transitions[0]["grant_applied"] is False
    assert result.verified_state.availability_by_knowledge == {}
    assert any(item["issue_code"] == "PRIOR_TEACHING_NOT_VERIFIED" for item in result.blocked_occurrences)
    assert all(item["occurrence_id"] != "A2" for item in result.markdown_occurrences)


def test_phase1c_case_h_explicit_zero_render_skips_writer_and_grant() -> None:
    a1 = _occurrence("A1", 1, "TEACH: introduce generic method")
    a2 = _occurrence("A2", 2, "APPLY: no current use")
    calls: list[str] = []

    def render(brief):
        calls.append(brief.occurrence_id)
        return wrap_rendered_occurrence(brief, "The generic method is defined and explained. Evidence: C1")

    result = execute_verified_occurrences(
        occurrences=[a1, a2],
        deltas=[
            _delta("A1", new_facets=["EXPLAIN"]),
            replace(_delta("A2", new_facets=[]), contribution_summary=""),
        ],
        sources={"source:generic-method": _source()},
        points={"kp:generic-method": _point()},
        chunks=[_chunk()],
        render_occurrence=render,
        semantic_entailment_judge=_AlwaysSupportedJudge(),
    )
    # A2 has no new contribution, no task use, and no recall; runtime keeps
    # the occurrence audit but does not call the writer or grant state.
    assert calls == ["A1"]
    assert [item.occurrence_id for item in result.coverage.zero_render_occurrences] == ["A2"]
    assert "A2" not in result.verified_state.availability_by_knowledge["kp:generic-method"].facet_source_occurrence_ids.values()


def test_phase1c_cases_e_and_i_real_workflow_needs_no_external_semantic_json(tmp_path: Path) -> None:
    video_path = tmp_path / "videos.jsonl"
    document_path = tmp_path / "documents.jsonl"
    output_dir = tmp_path / "semantic_output"
    write_jsonl(video_path, [])
    write_jsonl(document_path, [{
        "segment_id": "D1",
        "asset_id": "A1",
        "title": "generic method",
        "knowledge_point": "generic method",
        "evidence_text": "The generic method is defined and explained for the current task.",
        "summary": "The generic method is defined and explained for the current task.",
        "recommended_chapter": "generic chapter",
        "material_block": "generic block",
        "source_type": "document_segment",
        "review_status": "approved",
        "teaching_value": 0.9,
    }])

    outputs = TextbookWorkflow(use_llm=False).run(
        video_segments_path=video_path,
        document_segments_path=document_path,
        output_dir=output_dir,
        title="Generic textbook",
        config=WorkflowConfig(copy_media_assets=False, review_rounds=0),
        book_mode=True,
        semantic_book_mode=True,
        resume_chapters=False,
    )
    manifest = json.loads(Path(outputs.manifest_path).read_text(encoding="utf-8"))
    assert Path(outputs.semantic_execution_path).exists()
    assert not manifest["input"].get("semantic_evaluation_input")
    assert manifest["source_book_plan_invariant"]["deep_equal"] is True
    assert manifest["source_book_plan_invariant"]["source_outline_signature"] == manifest["source_book_plan_invariant"]["final_outline_signature"]
    assert Path(outputs.materialization_audit_path).exists()
    assert Path(outputs.downstream_closure_path).exists()
    assert Path(outputs.downstream_closure_markdown_path).exists()
    assert manifest["semantic_execution_mode"] == "verified_sequential"
    assert manifest["semantic_planner_mode"] == "deterministic"
    assert manifest["input"]["semantic_book_mode"] is True
    assert manifest["input"]["book_plan_is_frozen"] is False
    for key in (
        "knowledge_map_json",
        "learning_trajectory_report_markdown",
        "canonical_mapping_audit_markdown",
        "semantic_planning_evaluation_json",
        "semantic_learning_trajectory_report_markdown",
        "evidence_coverage_resolution_json",
        "planning_evidence_gate_markdown",
        "rendered_claim_evidence_audit_json",
        "rendered_claim_evidence_audit_markdown",
    ):
        assert manifest["outputs"][key]
        assert Path(manifest["outputs"][key]).exists()
    assert manifest["rendered_claim_evidence_audit"]["summary"]["semantic_routing_categories"]
    materialization = json.loads(Path(outputs.materialization_audit_path).read_text(encoding="utf-8"))
    assert materialization["publication_gate"]["downstream_closure_complete"] is True


def test_frozen_book_plan_requires_explicit_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="book_plan_is_frozen requires book_plan_input"):
        TextbookWorkflow(use_llm=False).run(
            video_segments_path=tmp_path / "missing.jsonl",
            output_dir=tmp_path / "output",
            title="Generic textbook",
            book_mode=True,
            semantic_book_mode=True,
            book_plan_is_frozen=True,
        )
