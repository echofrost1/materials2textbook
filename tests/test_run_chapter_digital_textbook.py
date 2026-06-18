from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_chapter_digital_textbook.py"
SPEC = importlib.util.spec_from_file_location("run_chapter_digital_textbook", SCRIPT_PATH)
assert SPEC and SPEC.loader
chapter_runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chapter_runner)


def test_resolve_chapter_uses_stable_code_without_chinese_cli() -> None:
    assert chapter_runner.resolve_chapter("tig_welding", "") == ("tig_welding", "钨极氩弧焊")
    assert chapter_runner.resolve_chapter("custom_code", "自定义章节") == ("custom_code", "自定义章节")


def test_build_writer_inputs_from_pack_filters_not_ready(tmp_path: Path) -> None:
    pack_path = tmp_path / "chapter_evidence_pack.jsonl"
    rows = [
        {
            "chapter": "钨极氩弧焊",
            "knowledge_point": "基本原理",
            "chunk_id": "C000001",
            "asset_id": "A1",
            "source_type": "video",
            "title": "基本原理",
            "summary": "高频引弧",
            "evidence_text": "观察电弧建立过程。",
            "source_path": "videos/demo.mp4",
            "start_time": "00:00:01",
            "end_time": "00:00:05",
            "image_paths": "frames/one.jpg",
            "review_status": "Agent_Keep",
            "teaching_value": 0.9,
            "confidence": 0.8,
            "quality_gate_status": "pass",
            "evidence_role": "primary",
            "tags": "引弧",
        },
        {
            "chapter": "钨极氩弧焊",
            "knowledge_point": "打底焊",
            "chunk_id": "REF000001",
            "asset_id": "R1",
            "source_type": "reference_text",
            "title": "打底焊",
            "summary": "证据不足",
            "evidence_text": "只有一句参考文本。",
            "source_path": "refs/book.txt",
            "review_status": "Agent_Keep",
            "quality_gate_status": "pass",
            "evidence_role": "reference",
        },
        {
            "chapter": "钨极氩弧焊",
            "knowledge_point": "基本原理",
            "chunk_id": "PPT000001",
            "asset_id": "P1",
            "source_type": "ppt",
            "title": "设备组成",
            "summary": "PPT 支撑",
            "evidence_text": "钨极和氩气保护。",
            "source_path": "slides/demo.pptx",
            "page_or_slide": 3,
            "image_paths": "slides/3.png",
            "review_status": "Pending_Agent_Review",
            "quality_gate_status": "weak",
            "evidence_role": "support",
        },
    ]
    with pack_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    readiness_rows = [
        {"knowledge_point": "基本原理", "readiness_status": "ready"},
        {"knowledge_point": "打底焊", "readiness_status": "not_ready"},
    ]

    video_path, ppt_path, document_path, selected = chapter_runner.build_writer_inputs_from_pack(
        pack_path=pack_path,
        readiness_rows=readiness_rows,
        output_root=tmp_path / "writer_inputs",
    )

    assert selected == 2
    assert _jsonl_count(video_path) == 1
    assert _jsonl_count(ppt_path) == 1
    assert _jsonl_count(document_path) == 0
    assert "打底焊" not in document_path.read_text(encoding="utf-8")


