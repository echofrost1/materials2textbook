from __future__ import annotations

from dataclasses import replace

import pytest

from materials2textbook.knowledge_map.availability import advance_verified_instructional_availability
from materials2textbook.knowledge_map.models import (
    BookPosition,
    InstructionalAvailabilityState,
    KnowledgeKind,
    KnowledgePoint,
    LearningRole,
    MasteryFacet,
    OccurrenceExecutionResult,
    PlannedOccurrence,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.semantic_evaluation import compile_occurrence_for_verified_availability
from materials2textbook.knowledge_map.writing_briefs import build_verified_occurrence_writing_brief


def _occurrence(occurrence_id: str, ordinal: int) -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id=occurrence_id,
        knowledge_id="kp:generic-method",
        source_knowledge_point_id=f"source:{occurrence_id}",
        position=BookPosition(ordinal, ordinal, ordinal),
        chapter_id=f"chapter_{ordinal:02d}",
        section_id=f"section_{ordinal:02d}",
        context_title="generic task",
        source_chunk_ids=[f"chunk:{occurrence_id}"],
        role=LearningRole.TEACH,
        trusted_for_state=True,
    )


def _delta(
    occurrence_id: str,
    *,
    new_facets: list[str],
    uses_prior_knowledge: bool = False,
    new_extension_keys: list[str] | None = None,
    repeats_prior_explanation: bool = False,
    repeats_complete_teaching: bool = False,
) -> SemanticDelta:
    return SemanticDelta(
        occurrence_id=occurrence_id,
        repeats_prior_explanation=repeats_prior_explanation,
        uses_prior_knowledge=uses_prior_knowledge,
        recall_needed=False,
        required_self_facets=[],
        required_self_extension_keys=[],
        cross_prerequisite_uses=[],
        new_facets=new_facets,
        new_extension_keys=list(new_extension_keys or []),
        new_context="current generic task" if uses_prior_knowledge else "",
        repeated_aspects=[],
        contribution_summary="apply the already taught method" if uses_prior_knowledge else "teach the method",
        confidence=0.95,
        rationale="domain-neutral runtime availability fixture",
        evidence_chunk_ids=[f"chunk:{occurrence_id}"],
        repeats_complete_teaching=repeats_complete_teaching,
    )


def _source(occurrence: PlannedOccurrence) -> SourceKnowledgePoint:
    return SourceKnowledgePoint(
        source_knowledge_point_id=occurrence.source_knowledge_point_id,
        title="generic method",
        chapter_id=occurrence.chapter_id,
        section_id=occurrence.section_id,
        chapter_ordinal=occurrence.position.chapter_ordinal,
        section_ordinal=0,
        task_ordinal=occurrence.position.task_ordinal,
        source_point_ordinal=0,
        context_title=occurrence.context_title,
        source_chunk_ids=list(occurrence.source_chunk_ids),
    )


def _point() -> KnowledgePoint:
    return KnowledgePoint(
        knowledge_id="kp:generic-method",
        title="generic method",
        aliases=[],
        kind=KnowledgeKind.METHOD,
        source_chunk_ids=["chunk:a1", "chunk:a2"],
        extraction_confidence=1.0,
    )


