from __future__ import annotations

from dataclasses import replace

from materials2textbook.knowledge_map.controlled_generative_repair import (
    GeneratedRepairDraft,
    InsertionStrategy,
    execute_controlled_generative_repair,
    execute_controlled_repair_sequence,
)
from materials2textbook.knowledge_map.repair_proposals import RepairAction, build_repair_proposal_report
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    extract_rendered_occurrences,
    check_rendered_conformance,
    wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.safe_auto_repair import RepairAttemptStatus
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


class ScriptedGenerator:
    model_id = "fixture-controlled-generator"
    prompt_version = "controlled-repair.fixture.v1"

    def __init__(self, text: str, *, ids: tuple[str, ...] = ("C001",), terms: tuple[str, ...] = ("method",)) -> None:
        self.text = text
        self.ids = ids
        self.terms = terms
        self.calls: list[tuple[str, str, str]] = []

    def generate(self, *, repair_action, target_gap, insertion_strategy, **kwargs) -> GeneratedRepairDraft:
        self.calls.append((repair_action, target_gap, insertion_strategy))
        return GeneratedRepairDraft(self.text, self.ids, self.terms, self.model_id, self.prompt_version)


def _brief(*, role: str, must_teach: list[str] | None = None, extensions: list[str] | None = None, avoid: list[str] | None = None) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id="occ:controlled",
        source_knowledge_point_id="source:controlled",
        canonical_knowledge_id="kp:controlled",
        source_title="Controlled fixture",
        canonical_title="Controlled fixture",
        chapter_id="chapter_01",
        section_id="section_01",
        role=role,
        already_available_facets=["EXPLAIN"] if role != "TEACH" else [],
        required_facets=[],
        must_teach_facets=must_teach or [],
        must_not_reteach_facets=["EXPLAIN"] if role != "TEACH" else [],
        extension_keys=extensions or [],
        repeated_aspects_to_avoid=avoid or [],
        prerequisite_context=[],
        contribution_goal="Use the known method in the current task.",
        source_chunk_ids=["C001"],
        writing_contract=f"immutable {role} contract",
        semantic_delta_evidence_ids=[],
        task_ordinal=1,
        occurrence_ordinal=1,
        allowed_content=["evidence-grounded task content"],
        forbidden_content=avoid or [],
        max_recap_sentences=1,
        must_include_points=[],
        must_avoid_patterns=avoid or [],
    )


def _evidence() -> dict[str, EvidenceChunk]:
    chunk = EvidenceChunk(
        "C001", "A1", "Arc source method", "The arc source method uses a thin plate current limit in the current task.",
        "The arc source method uses a thin plate current limit in the current task.", [], "approved", "fixture", "fixture", "fixture",
        EvidenceLocator(), EvidenceScore(),
    )
    return {chunk.chunk_id: chunk}


def _proposal_and_records(brief: OccurrenceWritingBrief, body: str):
    markdown = wrap_rendered_occurrence(brief, body)
    [record] = extract_rendered_occurrences(markdown)
    conformance = check_rendered_conformance([brief], markdown)
    proposal = build_repair_proposal_report(
        briefs=[brief], rendered_occurrences=[record], conformance_results=conformance.results,
    ).proposals[0]
    return proposal, record, replace(record, render_target="digital_book")


def test_missing_required_facet_gets_one_evidence_grounded_minimal_patch() -> None:
    brief = _brief(role="TEACH", must_teach=["EXPLAIN"])
    proposal, markdown, digital = _proposal_and_records(brief, "Observe the arc source.")
    generator = ScriptedGenerator("The arc source method explains the controlled source.", terms=("arc source", "method"))

    result = execute_controlled_generative_repair(
        brief=brief, proposal=proposal, repair_action=RepairAction.ADD_REQUIRED_FACET, target_gap="EXPLAIN",
        markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(), generator=generator,
    )

    patch = result.patch
    assert patch.status == RepairAttemptStatus.ACCEPTED
    assert patch.insertion_strategy == InsertionStrategy.APPEND_TO_BODY
    assert patch.post_conformance["markdown"].overall == ConformanceStatus.MATCH
    assert patch.post_conformance["digital_book"].overall == ConformanceStatus.MATCH
    assert result.markdown_candidate.markdown == result.digital_book_candidate.markdown
    assert generator.calls == [(RepairAction.ADD_REQUIRED_FACET, "EXPLAIN", InsertionStrategy.APPEND_TO_BODY)]


