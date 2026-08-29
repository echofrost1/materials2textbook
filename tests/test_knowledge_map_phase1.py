from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from materials2textbook.knowledge_map import analyze_book_knowledge, write_knowledge_map_artifacts
from materials2textbook.knowledge_map.models import (
    BookPosition,
    LearningRole,
    MasteryFacet,
    PlannedOccurrence,
    Prerequisite,
    PrerequisiteUse,
)
from materials2textbook.knowledge_map.availability import simulate_planned_instructional_availability
from materials2textbook.knowledge_map.semantic import HeuristicSemanticPlanner
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)


def make_chunk(chunk_id: str, title: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=chunk_id,
        title=title,
        content=f"{title} 的教学证据。",
        summary=f"{title} 摘要。",
        keywords=[title],
        subject="通用技能",
        material_block="通用技能",
        material_block_code="generic",
        recommended_chapter="",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.9),
        review_status="approved",
    )


def make_plan(sections_by_chapter: list[list[tuple[str, str]]]) -> BookPlan:
    chapters = []
    for chapter_index, sections in enumerate(sections_by_chapter, start=1):
        chapter_sections = []
        for section_index, (title, knowledge_title) in enumerate(sections, start=1):
            chunk_id = f"C{chapter_index}_{section_index}"
            chapter_sections.append(
                BookSectionPlan(
                    section_id=f"c{chapter_index}_s{section_index}",
                    section_no=f"{chapter_index}.{section_index}",
                    title=title,
                    knowledge_point_ids=[knowledge_title],
                    primary_material_ids=[chunk_id],
                )
            )
        chapters.append(
            BookChapterPlan(
                chapter_id=f"chapter_{chapter_index:02d}",
                chapter_no=chapter_index,
                title=f"第{chapter_index}章",
                learning_goals=[],
                sections=chapter_sections,
            )
        )
    return BookPlan(book_id="demo", title="阶段一测试教材", planning_strategy="test", chapters=chapters)


def chunks_for(plan: BookPlan) -> list[EvidenceChunk]:
    chunks = []
    for chapter in plan.chapters:
        for section in chapter.sections:
            chunks.extend(make_chunk(chunk_id, section.title) for chunk_id in section.primary_material_ids)
    return chunks


class FourChapterPlanner(HeuristicSemanticPlanner):
    def plan_occurrence(self, **kwargs):  # type: ignore[no-untyped-def]
        proposed = super().plan_occurrence(**kwargs)
        chapter = kwargs["source"].chapter_ordinal
        if chapter == 1:
            return replace(proposed, role=LearningRole.INTRO, intended_grants=[MasteryFacet.ORIENTED], intended_contribution="建立点检直觉")
        if chapter == 2:
            return replace(
                proposed,
                role=LearningRole.TEACH,
                required_self_facets=[MasteryFacet.ORIENTED],
                intended_grants=[MasteryFacet.EXPLAIN, MasteryFacet.PERFORM],
                intended_contribution="完整讲授点检步骤与判断依据",
            )
        if chapter == 3:
            return replace(
                proposed,
                role=LearningRole.APPLY,
                required_self_facets=[MasteryFacet.PERFORM],
                intended_grants=[],
                intended_contribution="在当前任务中使用点检方法",
            )
        return replace(
            proposed,
            role=LearningRole.EXTEND,
            required_self_facets=[MasteryFacet.EXPLAIN, MasteryFacet.PERFORM],
            intended_grants=[MasteryFacet.ANALYZE],
            intended_extension_keys=["constraint:异常组合"],
            intended_contribution="在异常组合条件下扩展点检判断",
        )


def test_phase1_is_read_only_and_tracks_instructional_availability() -> None:
    plan = make_plan(
        [
            [("设备认识", "设备点检方法")],
            [("点检教学", "设备点检方法")],
            [("现场应用", "设备点检方法")],
            [("任务准备一", "任务准备一"), ("任务准备二", "任务准备二"), ("任务准备三", "任务准备三"), ("异常条件", "设备点检方法")],
        ]
    )
    original = deepcopy(plan)

    result = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan), semantic_planner=FourChapterPlanner(), recall_after_tasks=3)

    assert plan == original
    trajectory = next(item for item in result.trajectories if len(item.occurrence_ids) == 4)
    occurrences = [next(item for item in result.planned_occurrences if item.occurrence_id == occurrence_id) for occurrence_id in trajectory.occurrence_ids]
    assert [item.role for item in occurrences] == ["INTRO", "TEACH", "APPLY", "EXTEND"]
    assert all(not item.required_prerequisites for item in occurrences)
    fourth_snapshot = next(item for item in result.availability_snapshots if item.occurrence_id == occurrences[-1].occurrence_id)
    before = fourth_snapshot.before.availability_by_knowledge[occurrences[-1].knowledge_id]
    assert set(before.available_facets) == {MasteryFacet.ORIENTED, MasteryFacet.EXPLAIN, MasteryFacet.PERFORM}
    assert not any(issue.type == "NO_COGNITIVE_INCREMENT" for issue in result.validation_issues)


