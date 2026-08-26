from materials2textbook.knowledge_map.availability import InstructionalAvailabilityState
from materials2textbook.knowledge_map.semantic_evaluation import compile_occurrence_for_verified_availability

from test_root_cause_fixes import _delta, _occurrence


def test_preview_compilation_keeps_strict_block_as_audit_but_allows_readable_rendering() -> None:
    occurrence = _occurrence("preview-apply", ordinal=2)
    delta = _delta("preview-apply", new_facets=[], uses_prior=True)

    strict = compile_occurrence_for_verified_availability(
        seed=occurrence,
        delta=delta,
        verified_before=InstructionalAvailabilityState(),
        has_previous=True,
        source_context="apply: current task",
        first_position={occurrence.knowledge_id: occurrence.position},
    )
    assert strict.executable is False
    assert strict.issue_code == "PRIOR_TEACHING_NOT_VERIFIED"

    preview = compile_occurrence_for_verified_availability(
        seed=occurrence,
        delta=delta,
        verified_before=InstructionalAvailabilityState(),
        has_previous=True,
        source_context="apply: current task",
        first_position={occurrence.knowledge_id: occurrence.position},
        preview_mode=True,
    )
    assert preview.executable is True
    assert preview.compiled_occurrence is not None
    assert any(item["classification"] == "PREVIEW_STRICT_RUNTIME_BLOCK_RETAINED" for item in preview.audit)
