from __future__ import annotations

import json
from pathlib import Path

from materials2textbook.knowledge_map.repair_proposals import (
    RepairAction,
    RepairExecutionSafety,
    build_repair_proposal_report,
    render_repair_proposal_report_markdown,
)
from materials2textbook.knowledge_map.rendered_conformance import (
    check_rendered_conformance,
    extract_rendered_occurrences,
    wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief


def _brief(case: dict) -> OccurrenceWritingBrief:
    occurrence_id = f"occ:phase3a:{case['id']}"
    available = case.get("available") or []
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id,
        source_knowledge_point_id=f"source:{case['trajectory_id']}",
        canonical_knowledge_id=f"kp:{case['trajectory_id']}",
        source_title=case["trajectory_id"],
        canonical_title=case["trajectory_id"],
        chapter_id="chapter_01",
        section_id="section_01",
        role=case["role"],
        already_available_facets=available,
        required_facets=available,
        must_teach_facets=case.get("must_teach") or [],
        must_not_reteach_facets=available,
        extension_keys=case.get("extensions") or [],
        repeated_aspects_to_avoid=case.get("avoid") or [],
        prerequisite_context=[f"self: {', '.join(available)}"] if available else [],
        contribution_goal=f"Contribution for {case['trajectory_id']}",
        source_chunk_ids=[f"C{case['id'][-1]}"],
        writing_contract=f"immutable {case['role']} contract",
        semantic_delta_evidence_ids=[f"D{case['id'][-1]}"],
        task_ordinal=1,
        occurrence_ordinal=1,
        allowed_content=["evidence-grounded task content"],
        forbidden_content=case.get("avoid") or [],
        max_recap_sentences=case.get("recap_limit", 1),
        must_include_points=[],
        must_avoid_patterns=case.get("avoid") or [],
    )


def test_phase3a_injected_gold_violations_produce_expected_read_only_proposals() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "knowledge_map_gold" / "phase3a_repair_proposals.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]
    proposals = {}
    for case in cases:
        brief = _brief(case)
        markdown = "" if not case.get("anchor", True) else wrap_rendered_occurrence(brief, case["body"])
        conformance = check_rendered_conformance([brief], markdown)
        rendered = extract_rendered_occurrences(markdown)
        report = build_repair_proposal_report(
            briefs=[brief], rendered_occurrences=rendered, conformance_results=conformance.results,
        )
        assert len(report.proposals) == 1
        proposals[case["id"]] = report.proposals[0]

    for case in cases:
        proposal = proposals[case["id"]]
        assert list(proposal.actions) == case["expected_actions"]
        assert proposal.immutable_upstream.canonical_knowledge_id == f"kp:{case['trajectory_id']}"
        assert proposal.immutable_upstream.learning_role == case["role"]
        assert proposal.evidence_source_ids
        assert proposal.expected_conformance.forbidden_reteach_absent is True

    assert proposals["high_frequency_arc_intro_missing_anchor"].execution_safety == RepairExecutionSafety.HUMAN_REVIEW_REQUIRED
    assert proposals["thin_plate_extend_missing_extension"].actions == (RepairAction.ADD_REQUIRED_FACET, RepairAction.ADD_EXTENSION)
    assert RepairAction.RESTORE_MINIMAL_RECALL in proposals["arc_decay_recall_reteaches"].actions
    assert RepairAction.ADD_REQUIRED_FACET in proposals["related_distinct_teach_missing_facet"].actions
    assert RepairAction.ADD_CONTRIBUTION in proposals["high_frequency_alias_apply_missing_contribution"].actions


def test_phase3a_only_allows_exact_reteach_removal_to_become_an_automation_candidate() -> None:
    case = {
        "id": "safe_exact_removal", "trajectory_id": "safe", "role": "APPLY", "available": ["EXPLAIN"],
        "avoid": ["definition"], "body": "Use the taught method in this task. Current is defined as arc current.",
    }
    brief = _brief(case)
    markdown = wrap_rendered_occurrence(brief, case["body"])
    conformance = check_rendered_conformance([brief], markdown)
    proposal = build_repair_proposal_report(
        briefs=[brief], rendered_occurrences=extract_rendered_occurrences(markdown), conformance_results=conformance.results,
    ).proposals[0]

    # Because the existing first sentence already provides the APPLY
    # contribution, removing the checker-cited definition is the only action.
    assert proposal.actions == (RepairAction.REMOVE_RETEACH,)
    assert proposal.execution_safety == RepairExecutionSafety.AUTO_CANDIDATE
    assert "Current is defined as arc current." in proposal.content_to_remove_or_compress


def test_repair_report_states_that_no_proposal_is_applied() -> None:
    case = {"id": "report", "trajectory_id": "report", "role": "APPLY", "available": ["EXPLAIN"], "body": "Observe only."}
    brief = _brief(case)
    report = build_repair_proposal_report(
        briefs=[brief],
        rendered_occurrences=extract_rendered_occurrences(wrap_rendered_occurrence(brief, case["body"])),
        conformance_results=check_rendered_conformance([brief], wrap_rendered_occurrence(brief, case["body"])).results,
    )
    markdown = render_repair_proposal_report_markdown(report)

    assert "read-only" in markdown
    assert "no proposal has been applied" in markdown
