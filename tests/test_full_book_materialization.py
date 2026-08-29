from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from materials2textbook.knowledge_map.materialization import (
    OccurrenceFinalStatus,
    instruction_from_generated_repair,
    instruction_from_recall_capsule,
    instruction_from_safe_repair,
    materialize_full_book,
    write_materialized_book_artifacts,
)
from materials2textbook.knowledge_map.models import BookPosition, PlannedOccurrence
from materials2textbook.knowledge_map.repair_proposals import build_repair_proposal_report
from materials2textbook.knowledge_map.rendered_conformance import extract_rendered_occurrences, wrap_rendered_occurrence, check_rendered_conformance
from materials2textbook.knowledge_map.safe_auto_repair import execute_synchronized_safe_repair
from materials2textbook.knowledge_map.writing_briefs import (
    OccurrenceWritingBrief,
    RejectedPlanOccurrence,
    WritingBriefCoverage,
    ZeroRenderOccurrence,
)
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    DigitalBook,
    DigitalBookBlock,
    DigitalBookProject,
    DigitalBookTask,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def _brief(occurrence_id: str, section_id: str) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id, source_knowledge_point_id=f"source:{occurrence_id}", canonical_knowledge_id=f"kp:{occurrence_id}",
        source_title=occurrence_id, canonical_title=occurrence_id, chapter_id="chapter_01", section_id=section_id,
        role="APPLY", already_available_facets=["EXPLAIN"], required_facets=["EXPLAIN"], must_teach_facets=[],
        must_not_reteach_facets=["EXPLAIN"], extension_keys=[], repeated_aspects_to_avoid=["definition"],
        prerequisite_context=["self: EXPLAIN"], contribution_goal="Use the known method in the current task.", source_chunk_ids=["C001"],
        writing_contract="immutable", semantic_delta_evidence_ids=["C001"], task_ordinal=1, occurrence_ordinal=1,
        allowed_content=["task"], forbidden_content=["definition"], max_recap_sentences=1,
        must_include_points=[], must_avoid_patterns=["definition"],
    )


def _book(briefs: list[OccurrenceWritingBrief], bodies: list[str]) -> DigitalBook:
    blocks = []
    for index, (brief, body) in enumerate(zip(briefs, bodies), start=1):
        block_id = f"block:{index}"
        blocks.append(DigitalBookBlock(
            block_id=block_id, type="implementation", title=brief.source_title, markdown=body,
            metadata={"semantic_occurrence": {
                "occurrence_id": brief.occurrence_id, "role": brief.role, "chapter_id": brief.chapter_id,
                "section_id": brief.section_id, "block_id": block_id,
            }},
        ))
    return DigitalBook("book", "教材", {}, [DigitalBookProject("chapter_01", "第一章", "", [], [], [DigitalBookTask("task:1", "学习任务", blocks)])])


def _evidence(text: str) -> EvidenceChunk:
    return EvidenceChunk("C001", "A001", "known method", text, text, [], "approved", "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore())


def _reference_book_plan() -> BookPlan:
    return BookPlan(
        "book", "Fixture", "test",
        [BookChapterPlan("chapter_01", 1, "Original chapter", [], [BookSectionPlan("section_01", "1.1", "Original section", ["Current"], ["C001"])])],
    )


def _safe_instruction(brief: OccurrenceWritingBrief, body: str):
    [markdown] = extract_rendered_occurrences(wrap_rendered_occurrence(brief, body))
    digital = replace(markdown, render_target="digital_book", block_id="block:1")
    conformance = check_rendered_conformance([brief], wrap_rendered_occurrence(brief, body))
    proposal = build_repair_proposal_report(briefs=[brief], rendered_occurrences=[markdown], conformance_results=conformance.results).proposals[0]
    result = execute_synchronized_safe_repair(brief=brief, proposal=proposal, markdown_rendered=markdown, digital_book_rendered=digital)
    return result, instruction_from_safe_repair(result)


def _planned_terminal_occurrence(occurrence_id: str, section_id: str) -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id=occurrence_id,
        knowledge_id=f"kp:{occurrence_id}",
        source_knowledge_point_id=f"source:{occurrence_id}",
        position=BookPosition(1, 1, 1),
        chapter_id="chapter_01",
        section_id=section_id,
        context_title=section_id,
        source_chunk_ids=["C001"],
        role="APPLY",
    )


