from __future__ import annotations

from materials2textbook.knowledge_map.writing_briefs import (
    OccurrenceWritingBrief,
    build_occurrence_writing_briefs_from_payload,
    check_writing_brief_consistency,
)


def test_persisted_semantic_evaluation_becomes_an_immutable_writing_brief() -> None:
    payload = {
        "semantic_deltas": [{
            "occurrence_id": "occ:apply",
            "repeated_aspects": ["definition", "adjustment method"],
            "evidence_chunk_ids": ["C003"],
        }],
        "knowledge_map": {
            "source_knowledge_points": [{"source_knowledge_point_id": "source:current", "title": "Current task"}],
            "knowledge_points": [{"knowledge_id": "kp:current", "title": "Welding current"}],
            "availability_snapshots": [{
                "occurrence_id": "occ:apply",
                "before": {"availability_by_knowledge": {"kp:current": {"available_facets": ["EXPLAIN"]}}},
            }],
            "planned_occurrences": [{
                "occurrence_id": "occ:apply",
                "knowledge_id": "kp:current",
                "source_knowledge_point_id": "source:current",
                "chapter_id": "chapter_01",
                "section_id": "section_01",
                "role": "APPLY",
                "required_self_facets": ["EXPLAIN"],
                "required_prerequisites": [{"knowledge_id": "kp:fixture", "required_facets": ["EXPLAIN"]}],
                "intended_grants": [],
                "intended_extension_keys": [],
                "intended_contribution": "Apply the known method to this task.",
                "source_chunk_ids": ["C003"],
                "trusted_for_state": True,
            }],
        },
    }

    [brief] = build_occurrence_writing_briefs_from_payload(payload)

    assert brief.role == "APPLY"
    assert brief.already_available_facets == ["EXPLAIN"]
    assert brief.required_facets == ["EXPLAIN"]
    assert brief.must_teach_facets == []
    assert brief.must_not_reteach_facets == ["EXPLAIN"]
    assert brief.repeated_aspects_to_avoid == ["definition", "adjustment method"]
    assert brief.prerequisite_context == ["self: EXPLAIN", "kp:fixture: EXPLAIN"]
    assert brief.source_chunk_ids == ["C003"]
    assert "current task action" in brief.allowed_content
    assert "definition" in brief.forbidden_content
    assert brief.max_recap_sentences == 1
    assert "parameter/method rule" in brief.must_avoid_patterns


def test_writing_brief_consistency_rejects_a_new_grant_that_is_also_forbidden_reteach() -> None:
    brief = OccurrenceWritingBrief(
        occurrence_id="occ:conflict", source_knowledge_point_id="source:1", canonical_knowledge_id="kp:1",
        source_title="fixture", canonical_title="fixture", chapter_id="chapter_01", section_id="section_01",
        role="TEACH", already_available_facets=["PERFORM"], required_facets=[],
        must_teach_facets=["PERFORM"], must_not_reteach_facets=["PERFORM"], extension_keys=[],
        repeated_aspects_to_avoid=[], prerequisite_context=[], contribution_goal="", source_chunk_ids=["C001"],
        writing_contract="fixture",
    )
    [issue] = check_writing_brief_consistency([brief])
    assert issue.rule == "MUST_TEACH_AND_MUST_NOT_RETEACH_OVERLAP"


def test_untrusted_persisted_occurrence_cannot_reach_writer() -> None:
    payload = {
        "semantic_deltas": [],
        "knowledge_map": {
            "source_knowledge_points": [],
            "knowledge_points": [],
            "availability_snapshots": [],
            "planned_occurrences": [{"occurrence_id": "occ:untrusted", "trusted_for_state": False}],
        },
    }

    try:
        build_occurrence_writing_briefs_from_payload(payload)
    except ValueError as exc:
        assert "untrusted" in str(exc)
    else:  # pragma: no cover - documents the hard safety boundary.
        raise AssertionError("Untrusted semantic output must not constrain the writer.")
