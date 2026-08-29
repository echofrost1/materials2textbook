from __future__ import annotations

from materials2textbook.knowledge_map.models import LearningRole
from materials2textbook.knowledge_map.rendered_conformance import (
    ConformanceStatus,
    check_rendered_conformance,
    extract_rendered_occurrences,
    wrap_rendered_occurrence,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.agents.textbook_writer import TextbookWriterAgent
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def _brief(
    occurrence_id: str,
    role: str,
    *,
    available: list[str] | None = None,
    must_teach: list[str] | None = None,
    extensions: list[str] | None = None,
    avoid: list[str] | None = None,
    recap_limit: int = 0,
) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id,
        source_knowledge_point_id=f"source:{occurrence_id}",
        canonical_knowledge_id="kp:current",
        source_title="Welding current",
        canonical_title="Welding current",
        chapter_id="chapter_01",
        section_id="section_01",
        role=role,
        already_available_facets=available or [],
        required_facets=available or [],
        must_teach_facets=must_teach or [],
        must_not_reteach_facets=available or [],
        extension_keys=extensions or [],
        repeated_aspects_to_avoid=avoid or [],
        prerequisite_context=[],
        contribution_goal="fixture contribution",
        source_chunk_ids=["C001"],
        writing_contract="fixture contract",
        task_ordinal=int(occurrence_id.rsplit(":", 1)[-1]),
        allowed_content=["fixture"],
        forbidden_content=avoid or [],
        max_recap_sentences=recap_limit,
        must_include_points=[],
        must_avoid_patterns=avoid or [],
    )


def test_code_owned_anchors_have_stable_occurrence_chapter_section_task_spans() -> None:
    brief = _brief("occ:1", LearningRole.INTRO, must_teach=["ORIENTED"])
    markdown = "# Fixture\n\n" + wrap_rendered_occurrence(brief, "The current direction guides the observation. Evidence: C001")

    [rendered] = extract_rendered_occurrences(markdown)

    assert rendered.occurrence_id == "occ:1"
    assert rendered.chapter_id == "chapter_01"
    assert rendered.section_id == "section_01"
    assert rendered.task_id == "chapter_01:task:1"
    assert markdown[rendered.start_offset:rendered.end_offset] == rendered.markdown


def test_controlled_intro_teach_apply_extend_are_match_and_duplicate_teach_is_violation() -> None:
    intro = _brief("occ:1", LearningRole.INTRO, must_teach=["ORIENTED"])
    teach = _brief("occ:2", LearningRole.TEACH, available=["ORIENTED"], must_teach=["EXPLAIN"])
    apply = _brief(
        "occ:3", LearningRole.APPLY, available=["EXPLAIN"],
        avoid=["definition", "adjustment method"], recap_limit=1,
    )
    extend = _brief(
        "occ:4", LearningRole.EXTEND, available=["EXPLAIN"],
        extensions=["constraint:thin_plate_current_limit"], avoid=["standard definition"], recap_limit=1,
    )
    duplicate = _brief(
        "occ:5", LearningRole.TEACH, available=["EXPLAIN"],
        avoid=["definition", "effect", "adjustment method", "principle explanation", "complete procedure", "parameter/method rule"],
        recap_limit=2,
    )
    bodies = {
        "occ:1": "Observe current direction first; this creates an initial intuition. Evidence: C001",
        "occ:2": "Welding current is defined as the current through the arc; it affects weld formation. Evidence: C001",
        "occ:3": "In the assembly task, use the already taught setting method to select current and inspect the weld. Evidence: C001",
        "occ:4": "For thin plate work, use the known method with a current limit to avoid burn-through. Evidence: C001",
        "occ:5": "## Concept explanation\nWelding current is defined as the current through the arc.\n## Operating points\nAdjust current according to material thickness. Evidence: C001",
    }
    briefs = [intro, teach, apply, extend, duplicate]
    markdown = "# Fixture\n\n" + "\n".join(wrap_rendered_occurrence(brief, bodies[brief.occurrence_id]) for brief in briefs)

    report = check_rendered_conformance(briefs, markdown)
    results = {item.occurrence_id: item for item in report.results}

    assert report.anchor_coverage == 1.0
    assert results["occ:1"].overall == ConformanceStatus.MATCH
    assert results["occ:2"].overall == ConformanceStatus.MATCH
    assert results["occ:3"].overall == ConformanceStatus.MATCH
    assert results["occ:4"].overall == ConformanceStatus.MATCH
    assert results["occ:5"].overall == ConformanceStatus.VIOLATION
    assert {item.rule for item in results["occ:5"].forbidden_reteach_violation} >= {"definition", "parameter_or_method_rule"}


