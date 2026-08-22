from __future__ import annotations

from materials2textbook.knowledge_map.models import LearningRole
from materials2textbook.knowledge_map.rendered_conformance import wrap_rendered_occurrence
from materials2textbook.knowledge_map.section_discourse import (
    build_section_discourse_bodies,
    complete_section_discourse_audits,
)
from materials2textbook.knowledge_map.semantic_book_conformance import build_semantic_book_conformance_report
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief, WritingBriefCoverage


def _brief(
    occurrence_id: str,
    role: str,
    ordinal: int,
    *,
    title: str = "Section S",
    canonical_title: str | None = None,
    source_ids: list[str] | None = None,
    extensions: list[str] | None = None,
) -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id=occurrence_id,
        source_knowledge_point_id=f"source:{occurrence_id}",
        canonical_knowledge_id=f"kp:{occurrence_id}",
        source_title=title,
        canonical_title=canonical_title or title,
        chapter_id="chapter_01",
        section_id="section_01",
        role=role,
        already_available_facets=["EXPLAIN"] if source_ids else [],
        required_facets=[],
        must_teach_facets=["EXPLAIN"] if role == LearningRole.TEACH else [],
        must_not_reteach_facets=["EXPLAIN"] if source_ids else [],
        extension_keys=extensions or [],
        repeated_aspects_to_avoid=[],
        prerequisite_context=[],
        contribution_goal="fixture contribution",
        source_chunk_ids=["C001"],
        writing_contract="fixture",
        task_ordinal=1,
        occurrence_ordinal=ordinal,
        availability_source_occurrence_ids=source_ids or [],
    )


def test_section_assembly_keeps_one_title_and_two_auditable_spans() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1)
    second = _brief("occ:b", LearningRole.TEACH, 2)
    rows = [
        {"occurrence_id": first.occurrence_id, "body": "Teach A", "source_title": first.source_title},
        {"occurrence_id": second.occurrence_id, "body": "Teach B", "source_title": second.source_title},
    ]

    bodies, audits = build_section_discourse_bodies(rows, [first, second])

    assert list(bodies) == ["occ:a", "occ:b"]
    assert audits[0].visible_title_count == 1
    assert audits[0].rendered_occurrence_ids == ("occ:a", "occ:b")
    assert audits[0].order_preserved is True
    assert audits[0].visible_passage_count == 1
    assert audits[0].passage_id == "chapter_01:section_01:passage"
    assert "Teach A" in bodies["occ:a"]
    assert "Teach B" in bodies["occ:b"]


def test_phase2b_keeps_adjacent_occurrences_in_one_auditable_passage() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1)
    second = _brief("occ:b", LearningRole.TEACH, 2)
    rows = [
        {"occurrence_id": first.occurrence_id, "body": "Teach A", "source_title": first.source_title},
        {"occurrence_id": second.occurrence_id, "body": "Teach B", "source_title": second.source_title},
    ]

    bodies, audits = build_section_discourse_bodies(rows, [first, second])

    # The audit defines one visible passage while the body map still exposes
    # two independently auditable occurrence spans.
    assert audits[0].visible_passage_count == 1
    assert audits[0].rendered_occurrence_ids == ("occ:a", "occ:b")
    assert set(bodies) == {"occ:a", "occ:b"}


def test_teach_to_apply_adds_prior_to_current_task_bridge_without_replanning() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1, title="Section S", canonical_title="安全确认")
    second = _brief("occ:b", LearningRole.APPLY, 2, title="Section S", canonical_title="当前任务", source_ids=["occ:a"])
    rows = [
        {"occurrence_id": first.occurrence_id, "body": "Teach safety", "source_title": first.source_title},
        {"occurrence_id": second.occurrence_id, "body": "Apply it", "source_title": second.source_title},
    ]

    bodies, audits = build_section_discourse_bodies(rows, [first, second])

    assert second.role == LearningRole.APPLY
    assert "安全确认" in bodies["occ:b"]
    assert "当前任务" in bodies["occ:b"] or "Section S" in bodies["occ:b"]
    assert audits[0].transitions[1].kind == "PRIOR_TO_APPLICATION"