def test_phase1_triggers_recall_policy_after_three_intervening_tasks() -> None:
    plan = make_plan(
        [
            [("设备认识", "设备点检方法")],
            [("点检教学", "设备点检方法")],
            [("现场应用", "设备点检方法")],
            [("任务准备一", "任务准备一"), ("任务准备二", "任务准备二"), ("任务准备三", "任务准备三"), ("异常条件", "设备点检方法")],
        ]
    )
    result = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan), semantic_planner=FourChapterPlanner(), recall_after_tasks=3)

    recall_issues = [issue for issue in result.validation_issues if issue.type == "RECALL_POLICY_TRIGGERED"]
    assert len(recall_issues) == 1
    assert recall_issues[0].deterministic_evidence["intervening_task_count"] == 3
    assert recall_issues[0].suggested_future_repair == "ADD_RECALL"


def test_phase1_reports_same_role_same_contribution_as_no_increment() -> None:
    plan = make_plan([[("第一处讲授", "设备点检方法")], [("第二处讲授", "设备点检方法")]])

    result = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan))

    assert any(issue.type == "NO_COGNITIVE_INCREMENT" for issue in result.validation_issues)


def test_phase1_uses_set_inclusion_not_a_linear_mastery_level() -> None:
    plan = make_plan([[("概念说明", "状态概念")], [("执行任务", "执行技能")]])
    initial = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan))
    concept_id = next(point.knowledge_id for point in initial.knowledge_points if point.title == "状态概念")
    skill_id = next(point.knowledge_id for point in initial.knowledge_points if point.title == "执行技能")
    prerequisite = Prerequisite(
        edge_id="edge:concept-to-skill",
        source_knowledge_id=concept_id,
        target_knowledge_id=skill_id,
        required_facets=[MasteryFacet.PERFORM],
        rationale="测试概念讲授不能替代执行教学。",
        confidence=1.0,
    )

    result = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan), prerequisites=[prerequisite])

    assert any(issue.type == "PREREQUISITE_GAP" and issue.knowledge_id == skill_id for issue in result.validation_issues)


def test_phase1_allows_one_source_point_to_decompose_without_dropping_it() -> None:
    plan = make_plan([[("综合说明", "注意力机制原理与应用")]])

    result = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan))

    mapping = result.mappings[0]
    assert mapping.mapping_type == "DECOMPOSED"
    assert len(mapping.canonical_knowledge_ids) == 2


def test_low_confidence_semantic_proposal_does_not_update_availability_or_write_text(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class LowConfidencePlanner(HeuristicSemanticPlanner):
        def plan_occurrence(self, **kwargs):  # type: ignore[no-untyped-def]
            return replace(super().plan_occurrence(**kwargs), planning_confidence=0.2)

    plan = make_plan([[("设备认识", "设备点检方法")]])
    result = analyze_book_knowledge(book_plan=plan, chunks=chunks_for(plan), semantic_planner=LowConfidencePlanner())
    snapshot = result.availability_snapshots[0]

    assert not snapshot.transition_applied
    assert snapshot.after.availability_by_knowledge == {}
    assert any(issue.type == "SEMANTIC_PLANNING_LOW_CONFIDENCE" for issue in result.validation_issues)
    json_path, markdown_path, mapping_path = write_knowledge_map_artifacts(result, tmp_path)
    assert json_path.exists() and markdown_path.exists() and mapping_path.exists()
    assert "学习轨迹审计" in markdown_path.read_text(encoding="utf-8")

def test_supporting_background_prerequisite_is_audit_context_not_a_transition_blocker() -> None:
    occurrence = PlannedOccurrence(
        occurrence_id="occ:context", knowledge_id="kp:current", source_knowledge_point_id="source:current",
        position=BookPosition(1, 1, 1), chapter_id="chapter_01", section_id="section_01", context_title="fixture",
        source_chunk_ids=[], role=LearningRole.TEACH,
        required_prerequisites=[PrerequisiteUse(
            knowledge_id="kp:future-context", required_facets=[MasteryFacet.EXPLAIN],
            relation="SUPPORTING", use_type="BACKGROUND",
        )],
        intended_grants=[MasteryFacet.EXPLAIN], trusted_for_state=True,
    )
    [snapshot] = simulate_planned_instructional_availability([occurrence])
    assert snapshot.cross_requirements_available is True
    assert snapshot.transition_applied is True