def test_writer_owns_all_anchors_and_checks_the_rendered_result() -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.responses = iter([
                "Observe current direction to form an initial intuition. Evidence: C001",
                "Current is defined as the arc current and affects weld formation. Evidence: C001",
                "Use the taught method in the current assembly task. Evidence: C001",
                "For thin plate work, impose a current limit to avoid burn-through. Evidence: C001",
                "## Concept explanation\nCurrent is defined as arc current.\nAdjust current according to material thickness. Evidence: C001",
            ])

        def generate(self, messages: list[dict[str, str]]) -> str:
            return next(self.responses)

    briefs = [
        _brief("occ:1", LearningRole.INTRO, must_teach=["ORIENTED"]),
        _brief("occ:2", LearningRole.TEACH, available=["ORIENTED"], must_teach=["EXPLAIN"]),
        _brief("occ:3", LearningRole.APPLY, available=["EXPLAIN"], avoid=["definition", "adjustment method"], recap_limit=1),
        _brief("occ:4", LearningRole.EXTEND, available=["EXPLAIN"], extensions=["constraint:thin_plate_current_limit"], avoid=["standard definition"]),
        _brief(
            "occ:5", LearningRole.TEACH, available=["EXPLAIN"], recap_limit=2,
            avoid=["definition", "effect", "adjustment method", "principle explanation", "complete procedure", "parameter/method rule"],
        ),
    ]
    chunk = EvidenceChunk("C001", "A1", "fixture", "evidence", "fixture", [], "approved", "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore())
    writer = TextbookWriterAgent(llm_provider=StubProvider(), use_llm=True)

    markdown = writer.run([], [chunk], "Fixture", occurrence_writing_briefs=briefs)

    assert markdown.count("<!-- occurrence:start") == 5
    assert markdown.count("<!-- occurrence:end") == 5
    assert writer.last_conformance_report is not None
    result = {item.occurrence_id: item for item in writer.last_conformance_report.results}
    assert writer.last_conformance_report.anchor_coverage == 1.0
    # The writer now rejects a body that violates the immutable brief and
    # deterministically falls back to the brief-bounded occurrence renderer.
    assert result["occ:5"].overall == ConformanceStatus.MATCH
    assert "occ:5: LLM body failed immutable brief conformance" in writer.last_generation_warning


def test_rule_template_fallback_records_non_instructional_provenance() -> None:
    """A template body is auditable as a fallback, never as verified teaching."""
    brief = _brief("occ:93", LearningRole.TEACH, must_teach=["EXPLAIN"])
    chunk = EvidenceChunk(
        "C001", "A1", "fixture", "fixture evidence", "fixture evidence", [], "approved",
        "fixture", "fixture", "fixture", EvidenceLocator(), EvidenceScore(),
    )

    writer = TextbookWriterAgent(use_llm=False)
    markdown = writer.run([], [chunk], "Fixture", occurrence_writing_briefs=[brief])

    [rendered] = extract_rendered_occurrences(markdown)
    assert rendered.generation_provenance == "rule_template_fallback"
    assert writer.last_occurrence_generation_provenance[brief.occurrence_id] == "rule_template_fallback"


def test_explain_facet_accepts_a_deterministic_chinese_explanatory_construction() -> None:
    brief = _brief("occ:1", LearningRole.TEACH, must_teach=["EXPLAIN"])
    markdown = wrap_rendered_occurrence(
        brief,
        "高频引弧是一种非接触式的引弧方式，通过高频电流使电弧迅速引燃。Evidence: C001",
    )

    [result] = check_rendered_conformance([brief], markdown).results

    assert result.must_teach_coverage == {"EXPLAIN": ConformanceStatus.MATCH}
    assert result.overall == ConformanceStatus.MATCH


def test_internal_facet_label_alone_is_not_explain_teaching() -> None:
    brief = _brief("occ:91", LearningRole.TEACH, must_teach=["EXPLAIN"])
    markdown = wrap_rendered_occurrence(
        brief,
        "围绕当前任务，重点学习本次计划新增的内容：EXPLAIN。",
        generation_provenance="rule_template_fallback",
    )

    [result] = check_rendered_conformance([brief], markdown).results

    assert result.must_teach_coverage["EXPLAIN"] == ConformanceStatus.VIOLATION
    assert result.overall == ConformanceStatus.VIOLATION


def test_internal_facet_label_alone_is_not_perform_teaching() -> None:
    brief = _brief("occ:92", LearningRole.TEACH, must_teach=["PERFORM"])
    markdown = wrap_rendered_occurrence(
        brief,
        "本节需要完成 PERFORM。",
        generation_provenance="rule_template_fallback",
    )

    [result] = check_rendered_conformance([brief], markdown).results

    assert result.must_teach_coverage["PERFORM"] == ConformanceStatus.VIOLATION
    assert result.overall == ConformanceStatus.VIOLATION