def test_extend_patch_adds_only_new_constraint_and_apply_patch_adds_task_contribution() -> None:
    extend = _brief(role="EXTEND", extensions=["constraint:thin_plate_current_limit"], avoid=["definition"])
    proposal, markdown, digital = _proposal_and_records(extend, "Use the known method in this task.")
    extension_result = execute_controlled_generative_repair(
        brief=extend, proposal=proposal, repair_action=RepairAction.ADD_EXTENSION,
        target_gap="constraint:thin_plate_current_limit", markdown_rendered=markdown, digital_book_rendered=digital,
        evidence_by_id=_evidence(), generator=ScriptedGenerator("For thin plate work, use a current limit.", terms=("thin plate", "current limit")),
    )
    assert extension_result.patch.status == RepairAttemptStatus.ACCEPTED
    assert "defined as" not in extension_result.patch.generated_text

    apply = _brief(role="APPLY", avoid=["definition"])
    proposal, markdown, digital = _proposal_and_records(apply, "Observe the arc source.")
    contribution_result = execute_controlled_generative_repair(
        brief=apply, proposal=proposal, repair_action=RepairAction.ADD_CONTRIBUTION, target_gap="contribution",
        markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(),
        generator=ScriptedGenerator("Use the known method in the current task.", terms=("method", "current task")),
    )
    assert contribution_result.patch.status == RepairAttemptStatus.ACCEPTED
    assert contribution_result.patch.post_conformance["markdown"].contribution_goal_coverage == ConformanceStatus.MATCH


def test_reteach_or_other_conformance_regression_rolls_back_even_when_target_is_filled() -> None:
    brief = _brief(role="EXTEND", extensions=["constraint:thin_plate_current_limit"], avoid=["definition"])
    proposal, markdown, digital = _proposal_and_records(brief, "Use the known method in this task.")
    result = execute_controlled_generative_repair(
        brief=brief, proposal=proposal, repair_action=RepairAction.ADD_EXTENSION,
        target_gap="constraint:thin_plate_current_limit", markdown_rendered=markdown, digital_book_rendered=digital,
        evidence_by_id=_evidence(),
        generator=ScriptedGenerator(
            "For thin plate work, use a current limit while the arc source is defined as a method.",
            terms=("thin plate", "current limit", "arc source", "method"),
        ),
    )

    assert result.patch.status == RepairAttemptStatus.ROLLED_BACK
    assert result.patch.rollback_reason.startswith("POST_CONFORMANCE_NOT_MATCH")
    assert result.markdown_candidate is None
    assert result.patch.post_conformance["markdown"].forbidden_reteach_violation


def test_unauthorized_or_unsupported_patch_never_executes() -> None:
    brief = _brief(role="APPLY", avoid=["definition"])
    proposal, markdown, digital = _proposal_and_records(brief, "Observe the arc source.")
    unauthorized = execute_controlled_generative_repair(
        brief=brief, proposal=proposal, repair_action=RepairAction.ADD_CONTRIBUTION, target_gap="contribution",
        markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(),
        generator=ScriptedGenerator("Use the known method in the current task.", ids=("C999",), terms=("method",)),
    )
    assert unauthorized.patch.status == RepairAttemptStatus.ROLLED_BACK
    assert unauthorized.patch.rollback_reason == "UNAUTHORIZED_EVIDENCE_ID"

    unsupported = execute_controlled_generative_repair(
        brief=brief, proposal=proposal, repair_action=RepairAction.REWRITE_TO_ROLE, target_gap="contribution",
        markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(),
        generator=ScriptedGenerator("Use the known method in the current task."),
    )
    assert unsupported.patch.status == RepairAttemptStatus.ROLLED_BACK
    assert unsupported.patch.rollback_reason == "UNSUPPORTED_OR_UNPROPOSED_REPAIR_ACTION"


def test_multi_gap_sequence_stops_on_a_non_match_instead_of_accepting_partial_state() -> None:
    brief = _brief(
        role="EXTEND", must_teach=["ANALYZE"], extensions=["constraint:thin_plate_current_limit"], avoid=["definition"],
    )
    proposal, markdown, digital = _proposal_and_records(brief, "Use the known method in this task.")
    sequence = execute_controlled_repair_sequence(
        brief=brief, proposal=proposal,
        gaps=[(RepairAction.ADD_REQUIRED_FACET, "ANALYZE"), (RepairAction.ADD_EXTENSION, "constraint:thin_plate_current_limit")],
        markdown_rendered=markdown, digital_book_rendered=digital, evidence_by_id=_evidence(),
        generator=ScriptedGenerator("Analyze the arc source method.", terms=("arc", "source", "method")),
    )

    assert len(sequence.patches) == 1
    assert sequence.patches[0].status == RepairAttemptStatus.ROLLED_BACK
    assert sequence.patches[0].rollback_reason.startswith("POST_CONFORMANCE_NOT_MATCH")
    assert sequence.markdown_candidate.markdown == markdown.markdown