def test_verified_rendered_teaching_establishes_runtime_availability_for_later_apply() -> None:
    a1 = _occurrence("a1", 1)
    a1_compile = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=_delta("a1", new_facets=[MasteryFacet.EXPLAIN]),
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="TEACH: introduce the generic method",
    )
    assert a1_compile.executable is True
    assert a1_compile.compiled_occurrence is not None
    assert a1_compile.compiled_occurrence.role == LearningRole.TEACH
    assert a1_compile.compiled_occurrence.intended_grants == [MasteryFacet.EXPLAIN]

    state = InstructionalAvailabilityState()
    # A planned intended grant alone has not changed runtime availability.
    assert state.availability_by_knowledge == {}
    transition = advance_verified_instructional_availability(
        state=state,
        occurrence=a1_compile.compiled_occurrence,
        execution=OccurrenceExecutionResult(
            occurrence_id="a1",
            rendered_span_id="markdown:a1:body",
            rendered_body="The generic method is explained for the current task.",
            conformance_status="MATCH",
            evidence_status="SUPPORTED",
            conformance_verified_facets=(MasteryFacet.EXPLAIN,),
            evidence_supported_facets=(MasteryFacet.EXPLAIN,),
        ),
    )
    assert transition.grant_applied is True
    record = state.availability_by_knowledge["kp:generic-method"]
    assert record.available_facets == [MasteryFacet.EXPLAIN]
    assert record.facet_source_occurrence_ids == {MasteryFacet.EXPLAIN: "a1"}

    a2 = _occurrence("a2", 2)
    a2_compile = compile_occurrence_for_verified_availability(
        seed=a2,
        delta=_delta("a2", new_facets=[], uses_prior_knowledge=True),
        verified_before=state,
        has_previous=True,
        source_context="APPLY: use the generic method in this task",
    )
    assert a2_compile.executable is True
    assert a2_compile.compiled_occurrence is not None
    assert a2_compile.compiled_occurrence.role == LearningRole.APPLY
    brief = build_verified_occurrence_writing_brief(
        occurrence=a2_compile.compiled_occurrence,
        delta=a2_compile.effective_delta,
        source=_source(a2),
        point=_point(),
        verified_before=a2_compile.before,
    )
    assert brief.already_available_facets == [MasteryFacet.EXPLAIN]
    assert brief.availability_source_occurrence_ids == ["a1"]


@pytest.mark.parametrize(
    ("rendered_span_id", "rendered_body", "conformance_status", "evidence_status", "expected_blocker"),
    [
        (None, "The generic method is explained.", "MATCH", "SUPPORTED", "no_rendered_student_visible_span"),
        ("markdown:a1:body", "The generic method is explained.", "VIOLATION", "SUPPORTED", "local_conformance_not_passed:VIOLATION"),
        ("markdown:a1:body", "The generic method is explained.", "MATCH", "UNSUPPORTED", "local_evidence_not_passed:UNSUPPORTED"),
        ("markdown:a1:body", "", "MATCH", "SUPPORTED", "rendered_student_visible_body_empty"),
    ],
)
def test_failed_rendered_teaching_never_establishes_runtime_availability_or_apply(
    rendered_span_id: str | None,
    rendered_body: str,
    conformance_status: str,
    evidence_status: str,
    expected_blocker: str,
) -> None:
    a1 = _occurrence("a1", 1)
    a1_compile = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=_delta("a1", new_facets=[MasteryFacet.EXPLAIN]),
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="TEACH: introduce the generic method",
    )
    assert a1_compile.compiled_occurrence is not None
    state = InstructionalAvailabilityState()
    failed = advance_verified_instructional_availability(
        state=state,
        occurrence=a1_compile.compiled_occurrence,
        execution=OccurrenceExecutionResult(
            occurrence_id="a1",
            rendered_span_id=rendered_span_id,
            rendered_body=rendered_body,
            conformance_status=conformance_status,
            evidence_status=evidence_status,
            conformance_verified_facets=(MasteryFacet.EXPLAIN,),
            evidence_supported_facets=(MasteryFacet.EXPLAIN,),
        ),
    )
    assert failed.grant_applied is False
    assert expected_blocker in failed.blocked_reasons
    assert state.availability_by_knowledge == {}

    a2 = _occurrence("a2", 2)
    a2_compile = compile_occurrence_for_verified_availability(
        seed=a2,
        delta=_delta("a2", new_facets=[], uses_prior_knowledge=True),
        verified_before=state,
        has_previous=True,
        source_context="APPLY: use the generic method in this task",
    )
    assert a2_compile.executable is False
    assert a2_compile.compiled_occurrence is None
    assert a2_compile.issue_code == "PRIOR_TEACHING_NOT_VERIFIED"
    assert a2_compile.issue_details


