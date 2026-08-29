from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import inspect
import json
from pathlib import Path

from materials2textbook.agents import book_plan_llm
from materials2textbook.agents.book_plan_llm import expand_tasks_by_material_density
from materials2textbook.knowledge_map import analyze_book_knowledge
from materials2textbook.knowledge_map.outline import book_plan_deep_equal, book_plan_fingerprint
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
)
from materials2textbook.student_display import display_metadata_for_outline_node
from materials2textbook.workflow.orchestrator import TextbookWorkflow


def _chunk(chunk_id: str = "C001") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="A001",
        title="坡口加工与打磨",
        content="坡口加工和打磨的操作素材。",
        summary="坡口加工和打磨。",
        keywords=["坡口", "打磨"],
        review_status="approved",
        subject="焊接",
        material_block="焊接基本操作",
        material_block_code="welding",
        recommended_chapter="焊前准备",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.9),
    )


def _book_plan() -> BookPlan:
    section = BookSectionPlan(
        section_id="chapter_01_section_01",
        section_no="1.1",
        title="坡口加工与打磨",
        knowledge_point_ids=["坡口加工与打磨"],
        primary_material_ids=["C001"],
    )
    chapter = BookChapterPlan(
        chapter_id="chapter_01",
        chapter_no=1,
        title="焊前准备",
        learning_goals=["完成焊前准备。"],
        sections=[section],
        primary_material_ids=["C001"],
    )
    return BookPlan("production-book", "生产教材", "original", [chapter])


def test_original_density_expansion_uses_original_workflow_groups(monkeypatch) -> None:
    plan = _book_plan()
    chunk = _chunk()
    calls: list[tuple[str, list[str]]] = []

    def original_groups(chapter_title: str, chunks: list[EvidenceChunk]):
        calls.append((chapter_title, [item.chunk_id for item in chunks]))
        return [("原始工作流任务", chunks)]

    def ranked_groups(_chunks: list[EvidenceChunk]):
        raise AssertionError("The replacement title-ranked grouping must not run in BookPlan generation.")

    monkeypatch.setattr(book_plan_llm, "_project_workflow_task_groups", original_groups)
    monkeypatch.setattr(book_plan_llm, "_ranked_task_groups", ranked_groups)

    expanded, _ = expand_tasks_by_material_density(plan, [chunk])

    assert calls == [("焊前准备", ["C001"])]
    assert expanded.chapters[0].sections[0].title == "原始工作流任务"


def test_semantic_analysis_is_an_overlay_and_cannot_change_source_book_plan() -> None:
    plan = _book_plan()
    source_snapshot = deepcopy(plan)
    source_fingerprint = book_plan_fingerprint(plan)

    knowledge_map = analyze_book_knowledge(book_plan=plan, chunks=[_chunk()])

    assert book_plan_deep_equal(plan, source_snapshot)
    assert book_plan_fingerprint(plan) == source_fingerprint
    assert len(plan.chapters[0].sections) == 1
    assert plan.chapters[0].sections[0].title == "坡口加工与打磨"
    assert len(knowledge_map.source_knowledge_points) == 1
    assert len(knowledge_map.mappings[0].canonical_knowledge_ids) == 2


def test_display_metadata_is_rendering_overlay_not_book_plan_state() -> None:
    section = _book_plan().chapters[0].sections[0]
    overlay = display_metadata_for_outline_node(
        section.section_id,
        section.title,
        knowledge_titles=section.knowledge_point_ids,
    )

    assert overlay.outline_node_id == section.section_id
    assert overlay.title.display_title == section.title
    assert not hasattr(section, "display_title")
    assert not hasattr(section, "internal_context_title")


def test_production_workflow_cannot_reference_phase2b5_fixture_identity_or_title() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "knowledge_map_gold" / "phase2b5_real_trajectories.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_ids = {item["id"] for item in fixture["trajectories"]}
    production_source = inspect.getsource(TextbookWorkflow)
    production_artifact = json.dumps(asdict(analyze_book_knowledge(book_plan=_book_plan(), chunks=[_chunk()])), ensure_ascii=False)

    for fixture_id in fixture_ids:
        assert fixture_id not in production_source
        assert fixture_id not in production_artifact