def test_full_book_materializes_accepted_safe_repair_with_audit_and_publish_gate(tmp_path) -> None:
    repaired, original = _brief("occ:repair", "section_01"), _brief("occ:original", "section_02")
    before = "Use the known method in the current task. Current is defined as arc current."
    original_body = "Use the known method in the current task."
    safe_result, instruction = _safe_instruction(repaired, before)
    assert instruction is not None
    markdown = "# 教材\n\n" + wrap_rendered_occurrence(repaired, before) + "\n" + wrap_rendered_occurrence(original, original_body)
    result = materialize_full_book(
        markdown=markdown, digital_book=_book([repaired, original], [before, original_body]),
        coverage=WritingBriefCoverage(briefs=[repaired, original]), outline_signature="outline:v1", expected_outline_signature="outline:v1",
        semantic_objects={"canonical": ["kp:repair", "kp:original"], "roles": ["APPLY", "APPLY"]}, instructions=[instruction],
        evidence_chunks=[_evidence("Use the known method in the current task.")],
        source_book_plan_snapshot=_reference_book_plan(), final_reference_book_plan=_reference_book_plan(),
    )

    assert result.publication_gate.publishable
    assert result.publication_gate.markdown_digital_alignment == 1.0
    assert "Current is defined as arc current." not in result.markdown
    assert [item.status for item in result.final_states] == [OccurrenceFinalStatus.VERIFIED_REPAIRED, OccurrenceFinalStatus.VERIFIED_ORIGINAL]
    assert result.repair_audit[0].status == "MATERIALIZED"
    assert result.digital_book.projects[0].tasks[0].blocks[0].markdown == result.repair_audit[0].after_text
    assert safe_result.attempt.original_text == before
    paths = write_materialized_book_artifacts(result=result, output_dir=tmp_path)
    assert all(path.exists() for path in paths)


def test_publication_gate_requires_downstream_closure_for_standard_production() -> None:
    brief = _brief("occ:closure", "section_01")
    body = "Use the known method in the current task."
    result = materialize_full_book(
        markdown=wrap_rendered_occurrence(brief, body),
        digital_book=_book([brief], [body]),
        coverage=WritingBriefCoverage(briefs=[brief]),
        outline_signature="outline:v1",
        expected_outline_signature="outline:v1",
        semantic_objects={"canonical": ["kp:closure"], "roles": ["APPLY"]},
        evidence_chunks=[_evidence(body)],
        source_book_plan_snapshot=_reference_book_plan(),
        final_reference_book_plan=_reference_book_plan(),
        downstream_closure_required=True,
    )

    assert result.publication_gate.publishable is False
    assert result.publication_gate.downstream_closure_complete is False
    assert "DOWNSTREAM_CLOSURE_MISSING" in result.publication_gate.blockers


def test_publication_gate_consumes_downstream_closure_statuses() -> None:
    brief = _brief("occ:closure", "section_01")
    body = "Use the known method in the current task."
    closure = SimpleNamespace(results=[SimpleNamespace(status="UNSUPPORTED")])
    result = materialize_full_book(
        markdown=wrap_rendered_occurrence(brief, body),
        digital_book=_book([brief], [body]),
        coverage=WritingBriefCoverage(briefs=[brief]),
        outline_signature="outline:v1",
        expected_outline_signature="outline:v1",
        semantic_objects={"canonical": ["kp:closure"], "roles": ["APPLY"]},
        evidence_chunks=[_evidence(body)],
        source_book_plan_snapshot=_reference_book_plan(),
        final_reference_book_plan=_reference_book_plan(),
        downstream_closure_report=closure,
        downstream_closure_required=True,
    )

    assert result.publication_gate.publishable is False
    assert result.publication_gate.downstream_closure_complete is True
    assert result.publication_gate.downstream_hard_blockers == 1
    assert "DOWNSTREAM_CLOSURE_HARD_BLOCKERS" in result.publication_gate.blockers


