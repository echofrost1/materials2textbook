from __future__ import annotations

from materials2textbook.knowledge_map.downstream_closure import (
    BLOCKED_BY_PRIOR_FAILURE,
    CLOSED,
    TARGET_NOT_DELIVERED,
    UNDER_SUPPORTED,
    UNMAPPED_REQUIREMENT,
    analyze_downstream_closure,
    normalize_requirement_semantic_proposal,
)
from materials2textbook.knowledge_map.models import BookPosition, PlannedOccurrence, SourceKnowledgePoint


def _occurrence(occurrence_id: str, task: int, section: str = "section_01") -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_id=occurrence_id,
        knowledge_id="kp:generic",
        source_knowledge_point_id=f"source:{occurrence_id}",
        position=BookPosition(1, task, task),
        chapter_id="chapter_01",
        section_id=section,
        context_title="generic task",
        source_chunk_ids=["chunk:generic"],
        role="TEACH",
    )


def _source(occurrence: PlannedOccurrence) -> SourceKnowledgePoint:
    return SourceKnowledgePoint(
        source_knowledge_point_id=occurrence.source_knowledge_point_id,
        title="通用方法",
        chapter_id=occurrence.chapter_id,
        section_id=occurrence.section_id,
        chapter_ordinal=1,
        section_ordinal=1,
        task_ordinal=occurrence.position.task_ordinal,
        source_point_ordinal=1,
        context_title="generic task",
    )


def _book(*, block_type: str, text: str, task: int = 1, section: str = "section_01", goal: str | None = None, metadata: dict | None = None) -> dict:
    blocks = [{"type": block_type, "items": [text], "metadata": metadata or {}}]
    project = {
        "project_id": "chapter_01",
        "learning_goals": [goal] if goal else [],
        "tasks": [{
            "task_id": str(task),
            "title": "任务",
            "metadata": {"section_id": section},
            "blocks": blocks,
        }],
    }
    return {"book_id": "book", "projects": [project]}


def _state(facets: list[str], *, occurrence_id: str = "a1") -> dict:
    return {
        "position": {"chapter_ordinal": 1, "task_ordinal": 1, "occurrence_ordinal": 1},
        "availability_by_knowledge": {
            "kp:generic": {
                "available_facets": facets,
                "available_extension_keys": [],
                "facet_source_occurrence_ids": {facet: occurrence_id for facet in facets},
                "extension_source_occurrence_ids": {},
            }
        },
    }


def _execution(*, after: dict | None = None, blocked: list[dict] | None = None) -> dict:
    return {
        "transitions": ([{"occurrence_id": "a1", "after": after}] if after is not None else []),
        "blocked_occurrences": blocked or [],
        "coverage": {"execution_blocked_occurrences": blocked or []},
    }


def test_case_f_future_verified_facet_cannot_close_earlier_exercise() -> None:
    a1 = _occurrence("a1", 1)
    a3 = _occurrence("a3", 3)
    book = _book(block_type="exercises", text="请分析通用方法的影响。", task=1)
    execution = {
        "transitions": [
            {"occurrence_id": "a1", "after": _state(["ORIENTED"])},
            {"occurrence_id": "a3", "after": _state(["ORIENTED", "ANALYZE"])},
        ],
        "blocked_occurrences": [],
        "coverage": {"execution_blocked_occurrences": []},
    }
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1, a3],
        source_knowledge_points=[_source(a1), _source(a3)],
        semantic_execution=execution,
    )
    result = report.results[0]
    assert result.status == UNDER_SUPPORTED
    assert "ANALYZE" not in result.verified_facets


def test_case_g_forward_navigation_closes_when_section_delivers_target() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(block_type="learning_nav", text="本节重点掌握通用方法。")
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["ORIENTED"])),
    )
    assert report.results[0].status == CLOSED


def test_case_h_forward_navigation_target_blocked_is_not_prior_knowledge_gap() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(block_type="learning_nav", text="本节重点掌握通用方法。")
    blocked = [{"occurrence_id": "a1", "issue_code": "CROSS_PREREQUISITE_NOT_VERIFIED"}]
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(blocked=blocked),
    )
    assert report.results[0].status == TARGET_NOT_DELIVERED