def test_build_writer_inputs_accepts_case_and_source_type_variants(tmp_path: Path) -> None:
    pack_path = tmp_path / "chapter_evidence_pack.jsonl"
    rows = [
        {
            "chapter": "钨极氩弧焊",
            "knowledge_point": "基本原理",
            "chunk_id": "C000001",
            "asset_id": "A1",
            "source_type": "video_segment",
            "title": "基本原理",
            "evidence_text": "观察电弧建立过程。",
            "source_path": "videos/demo.mp4",
            "review_status": "Agent_Keep",
            "quality_gate_status": "PASS",
            "evidence_role": "Primary",
        },
        {
            "chapter": "钨极氩弧焊",
            "knowledge_point": "基本原理",
            "chunk_id": "PPT000001",
            "asset_id": "P1",
            "source_type": "ppt_slide",
            "title": "设备组成",
            "evidence_text": "钨极和氩气保护。",
            "source_path": "slides/demo.pptx",
            "review_status": "Pending_Agent_Review",
            "quality_gate_status": "WEAK",
            "evidence_role": "Support",
        },
    ]
    _write_jsonl(pack_path, rows)
    readiness_rows = [{"knowledge_point": "基本原理", "readiness_status": "Ready"}]

    video_path, ppt_path, document_path, selected = chapter_runner.build_writer_inputs_from_pack(
        pack_path=pack_path,
        readiness_rows=readiness_rows,
        output_root=tmp_path / "writer_inputs",
    )

    assert selected == 2
    assert _jsonl_count(video_path) == 1
    assert _jsonl_count(ppt_path) == 1
    assert _jsonl_count(document_path) == 0


def test_build_gap_log_marks_partial_and_not_ready() -> None:
    rows = chapter_runner.build_gap_log(
        "钨极氩弧焊",
        [
            {"knowledge_point": "基本原理", "readiness_status": "ready"},
            {
                "knowledge_point": "送丝操作",
                "readiness_status": "partial_ready",
                "readiness_reason": "missing reference text",
                "pass_count": 2,
                "weak_count": 1,
            },
            {
                "knowledge_point": "盖面焊",
                "readiness_status": "not_ready",
                "readiness_reason": "no usable evidence",
            },
        ],
    )

    assert [row["knowledge_point"] for row in rows] == ["送丝操作", "盖面焊"]
    assert rows[0]["status"] == "monitor"
    assert rows[1]["status"] == "open"
    assert rows[1]["assigned_to"] == "role_a_material_library"


def test_build_generation_report_reads_workflow_and_inputs(tmp_path: Path) -> None:
    chapter_root = tmp_path / "chapter_work" / "tig_welding"
    output_dir = chapter_root / "agent_workflow"
    writer_inputs = chapter_root / "writer_inputs"
    output_dir.mkdir(parents=True)
    writer_inputs.mkdir(parents=True)
    (output_dir / "textbook_final.md").write_text("# 样章\n\n教材正文 证据：C1\n", encoding="utf-8")
    (output_dir / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "summary": {
                    "writer_generation_mode": "rule_fallback",
                    "writer_generation_warning": "LLM output was empty",
                    "evidence_chunks": 3,
                    "knowledge_points": 2,
                    "citation_coverage_rate": 1.0,
                    "paragraph_support_rate": 0.5,
                    "claim_support_rate": 0.4,
                    "fact_issue_count": 1,
                    "overall_quality_score": 0.75,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (output_dir / "llm_cache.json").write_text(json.dumps({"a": "b", "c": "d"}), encoding="utf-8")
    _write_jsonl(writer_inputs / "chapter_video_segments.from_pack.jsonl", [{"id": "v1"}, {"id": "v2"}])
    _write_jsonl(writer_inputs / "chapter_ppt_assets.from_pack.jsonl", [{"id": "p1"}])
    _write_jsonl(writer_inputs / "chapter_document_segments.from_pack.jsonl", [])

    report = chapter_runner.build_generation_report(
        chapter_code="tig_welding",
        chapter_title="钨极氩弧焊",
        chapter_root=chapter_root,
        output_dir=output_dir,
        writer_input_root=writer_inputs,
        gap_log_rows=[{"gap_type": "partial_ready"}, {"gap_type": "not_ready"}],
        use_llm_requested=True,
        output_mode="preview_only",
    )
    markdown = chapter_runner.render_generation_report_markdown(report)

    assert report["writer_generation_mode"] == "rule_fallback"
    assert report["llm_cache_entries"] == 2
    assert report["writer_input_counts"]["video_segments"] == 2
    assert report["gap_log_by_type"] == {"partial_ready": 1, "not_ready": 1}
    assert not report["contains_learning_path_heading"]
    assert "citation_coverage_rate" in markdown
    assert "LLM output was empty" in markdown


def _jsonl_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