def test_materialization_fails_closed_on_digital_base_drift_and_reports_rejected_evidence() -> None:
    brief = _brief("occ:repair", "section_01")
    before = "Use the known method in the current task. Current is defined as arc current."
    _, instruction = _safe_instruction(brief, before)
    coverage = WritingBriefCoverage(
        briefs=[brief],
        rejected_plan_occurrences=[RejectedPlanOccurrence("occ:rejected", "source:rejected", "kp:rejected", "chapter_01", "section_03", 3, 3, "unsupported_planning_claim", "UNSUPPORTED")],
    )
    result = materialize_full_book(
        markdown=wrap_rendered_occurrence(brief, before), digital_book=_book([brief], ["drifted digital body"]), coverage=coverage,
        outline_signature="outline:v1", expected_outline_signature="outline:v1", semantic_objects={"canonical": ["kp:repair"]}, instructions=[instruction],
        source_book_plan_snapshot=_reference_book_plan(), final_reference_book_plan=_reference_book_plan(),
    )

    assert not result.publication_gate.publishable
    assert result.repair_audit[0].status == "ROLLED_BACK"
    assert result.repair_audit[0].rollback_reason == "MATERIALIZATION_BASE_MISMATCH"
    states = {item.occurrence_id: item.status for item in result.final_states}
    assert states["occ:repair"] == OccurrenceFinalStatus.FAILED_CONFORMANCE
    assert states["occ:rejected"] == OccurrenceFinalStatus.REJECTED_EVIDENCE


def test_publication_gate_verifies_outline_and_semantic_immutability() -> None:
    brief = _brief("occ:original", "section_01")
    body = "Use the known method in the current task."
    result = materialize_full_book(
        markdown=wrap_rendered_occurrence(brief, body), digital_book=_book([brief], [body]),
        coverage=WritingBriefCoverage(briefs=[brief]), outline_signature="outline:v2", expected_outline_signature="outline:v1",
        semantic_objects={"canonical": ["kp:original"]}, expected_semantic_fingerprint="deliberately-wrong", instructions=[],
        source_book_plan_snapshot=_reference_book_plan(), final_reference_book_plan=_reference_book_plan(),
    )

    assert not result.publication_gate.publishable
    assert not result.publication_gate.outline_signature_unchanged
    assert not result.publication_gate.semantic_objects_unchanged
    assert "OUTLINE_SIGNATURE_CHANGED" in result.publication_gate.blockers
    assert "SEMANTIC_OBJECT_MUTATED" in result.publication_gate.blockers


def test_publication_gate_rejects_any_deep_source_book_plan_mutation() -> None:
    brief = _brief("occ:original", "section_01")
    body = "Use the known method in the current task."
    source = _reference_book_plan()
    mutated = replace(source, chapters=[replace(source.chapters[0], sections=[replace(source.chapters[0].sections[0], title="Changed section")])])

    result = materialize_full_book(
        markdown=wrap_rendered_occurrence(brief, body), digital_book=_book([brief], [body]),
        coverage=WritingBriefCoverage(briefs=[brief]), outline_signature="outline:v1", expected_outline_signature="outline:v1",
        semantic_objects={"canonical": ["kp:original"]}, instructions=[],
        source_book_plan_snapshot=source, final_reference_book_plan=mutated,
    )

    assert not result.publication_gate.source_book_plan_unchanged
    assert not result.publication_gate.publishable
    assert "SOURCE_BOOK_PLAN_MUTATED" in result.publication_gate.blockers


def test_publication_gate_fails_closed_when_source_book_plan_proof_is_missing() -> None:
    brief = _brief("occ:original", "section_01")
    body = "Use the known method in the current task."

    result = materialize_full_book(
        markdown=wrap_rendered_occurrence(brief, body), digital_book=_book([brief], [body]),
        coverage=WritingBriefCoverage(briefs=[brief]), outline_signature="outline:v1", expected_outline_signature="outline:v1",
        semantic_objects={"canonical": ["kp:original"]}, instructions=[],
    )

    assert not result.publication_gate.source_book_plan_unchanged
    assert not result.publication_gate.publishable
    assert "SOURCE_BOOK_PLAN_MUTATED" in result.publication_gate.blockers


