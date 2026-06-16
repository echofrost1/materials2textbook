from pathlib import Path

from materials2textbook.exporters.digital_book import export_digital_book
from materials2textbook.schemas import CaseExample, ChapterPlan, EvidenceChunk, EvidenceLocator, EvidenceScore, KnowledgePoint


def test_export_digital_book_writes_json_viewer_and_assets(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    video_path = source_dir / "demo.mp4"
    poster_path = source_dir / "frame.jpg"
    video_path.write_bytes(b"video")
    poster_path.write_bytes(b"jpg")

    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝",
        content="送丝操作证据",
        summary="送丝摘要",
        keywords=["送丝", "操作"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(path=str(video_path), keyframe_paths=[str(poster_path)]),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
        metadata={"source_video": "demo.mp4", "start_time": "00:00:01", "end_time": "00:00:03"},
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝操作"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C1"], "送丝摘要")],
        evidence_chunk_ids=["C1"],
        case_examples=[
            CaseExample(
                "case_01",
                "送丝示例",
                "如何观察送丝？",
                "引用 C1 说明。",
                target_knowledge_point_ids=["kp_01"],
                evidence_chunk_ids=["C1"],
            )
        ],
    )

    book, json_path, index_path = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    assert book.title == "样章"
    assert json_path.exists()
    assert index_path.exists()
    assert (tmp_path / "digital_book" / "app.js").exists()
    assert (tmp_path / "digital_book" / "ask_config.js").exists()
    app_js = (tmp_path / "digital_book" / "app.js").read_text(encoding="utf-8")
    assert "localStorage" in app_js
    assert "bookmarkCurrent" in app_js
    assert "readerNote" in app_js
    assert "progressBar" in app_js
    assert "askInput" in app_js
    assert "buildAskIndex" in app_js
    assert "answerQuestion" in app_js
    assert "answerWithRemoteService" in app_js
    assert "materials2textbook.ask_book.v1" in app_js
    assert "exportStudyData" in app_js
    assert "syncStudyDataToEndpoint" in app_js
    assert "importStudyDataFile" in app_js
    assert "window.studyDataApi" in app_js
    assert "materials2textbook.study_data.v1" in app_js
    assert "ask_config.js" in index_path.read_text(encoding="utf-8")
    ask_config = (tmp_path / "digital_book" / "ask_config.js").read_text(encoding="utf-8")
    assert "DIGITAL_BOOK_ASK_ENDPOINT" in ask_config
    assert "DIGITAL_BOOK_STUDY_ENDPOINT" in ask_config
    assert "syncStudyData" in index_path.read_text(encoding="utf-8")
    assert "syncStatus" in index_path.read_text(encoding="utf-8")
    assert (tmp_path / "digital_book" / "assets" / "videos" / "demo.mp4").exists()
    assert (tmp_path / "digital_book" / "assets" / "keyframes" / "frame.jpg").exists()
    assert "assets/videos/demo.mp4" in json_path.read_text(encoding="utf-8")
    assert any(block.type == "case_example" for block in book.projects[0].tasks[0].blocks)
    assert "证据" in index_path.read_text(encoding="utf-8") or "数字教材" in index_path.read_text(encoding="utf-8")


def test_export_digital_book_finds_work_materials_converted_video(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    converted_dir = tmp_path / "work_materials" / "work_material1" / "02_working_processing" / "converted_mp4"
    converted_dir.mkdir(parents=True)
    (converted_dir / "A100_demo.mp4").write_bytes(b"video")
    chunk = EvidenceChunk(
        chunk_id="C100",
        asset_id="A100",
        title="demo",
        content="demo evidence",
        summary="",
        keywords=["demo"],
        subject="",
        material_block="demo",
        material_block_code="demo",
        recommended_chapter="demo",
        locator=EvidenceLocator(path="demo.flv"),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="demo",
        learning_goals=["demo"],
        knowledge_points=[KnowledgePoint("kp_01", "demo", ["C100"])],
        evidence_chunk_ids=["C100"],
    )

    book, _, _ = export_digital_book(
        title="demo",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    assert book.assets["videos"]
    assert (tmp_path / "digital_book" / "assets" / "videos" / "A100_demo.mp4").exists()


def test_export_digital_book_can_link_media_without_copying(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    video_path = source_dir / "demo.mp4"
    video_path.write_bytes(b"video")
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="demo",
        content="demo evidence",
        summary="",
        keywords=["demo"],
        subject="",
        material_block="demo",
        material_block_code="demo",
        recommended_chapter="demo",
        locator=EvidenceLocator(path=str(video_path)),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="demo",
        learning_goals=["demo"],
        knowledge_points=[KnowledgePoint("kp_01", "demo", ["C1"])],
        evidence_chunk_ids=["C1"],
    )

    book, json_path, _ = export_digital_book(
        title="demo",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
        copy_media_assets=False,
    )

    assert book.assets["videos"][0]["src"] == "../source/demo.mp4"
    assert not (tmp_path / "digital_book" / "assets" / "videos" / "demo.mp4").exists()
    assert "../source/demo.mp4" in json_path.read_text(encoding="utf-8")


def test_export_digital_book_does_not_treat_document_path_as_video(tmp_path: Path) -> None:
    document_path = tmp_path / "document_segments.jsonl"
    document_path.write_text("{}", encoding="utf-8")
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="DOC1",
        title="焊前安全检查",
        content="焊接前应检查设备。",
        summary="",
        keywords=["安全"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(path=str(document_path)),
        score=EvidenceScore(teaching_value=0.8),
        source_type="markdown",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解安全检查"],
        knowledge_points=[KnowledgePoint("kp_01", "焊前安全检查", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    assert book.assets["videos"] == []
    task_blocks = book.projects[0].tasks[0].blocks
    assert not any(block.type == "video" for block in task_blocks)
