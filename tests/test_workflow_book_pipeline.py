from __future__ import annotations

import json
from pathlib import Path

from materials2textbook.io_utils import write_jsonl
from materials2textbook.llm.cache import CachingLLMProvider
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.orchestrator import TextbookWorkflow


class FakeProvider:
    def generate(self, messages: list[dict[str, str]]) -> str:
        return "unused"


def test_book_mode_runs_chapters_as_independent_pipeline(tmp_path: Path) -> None:
    video_path = tmp_path / "videos.jsonl"
    document_path = tmp_path / "documents.jsonl"
    output_dir = tmp_path / "agent_workflow"
    chapter_root = tmp_path / "chapter_runs"
    write_jsonl(video_path, [])
    write_jsonl(
        document_path,
        [
            {
                "segment_id": "D1",
                "asset_id": "A1",
                "title": "焊接安全检查",
                "knowledge_point": "焊接安全检查",
                "evidence_text": "焊接前需要检查设备连接、绝缘状态和防护用品。",
                "summary": "焊接安全检查摘要",
                "recommended_chapter": "焊接安全与设备基础",
                "material_block": "焊接安全与设备基础",
                "source_type": "document_segment",
                "review_status": "approved",
                "teaching_value": 0.9,
            },
            {
                "segment_id": "D2",
                "asset_id": "A2",
                "title": "钨极氩弧焊送丝",
                "knowledge_point": "钨极氩弧焊送丝",
                "evidence_text": "送丝时应观察熔池形态，使焊丝端部稳定送入熔池。",
                "summary": "钨极氩弧焊送丝摘要",
                "recommended_chapter": "钨极氩弧焊",
                "material_block": "钨极氩弧焊",
                "source_type": "document_segment",
                "review_status": "approved",
                "teaching_value": 0.9,
            },
        ],
    )

    provider = CachingLLMProvider(FakeProvider(), tmp_path / "global_llm_cache.json")
    outputs = TextbookWorkflow(llm_provider=provider, use_llm=False).run(
        video_segments_path=video_path,
        document_segments_path=document_path,
        output_dir=output_dir,
        title="焊接数字教材",
        config=WorkflowConfig(copy_media_assets=False, max_input_tokens=0, review_rounds=1),
        book_mode=True,
        chapter_output_root=chapter_root,
        max_chapter_input_tokens=8000,
        resume_chapters=True,
    )

    manifest = json.loads(Path(outputs.manifest_path).read_text(encoding="utf-8"))
    book = json.loads(Path(outputs.digital_book_path).read_text(encoding="utf-8"))

    assert manifest["summary"]["chapter_pipeline_enabled"] is True
    assert manifest["summary"]["chapter_pipeline_total"] == 2
    assert manifest["summary"]["chapter_pipeline_completed"] == 2
    assert manifest["summary"]["chapter_pipeline_failed"] == 0
    assert len(manifest["chapter_runs"]) == 2
    assert all((Path(run["chapter_dir"]) / "chapter_status.json").exists() for run in manifest["chapter_runs"])
    assert all((Path(run["chapter_dir"]) / "textbook_final.md").exists() for run in manifest["chapter_runs"])
    assert all(Path(run["llm_cache_path"]).name == "llm_cache.json" for run in manifest["chapter_runs"])
    assert provider.cache_path == tmp_path / "global_llm_cache.json"
    assert len(book["projects"]) == 2
    assert book["metadata"]["book_plan"]["planned_chapter_count"] == 2
    assert book["metadata"]["book_plan"]["generated_chapter_count"] == 2
    assert Path(outputs.final_path).read_text(encoding="utf-8").startswith("# 焊接数字教材")


def test_book_mode_reuses_completed_chapter_outputs(tmp_path: Path) -> None:
    video_path = tmp_path / "videos.jsonl"
    document_path = tmp_path / "documents.jsonl"
    output_dir = tmp_path / "agent_workflow"
    chapter_root = tmp_path / "chapter_runs"
    write_jsonl(video_path, [])
    write_jsonl(
        document_path,
        [
            {
                "segment_id": "D1",
                "asset_id": "A1",
                "title": "焊接安全检查",
                "knowledge_point": "焊接安全检查",
                "evidence_text": "焊接前需要检查设备连接、绝缘状态和防护用品。",
                "recommended_chapter": "焊接安全与设备基础",
                "material_block": "焊接安全与设备基础",
                "source_type": "document_segment",
                "review_status": "approved",
                "teaching_value": 0.9,
            }
        ],
    )

    workflow = TextbookWorkflow()
    first = workflow.run(
        video_segments_path=video_path,
        document_segments_path=document_path,
        output_dir=output_dir,
        title="焊接数字教材",
        config=WorkflowConfig(copy_media_assets=False, review_rounds=1),
        book_mode=True,
        chapter_output_root=chapter_root,
        resume_chapters=True,
    )
    first_manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))
    second = workflow.run(
        video_segments_path=video_path,
        document_segments_path=document_path,
        output_dir=output_dir,
        title="焊接数字教材",
        config=WorkflowConfig(copy_media_assets=False, review_rounds=1),
        book_mode=True,
        chapter_output_root=chapter_root,
        resume_chapters=True,
    )

    second_manifest = json.loads(Path(second.manifest_path).read_text(encoding="utf-8"))

    assert first_manifest["chapter_runs"][0]["status"] == "success"
    assert second_manifest["chapter_runs"][0]["status"] == "reused"