def test_generated_patch_and_recall_capsule_adapters_accept_only_prior_dual_matches() -> None:
    brief = _brief("occ:adapter", "section_01")
    [record] = extract_rendered_occurrences(wrap_rendered_occurrence(brief, "before"))
    candidate = replace(record, markdown="after")
    patch = SimpleNamespace(
        status="ACCEPTED", occurrence_id=brief.occurrence_id, repair_action="ADD_CONTRIBUTION",
        before_text="before", after_text="after", to_dict=lambda: {"kind": "generated"},
    )
    generated = SimpleNamespace(patch=patch, markdown_candidate=candidate, digital_book_candidate=replace(candidate, render_target="digital_book"))
    generated_instruction = instruction_from_generated_repair(generated)
    assert generated_instruction and generated_instruction.repair_kind == "GENERATED_PATCH"

    attempt = SimpleNamespace(
        status="ACCEPTED", occurrence_id=brief.occurrence_id, before_text="before", after_text="after",
    )
    recall = SimpleNamespace(attempt=attempt, markdown_candidate=candidate, digital_book_candidate=replace(candidate, render_target="digital_book"))
    recall_instruction = instruction_from_recall_capsule(recall)
    assert recall_instruction and recall_instruction.actions == ("RESTORE_MINIMAL_RECALL",)


def test_every_planned_occurrence_gets_one_terminal_materialization_state() -> None:
    rendered = [_brief(f"occ:rendered:{index}", f"section_{index}") for index in range(1, 4)]
    blocked_before_writer = [
        {
            "occurrence_id": "occ:blocked:input",
            "issue_code": "INCOMPLETE_SEMANTIC_EXECUTION_INPUT",
            "details": "missing semantic input",
            "canonical_knowledge_id": "kp:blocked:input",
            "outline_node_id": "section_04",
            "rendered": False,
        },
        {
            "occurrence_id": "occ:blocked:prerequisite",
            "issue_code": "CROSS_PREREQUISITE_NOT_VERIFIED",
            "details": "prior teaching is unavailable",
            "canonical_knowledge_id": "kp:blocked:prerequisite",
            "outline_node_id": "section_05",
            "rendered": False,
        },
    ]
    zero = ZeroRenderOccurrence(
        occurrence_id="occ:zero",
        source_knowledge_point_id="source:zero",
        canonical_knowledge_id="kp:zero",
        chapter_id="chapter_01",
        section_id="section_06",
        outline_node_id="section_06",
        task_ordinal=1,
        occurrence_ordinal=6,
        role="APPLY",
        non_render_reason="NO_CURRENT_TEACHING_OR_TASK_USE_VALUE",
    )
    coverage = WritingBriefCoverage(
        briefs=rendered,
        execution_blocked_occurrences=blocked_before_writer,
        zero_render_occurrences=[zero],
    )
    bodies = ["Use the known method in the current task."] * 3
    planned = [
        *[_planned_terminal_occurrence(item.occurrence_id, item.section_id) for item in rendered],
        _planned_terminal_occurrence("occ:blocked:input", "section_04"),
        _planned_terminal_occurrence("occ:blocked:prerequisite", "section_05"),
        _planned_terminal_occurrence("occ:zero", "section_06"),
    ]
    result = materialize_full_book(
        markdown="# Fixture\n\n" + "\n".join(wrap_rendered_occurrence(item, body) for item, body in zip(rendered, bodies)),
        digital_book=_book(rendered, bodies),
        coverage=coverage,
        outline_signature="outline:v1",
        expected_outline_signature="outline:v1",
        semantic_objects={"occurrences": [item.occurrence_id for item in planned]},
        planned_occurrences=planned,
        source_book_plan_snapshot=_reference_book_plan(),
        final_reference_book_plan=_reference_book_plan(),
    )

    assert result.planned_occurrence_count == 6
    assert result.terminal_state_count == 6
    assert result.terminal_state_complete is True
    assert len(result.final_states) == 6
    statuses = {item.occurrence_id: item.status for item in result.final_states}
    assert statuses["occ:rendered:1"] == OccurrenceFinalStatus.VERIFIED_ORIGINAL
    assert statuses["occ:rendered:2"] == OccurrenceFinalStatus.VERIFIED_ORIGINAL
    assert statuses["occ:rendered:3"] == OccurrenceFinalStatus.VERIFIED_ORIGINAL
    assert statuses["occ:blocked:input"] == OccurrenceFinalStatus.BLOCKED_BEFORE_RENDER
    assert statuses["occ:blocked:prerequisite"] == OccurrenceFinalStatus.EXECUTION_BLOCKED
    assert statuses["occ:zero"] == OccurrenceFinalStatus.ZERO_RENDERED
    assert all(item.canonical_knowledge_id and item.outline_node_id for item in result.final_states)
