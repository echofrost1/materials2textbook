from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from materials2textbook.knowledge_map.repair_proposals import build_repair_proposal_report
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    check_rendered_conformance,
    extract_rendered_occurrences,
    wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.safe_auto_repair import (
    RemovedSpan,
    RepairAttemptStatus,
    execute_synchronized_safe_repair,
    merge_overlapping_spans,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief


def _brief(*, occurrence_id: str = "occ:repair", role: str = "APPLY", avoid: list[str] | None = None, must_teach: list[str] | None = None) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id,
        source_knowledge_point_id="source:repair",
        canonical_knowledge_id="kp:repair",
        source_title="Repair fixture",
        canonical_title="Repair fixture",
        chapter_id="chapter_01",
        section_id="section_01",
        role=role,
        already_available_facets=["EXPLAIN"],
        required_facets=["EXPLAIN"],
        must_teach_facets=must_teach or [],
        must_not_reteach_facets=["EXPLAIN"],
        extension_keys=[],
        repeated_aspects_to_avoid=avoid or [],
        prerequisite_context=["self: EXPLAIN"],
        contribution_goal="Apply the known method in the current task.",
        source_chunk_ids=["C001"],
        writing_contract="immutable fixture contract",
        semantic_delta_evidence_ids=["C001"],
        task_ordinal=1,
        occurrence_ordinal=1,
        allowed_content=["current task application"],
        forbidden_content=avoid or [],
        max_recap_sentences=1,
        must_include_points=[],
        must_avoid_patterns=avoid or [],
    )


def _proposal_and_targets(brief: OccurrenceWritingBrief, body: str):
    markdown = wrap_rendered_occurrence(brief, body)
    [rendered] = extract_rendered_occurrences(markdown)
    digital = replace(rendered, render_target="digital_book")
    conformance = check_rendered_conformance([brief], markdown)
    proposal = build_repair_proposal_report(
        briefs=[brief], rendered_occurrences=[rendered], conformance_results=conformance.results,
    ).proposals[0]
    return proposal, rendered, digital


def test_safe_delete_of_repeated_definition_is_accepted_and_keeps_markdown_digital_aligned() -> None:
    brief = _brief(avoid=["definition"])
    proposal, markdown, digital = _proposal_and_targets(
        brief, "Use the known method in this task. Current is defined as arc current. Evidence: C001",
    )
    original_brief = deepcopy(brief)
    original_markdown = markdown.markdown
    original_digital = digital.markdown
    original_proposal = deepcopy(proposal)

    result = execute_synchronized_safe_repair(
        brief=brief, proposal=proposal, markdown_rendered=markdown, digital_book_rendered=digital,
    )

    attempt = result.attempt
    assert attempt.status == RepairAttemptStatus.ACCEPTED
    assert attempt.executed_actions == ("REMOVE_RETEACH",)
    assert attempt.post_conformance["markdown"].overall == ConformanceStatus.MATCH
    assert attempt.post_conformance["digital_book"].overall == ConformanceStatus.MATCH
    assert result.markdown_candidate.markdown == result.digital_book_candidate.markdown
    assert "Current is defined as arc current." not in result.markdown_candidate.markdown
    assert "Use the known method in this task." in result.markdown_candidate.markdown
    assert attempt.diff and "-Use the known method" in attempt.diff
    assert markdown.markdown == original_markdown
    assert digital.markdown == original_digital
    assert brief == original_brief
    assert proposal == original_proposal


def test_removing_repeated_method_keeps_existing_contribution_and_is_accepted() -> None:
    brief = _brief(avoid=["parameter/method rule"])
    proposal, markdown, digital = _proposal_and_targets(
        brief, "Apply the taught method in the current task. Adjust current according to material thickness.",
    )

    result = execute_synchronized_safe_repair(
        brief=brief, proposal=proposal, markdown_rendered=markdown, digital_book_rendered=digital,
    )

    assert result.attempt.status == RepairAttemptStatus.ACCEPTED
    assert result.attempt.post_conformance["markdown"].contribution_goal_coverage == ConformanceStatus.MATCH
    assert "Adjust current" not in result.attempt.candidate_text


def test_deletion_that_loses_contribution_or_leaves_only_heading_rolls_back() -> None:
    brief = _brief(avoid=["definition"])
    for body, expected_reason in [
        ("Observe the setup. Current is defined as arc current.", "POST_CONFORMANCE_NOT_MATCH:markdown:PARTIAL,digital_book:PARTIAL"),
        ("Current is defined as arc current.", "EMPTY_OR_HEADING_ONLY_AFTER_REMOVAL"),
        ("# Current\nCurrent is defined as arc current.", "EMPTY_OR_HEADING_ONLY_AFTER_REMOVAL"),
    ]:
        proposal, markdown, digital = _proposal_and_targets(brief, body)
        result = execute_synchronized_safe_repair(
            brief=brief, proposal=proposal, markdown_rendered=markdown, digital_book_rendered=digital,
        )
        assert result.attempt.status == RepairAttemptStatus.ROLLED_BACK
        assert result.attempt.rollback_reason == expected_reason
        assert result.markdown_candidate is None
        assert result.attempt.post_conformance["markdown"].overall != ConformanceStatus.MATCH


def test_unsafe_non_remove_proposals_are_skipped_and_anchor_mismatch_rolls_back() -> None:
    unsafe_brief = _brief(role="TEACH", must_teach=["EXPLAIN"])
    proposal, markdown, digital = _proposal_and_targets(unsafe_brief, "Observe the fixture.")
    skipped = execute_synchronized_safe_repair(
        brief=unsafe_brief, proposal=proposal, markdown_rendered=markdown, digital_book_rendered=digital,
    )
    assert skipped.attempt.status == RepairAttemptStatus.SKIPPED
    assert skipped.attempt.executed_actions == ()

    safe_brief = _brief(occurrence_id="occ:anchor", avoid=["definition"])
    proposal, markdown, digital = _proposal_and_targets(safe_brief, "Use the known method. Current is defined as arc current.")
    mismatch = execute_synchronized_safe_repair(
        brief=safe_brief, proposal=proposal, markdown_rendered=markdown,
        digital_book_rendered=replace(digital, section_id="section_other"),
    )
    assert mismatch.attempt.status == RepairAttemptStatus.ROLLED_BACK
    assert mismatch.attempt.rollback_reason == "ANCHOR_MISMATCH_OR_MISSING_TARGET"


def test_overlapping_checker_spans_merge_stably() -> None:
    merged = merge_overlapping_spans([
        RemovedSpan(8, 18, "first", ("v1",)),
        RemovedSpan(14, 24, "second", ("v2",)),
        RemovedSpan(24, 30, "third", ("v3",)),
    ])

    assert [(item.start_offset, item.end_offset, item.violation_ids) for item in merged] == [
        (8, 30, ("v1", "v2", "v3")),
    ]