def test_teach_to_extend_keeps_new_contribution_explicit() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1, title="焊接缺陷")
    second = _brief(
        "occ:b",
        LearningRole.EXTEND,
        2,
        title="焊接缺陷扩展",
        source_ids=["occ:a"],
        extensions=["current_limit"],
    )
    rows = [
        {"occurrence_id": first.occurrence_id, "body": "Teach defect", "source_title": first.source_title},
        {"occurrence_id": second.occurrence_id, "body": "Add the limit", "source_title": second.source_title},
    ]

    bodies, audits = build_section_discourse_bodies(rows, [first, second])

    assert "焊接缺陷" in bodies["occ:b"]
    assert "current_limit" in bodies["occ:b"]
    assert audits[0].transitions[1].kind == "KNOWN_TO_NEW_INCREMENT"


def test_existing_writer_bridge_is_not_duplicated() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1, title="焊接缺陷")
    second = _brief(
        "occ:b",
        LearningRole.EXTEND,
        2,
        title="焊接缺陷扩展",
        source_ids=["occ:a"],
        extensions=["current_limit"],
    )
    rows = [
        {"occurrence_id": first.occurrence_id, "body": "Teach defect", "source_title": first.source_title},
        {
            "occurrence_id": second.occurrence_id,
            "body": "在已学习焊接缺陷的基础上，本处只讨论新的限制。",
            "source_title": second.source_title,
        },
    ]

    bodies, _audits = build_section_discourse_bodies(rows, [first, second])

    assert bodies["occ:b"].count("在前面") == 0
    assert bodies["occ:b"].count("在已学习") == 1


def test_blocked_or_zero_render_occurrence_is_not_fabricated() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1)
    third = _brief("occ:c", LearningRole.TEACH, 3)
    rows = [
        {"occurrence_id": first.occurrence_id, "body": "Teach A", "source_title": first.source_title},
        {"occurrence_id": third.occurrence_id, "body": "Teach C", "source_title": third.source_title},
    ]
    _bodies, audits = build_section_discourse_bodies(rows, [first, third])
    completed = complete_section_discourse_audits(
        audits,
        blocked_occurrence_ids={"occ:b"},
        zero_render_occurrence_ids={"occ:d"},
    )

    assert "occ:b" not in completed[0].rendered_occurrence_ids
    assert "occ:d" not in completed[0].rendered_occurrence_ids
    assert "occ:b" not in completed[0].blocked_occurrence_ids
    assert "occ:d" not in completed[0].zero_render_occurrence_ids


def test_formal_empty_section_is_preserved_without_occurrence_body() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1)
    _bodies, audits = build_section_discourse_bodies(
        [{"occurrence_id": first.occurrence_id, "body": "Teach A", "source_title": first.source_title}],
        [first],
    )
    completed = complete_section_discourse_audits(
        audits,
        blocked_occurrence_ids={"occ:chapter_01_section_02:kp:01:kp:blocked"},
        section_catalog=[
            {"chapter_id": "chapter_01", "section_id": "section_01", "title": "Section S"},
            {"chapter_id": "chapter_01", "section_id": "chapter_01_section_02", "title": "Blocked Section"},
        ],
    )

    empty = next(item for item in completed if item.section_id == "chapter_01_section_02")
    assert empty.title == "Blocked Section"
    assert empty.rendered_occurrence_ids == ()
    assert empty.blocked_occurrence_ids == ("occ:chapter_01_section_02:kp:01:kp:blocked",)


def test_section_conformance_reports_single_visible_title_and_preserves_occurrence_mapping() -> None:
    first = _brief("occ:a", LearningRole.TEACH, 1)
    second = _brief("occ:b", LearningRole.APPLY, 2, source_ids=["occ:a"])
    bodies, audits = build_section_discourse_bodies(
        [
            {"occurrence_id": first.occurrence_id, "body": "Teach A", "source_title": first.source_title},
            {"occurrence_id": second.occurrence_id, "body": "Apply B", "source_title": second.source_title},
        ],
        [first, second],
    )
    markdown = "### Section S\n\n" + "\n".join(
        wrap_rendered_occurrence(brief, bodies[brief.occurrence_id]).strip()
        for brief in [first, second]
    )
    report = build_semantic_book_conformance_report(
        coverage=WritingBriefCoverage(briefs=[first, second]),
        markdown=markdown,
        digital_book_metadata={"semantic_occurrence_roles": {"occ:a": "TEACH", "occ:b": "APPLY"}, "semantic_rendered_conformance": {"anchor_coverage": 1.0, "results": []}, "semantic_section_assemblies": [item.to_dict() for item in audits]},
    )

    assert report.section_discourse["status"] == "MATCH"
    assert report.section_discourse["section_count"] == 1