def test_case_i_project_goal_uses_project_end_state_not_project_start() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(block_type="learning_nav", text="导航", goal="能够分析通用方法的影响。")
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["ANALYZE"])),
    )
    goal = next(item for item in report.results if item.requirement.source_module == "project_learning_goal")
    assert goal.status == CLOSED


def test_case_j_ambiguous_requirement_is_not_forced_to_a_facet() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(block_type="exercises", text="请处理相关内容。")
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["ANALYZE", "PERFORM", "EXPLAIN"])),
    )
    assert report.results[0].status == UNMAPPED_REQUIREMENT


def test_template_metadata_maps_assessment_without_reparsing_visible_text() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(
        block_type="assessment",
        text="系统生成的模板文本。",
        metadata={"requirement_semantics": [{
            "requirement_type": "ASSESSMENT_TEMPLATE",
            "target_knowledge_ids": ["kp:generic"],
            "candidate_required_facets": ["ORIENTED"],
            "extracted_action": "ORIENTED",
            "mapping_source": "TEMPLATE_METADATA",
            "mapping_confidence": 1.0,
            "extraction_provenance": "assessment_generator",
        }]},
    )
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["ORIENTED"])),
    )
    result = report.results[0]
    assert result.status == CLOSED
    assert result.requirement.mapping_source == "TEMPLATE_METADATA"


def test_model_extracted_compound_requirement_preserves_all_relation() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(
        block_type="exercises",
        text="说明并分析该方法。",
        metadata={"requirement_semantics": [{
            "requirement_type": "EXERCISE",
            "target_knowledge_ids": ["kp:generic"],
            "candidate_required_facets": ["EXPLAIN", "ANALYZE"],
            "facet_relation": "ALL",
            "extracted_action": "EXPLAIN+ANALYZE",
            "mapping_source": "MODEL_EXTRACTED",
            "mapping_confidence": 0.93,
            "extraction_provenance": "qwen_requirement_semantic_v1",
        }]},
    )
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["EXPLAIN"])),
    )
    assert report.results[0].status == UNDER_SUPPORTED
    assert report.results[0].requirement.required_facets == ("EXPLAIN", "ANALYZE")


def test_requirement_any_relation_does_not_become_all() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(
        block_type="assessment",
        text="说明或分析该方法。",
        metadata={"requirement_semantics": [{
            "target_knowledge_ids": ["kp:generic"],
            "candidate_required_facets": ["EXPLAIN", "ANALYZE"],
            "facet_relation": "ANY",
            "mapping_source": "MODEL_EXTRACTED",
            "mapping_confidence": 0.9,
        }]},
    )
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["EXPLAIN"])),
    )
    assert report.results[0].status == CLOSED


def test_invalid_model_target_is_unmapped_not_supported() -> None:
    a1 = _occurrence("a1", 1)
    book = _book(
        block_type="exercises",
        text="分析该方法。",
        metadata={"requirement_semantics": [{
            "target_knowledge_ids": ["kp:not-in-book"],
            "candidate_required_facets": ["ANALYZE"],
            "mapping_source": "MODEL_EXTRACTED",
            "mapping_confidence": 0.95,
        }]},
    )
    report = analyze_downstream_closure(
        digital_book=book,
        planned_occurrences=[a1],
        source_knowledge_points=[_source(a1)],
        semantic_execution=_execution(after=_state(["ANALYZE"])),
    )
    assert report.results[0].status == UNMAPPED_REQUIREMENT


def test_model_proposal_unknown_target_is_rejected_by_whitelist() -> None:
    normalized = normalize_requirement_semantic_proposal(
        {
            "target_knowledge_ids": ["kp:not-in-book"],
            "candidate_required_facets": ["ANALYZE"],
            "confidence": 0.99,
        },
        allowed_knowledge_ids={"kp:generic"},
        model_version="test",
    )
    assert normalized["mapping_source"] == "UNMAPPED"
    assert normalized["mapping_confidence"] == 0.0
    assert normalized["invalid_target_knowledge_ids"] == ["kp:not-in-book"]
