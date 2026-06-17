from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from materials2textbook.agents.book_planner import (
    BookPlannerAgent,
    book_plan_to_chapter_plans,
    render_curriculum_order_yaml,
    render_book_outline_markdown,
    review_book_plan,
)
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def make_chunk(
    chunk_id: str,
    title: str,
    *,
    chapter: str = "",
    source_type: str = "ppt_slide",
    asset_id: str = "",
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id=asset_id or chunk_id,
        title=title,
        content=f"{title} 内容",
        summary=f"{title} 摘要",
        keywords=[title],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig",
        recommended_chapter=chapter,
        locator=EvidenceLocator(path=f"{asset_id or chunk_id}.mp4" if source_type == "video_segment" else ""),
        score=EvidenceScore(teaching_value=0.8),
        source_type=source_type,
        review_status="approved",
    )


def test_book_planner_prefers_manifest_xlsx_chapter_and_section(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["素材ID", "章节", "小节", "知识点"])
    sheet.append(["C1", "第1章 焊接认知", "1.1 基本原理", "钨极氩弧焊基本原理"])
    workbook.save(manifest)
    chunks = [make_chunk("C1", "错误章节标题", chapter="旧章节")]

    plan = BookPlannerAgent().run(title="样书", chunks=chunks, manifest_xlsx=manifest)

    assert plan.planning_strategy == "manifest_xlsx_first"
    assert plan.chapters[0].title == "第1章 焊接认知"
    assert plan.chapters[0].sections[0].title == "1.1 基本原理"
    assert plan.chapters[0].sections[0].knowledge_point_ids == ["钨极氩弧焊基本原理"]
    assert plan.chapters[0].primary_material_ids == ["C1"]


def test_book_planner_can_auto_plan_from_asset_block_map(tmp_path: Path) -> None:
    manifest = tmp_path / "asset_block_map.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "asset_id",
            "filename",
            "material_block_code",
            "material_block_cn",
            "knowledge_point_code",
            "knowledge_point_cn",
            "confidence",
            "active_for_index",
            "needs_confirmation",
        ]
    )
    sheet.append(["A2", "tig.mp4", "M04", "钨极氩弧焊", "KP02", "送丝", 0.9, "true", "false"])
    sheet.append(["A1", "safe.pptx", "M01", "焊接设备与安全", "KP01", "安全操作", 0.9, "true", "false"])
    workbook.save(manifest)
    chunks = [
        make_chunk("C_tig", "送丝", asset_id="A2", source_type="video_segment"),
        make_chunk("C_safe", "安全操作", asset_id="A1", source_type="ppt_slide"),
    ]

    plan = BookPlannerAgent().run(title="样书", chunks=chunks, manifest_xlsx=manifest)
    yaml = render_curriculum_order_yaml(plan)

    assert [chapter.title for chapter in plan.chapters] == ["焊接设备与安全", "钨极氩弧焊"]
    assert plan.chapters[0].sections[0].title == "安全操作"
    assert plan.chapters[1].sections[0].title == "送丝"
    assert plan.metadata["curriculum_order"][:2] == ["焊接设备与安全", "钨极氩弧焊"]
    assert "curriculum_order_source" in plan.metadata
    assert "chapter_order:" in yaml
    assert "title: 焊接设备与安全" in yaml


def test_book_planner_fills_missing_manifest_fields_from_chunks() -> None:
    chunks = [
        make_chunk("C1", "送丝操作", chapter="基本操作", source_type="video_segment"),
        make_chunk("C2", "收弧操作", chapter="基本操作", source_type="video_segment"),
    ]

    plan = BookPlannerAgent().run(title="样书", chunks=chunks)
    chapter = plan.chapters[0]

    assert chapter.title == "基本操作"
    assert {section.title for section in chapter.sections} == {"送丝操作", "收弧操作"}
    assert set(chapter.primary_material_ids) == {"C1", "C2"}


def test_book_planner_keeps_over_budget_materials_as_references() -> None:
    chunks = [make_chunk(f"C{i}", f"知识点{i}", chapter="大章节", source_type="ppt_slide") for i in range(30)]

    plan = BookPlannerAgent().run(title="样书", chunks=chunks)
    chapter = plan.chapters[0]

    assert len(chapter.primary_material_ids) == 20
    assert len(chapter.reference_material_ids) == 10
    assert set(chapter.primary_material_ids).isdisjoint(chapter.reference_material_ids)


def test_book_plan_can_derive_chapter_plans_and_outline() -> None:
    chunks = [make_chunk("C1", "送丝操作", chapter="基本操作", source_type="video_segment")]
    book_plan = BookPlannerAgent().run(title="样书", chunks=chunks)

    chapter_plans = book_plan_to_chapter_plans(book_plan, chunks)
    outline = render_book_outline_markdown(book_plan)
    issues = review_book_plan(book_plan, chunks)

    assert chapter_plans[0].title == "基本操作"
    assert chapter_plans[0].knowledge_points[0].title == "送丝操作"
    assert "第1章 基本操作" in outline
    assert issues == []