def test_duplicate_complete_teaching_becomes_apply_after_verified_prior_support() -> None:
    a1 = _occurrence("a1", 1)
    a1_compile = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=_delta("a1", new_facets=[MasteryFacet.EXPLAIN]),
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="TEACH: introduce the generic method",
    )
    state = InstructionalAvailabilityState()
    advance_verified_instructional_availability(
        state=state,
        occurrence=a1_compile.compiled_occurrence,
        execution=OccurrenceExecutionResult(
            occurrence_id="a1",
            rendered_span_id="markdown:a1:body",
            rendered_body="The generic method is explained for the current task.",
            conformance_status="MATCH",
            evidence_status="SUPPORTED",
            conformance_verified_facets=(MasteryFacet.EXPLAIN,),
            evidence_supported_facets=(MasteryFacet.EXPLAIN,),
        ),
    )
    a2 = _occurrence("a2", 2)
    a2_compile = compile_occurrence_for_verified_availability(
        seed=a2,
        delta=_delta(
            "a2",
            new_facets=[],
            uses_prior_knowledge=True,
            repeats_prior_explanation=True,
            repeats_complete_teaching=True,
        ),
        verified_before=state,
        has_previous=True,
        source_context="APPLY: repeat the complete definition and method",
    )
    assert a2_compile.executable is True
    assert a2_compile.compiled_occurrence is not None
    assert a2_compile.compiled_occurrence.role == LearningRole.APPLY
    assert a2_compile.compiled_occurrence.intended_grants == []
    brief = build_verified_occurrence_writing_brief(
        occurrence=a2_compile.compiled_occurrence,
        delta=a2_compile.effective_delta,
        source=_source(a2),
        point=_point(),
        verified_before=a2_compile.before,
    )
    assert MasteryFacet.EXPLAIN in brief.must_not_reteach_facets
    assert brief.availability_source_occurrence_ids == ["a1"]


def test_duplicate_signal_cannot_erase_a_real_extension() -> None:
    a1 = _occurrence("a1", 1)
    a1_compile = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=_delta("a1", new_facets=[MasteryFacet.EXPLAIN]),
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="TEACH: introduce the generic method",
    )
    state = InstructionalAvailabilityState()
    advance_verified_instructional_availability(
        state=state,
        occurrence=a1_compile.compiled_occurrence,
        execution=OccurrenceExecutionResult(
            occurrence_id="a1",
            rendered_span_id="markdown:a1:body",
            rendered_body="The generic method is explained for the current task.",
            conformance_status="MATCH",
            evidence_status="SUPPORTED",
            conformance_verified_facets=(MasteryFacet.EXPLAIN,),
            evidence_supported_facets=(MasteryFacet.EXPLAIN,),
        ),
    )
    a2 = _occurrence("a2", 2)
    a2_compile = compile_occurrence_for_verified_availability(
        seed=a2,
        delta=_delta(
            "a2",
            new_facets=[],
            uses_prior_knowledge=True,
            new_extension_keys=["constraint:variant"],
            repeats_prior_explanation=True,
            repeats_complete_teaching=True,
        ),
        verified_before=state,
        has_previous=True,
        source_context="EXTEND: add a constrained variant",
    )
    assert a2_compile.executable is True
    assert a2_compile.compiled_occurrence is not None
    assert a2_compile.compiled_occurrence.role == LearningRole.EXTEND
    assert a2_compile.compiled_occurrence.intended_extension_keys == ["constraint:variant"]


def test_duplicate_apply_requires_verified_prior_support() -> None:
    a2 = _occurrence("a2", 2)
    a2_compile = compile_occurrence_for_verified_availability(
        seed=a2,
        delta=_delta(
            "a2",
            new_facets=[],
            uses_prior_knowledge=True,
            repeats_prior_explanation=True,
            repeats_complete_teaching=True,
        ),
        verified_before=InstructionalAvailabilityState(),
        has_previous=True,
        source_context="APPLY: use the already taught method",
    )
    assert a2_compile.executable is False
    assert a2_compile.compiled_occurrence is None
    assert a2_compile.issue_code == "PRIOR_TEACHING_NOT_VERIFIED"


def test_phase3a0_valid_extend_preserves_new_facet_and_extension() -> None:
    """A verified base facet plus a real increment must remain EXTEND."""
    a1 = _occurrence("a1", 1)
    a1_compile = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=_delta("a1", new_facets=[MasteryFacet.EXPLAIN]),
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="TEACH: establish the generic method",
    )
    assert a1_compile.compiled_occurrence is not None
    state = InstructionalAvailabilityState()
    first = advance_verified_instructional_availability(
        state=state,
        occurrence=a1_compile.compiled_occurrence,
        execution=OccurrenceExecutionResult(
            occurrence_id="a1",
            rendered_span_id="markdown:a1:body",
            rendered_body="The method is explained.",
            conformance_status="MATCH",
            evidence_status="SUPPORTED",
            conformance_verified_facets=(MasteryFacet.EXPLAIN,),
            evidence_supported_facets=(MasteryFacet.EXPLAIN,),
        ),
    )
    assert first.grant_applied is True

    a2 = _occurrence("a2", 2)
    a2_compile = compile_occurrence_for_verified_availability(
        seed=a2,
        delta=_delta(
            "a2",
            new_facets=[MasteryFacet.ANALYZE],
            uses_prior_knowledge=True,
            new_extension_keys=["constraint:variant"],
        ),
        verified_before=state,
        has_previous=True,
        source_context="EXTEND: analyze a constrained variant",
    )
    assert a2_compile.executable is True
    assert a2_compile.compiled_occurrence is not None
    assert a2_compile.compiled_occurrence.role == LearningRole.EXTEND
    assert a2_compile.compiled_occurrence.intended_grants == [MasteryFacet.ANALYZE]
    assert a2_compile.compiled_occurrence.intended_extension_keys == ["constraint:variant"]
    second = advance_verified_instructional_availability(
        state=state,
        occurrence=a2_compile.compiled_occurrence,
        execution=OccurrenceExecutionResult(
            occurrence_id="a2",
            rendered_span_id="markdown:a2:body",
            rendered_body="The constrained variant is analyzed.",
            conformance_status="MATCH",
            evidence_status="SUPPORTED",
            conformance_verified_facets=(MasteryFacet.ANALYZE,),
            conformance_verified_extension_keys=("constraint:variant",),
            evidence_supported_facets=(MasteryFacet.ANALYZE,),
            evidence_supported_extension_keys=("constraint:variant",),
        ),
    )
    assert second.grant_applied is True
    record = state.availability_by_knowledge["kp:generic-method"]
    assert record.available_extension_keys == ["constraint:variant"]
    assert record.extension_source_occurrence_ids == {"constraint:variant": "a2"}


def test_phase3a0_explicit_explain_is_not_collapsed_to_intro() -> None:
    a1 = _occurrence("a1", 1)
    compilation = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=_delta("a1", new_facets=[MasteryFacet.EXPLAIN]),
        verified_before=InstructionalAvailabilityState(),
        has_previous=False,
        source_context="introductory context, but explain the method",
    )
    assert compilation.executable is True
    assert compilation.compiled_occurrence is not None
    assert compilation.compiled_occurrence.role == LearningRole.TEACH
    assert compilation.compiled_occurrence.intended_grants == [MasteryFacet.EXPLAIN]


def test_phase3a0_insufficient_verified_state_still_blocks_extend() -> None:
    a1 = _occurrence("a1", 1)
    delta = replace(
        _delta(
            "a1",
            new_facets=[MasteryFacet.ANALYZE],
            uses_prior_knowledge=True,
            new_extension_keys=["constraint:variant"],
        ),
        required_self_facets=[MasteryFacet.EXPLAIN],
    )
    compilation = compile_occurrence_for_verified_availability(
        seed=a1,
        delta=delta,
        verified_before=InstructionalAvailabilityState(),
        has_previous=True,
        source_context="EXTEND: constrained variant",
    )
    assert compilation.executable is False
    assert compilation.compiled_occurrence is None
    assert compilation.issue_code in {"SELF_REQUIREMENT_NOT_VERIFIED", "PRIOR_TEACHING_NOT_VERIFIED"}
