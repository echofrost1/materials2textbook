from pathlib import Path

import json
import subprocess
import sys
import zipfile
import pytest

from materials2textbook.exporters.digital_book import (
    export_digital_book,
    smoke_test_student_package_static_assets,
    smoke_test_student_package_ask,
    validate_student_digital_book_package,
    write_student_digital_book_package,
)
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    CaseExample,
    ChapterPlan,
    EvidenceChunk,
    EvidenceLocator,
    EvidenceScore,
    KnowledgePoint,
)


class FakeLLMProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.messages.append(messages)
        return self.response


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
    assert "sanitizeStudentAnswer" in app_js
    assert "preferAskResults" in app_js
    assert "focusAskTerms" in app_js
    assert "renderMarkdown" in app_js
    assert "renderInlineMarkdown" in app_js
    assert "videoActions" in app_js
    assert "video.pause()" in app_js
    assert "video.preload = 'metadata'" in app_js
    assert "video.playsInline = true" in app_js
    assert "浏览器阻止了自动播放" in app_js
    assert "document.createElement('pre')" not in app_js
    assert ".split(/\\n{2,}/)" in app_js
    assert ".replace(/\\n/g, '<br>')" in app_js
    assert "block.type || block.block_type" in app_js
    assert "[2, 3, 4]" in app_js
    assert "chunkId.toLowerCase() === term" not in app_js
    assert "materials2textbook.ask_book.v1" in app_js
    index_html = index_path.read_text(encoding="utf-8")
    assert "输入知识点或操作问题" in index_html
    assert "证据编号" not in index_html
    assert "exportStudyData" in app_js
    assert "syncStudyDataToEndpoint" in app_js
    assert "importStudyDataFile" in app_js
    assert "window.studyDataApi" in app_js
    assert "materials2textbook.study_data.v1" in app_js
    assert "ask_config.js" in index_path.read_text(encoding="utf-8")
    ask_config = (tmp_path / "digital_book" / "ask_config.js").read_text(encoding="utf-8")
    assert "DIGITAL_BOOK_ASK_ENDPOINT" in ask_config
    assert "DIGITAL_BOOK_STUDY_ENDPOINT" in ask_config
    assert "syncStudyData" in index_html
    assert "syncStatus" in index_html
    assert (tmp_path / "digital_book" / "assets" / "videos" / "demo.mp4").exists()
    assert (tmp_path / "digital_book" / "assets" / "keyframes" / "frame.jpg").exists()
    assert "assets/videos/demo.mp4" in json_path.read_text(encoding="utf-8")
    assert any(block.type == "case_example" for block in book.projects[0].tasks[0].blocks)
    assert "证据定位" not in " ".join(book.projects[0].ability_map)
    assert "人工复核" not in " ".join(book.projects[0].ability_map)
    assert not any(block.type == "learning_nav" for block in book.projects[0].tasks[0].blocks)
    assert "数字教材" in index_html


def test_export_digital_book_embeds_whole_book_plan_for_reader_outline(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="C1",
        title="送丝操作",
        content="送丝操作时应观察熔池形态。",
        summary="送丝操作摘要",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本操作"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝操作", ["C1"], order_index=1)],
        evidence_chunk_ids=["C1"],
    )
    book_plan = BookPlan(
        book_id="sample",
        title="样书",
        planning_strategy="manifest_xlsx_first",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter_01",
                chapter_no=1,
                title="基本操作",
                learning_goals=["理解基本操作"],
                sections=[
                    BookSectionPlan(
                        section_id="chapter_01_section_01",
                        section_no="1.1",
                        title="送丝",
                        knowledge_point_ids=["送丝操作"],
                        primary_material_ids=["C1"],
                    )
                ],
                primary_material_ids=["C1"],
            )
        ],
    )

    book, json_path, _ = export_digital_book(
        title="样书",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
        book_plan=book_plan,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    app_js = (tmp_path / "digital_book" / "app.js").read_text(encoding="utf-8")
    assert payload["metadata"]["book_plan"]["chapters"][0]["sections"][0]["section_no"] == "1.1"
    assert book.projects[0].title == "第1章 基本操作"
    assert book.projects[0].project_id == "chapter_01"
    assert "renderBookOutline" in app_js
    assert "教材大纲" in app_js
    assert "tocChapter" in app_js
    assert "toc-chapter-toggle" in app_js
    assert "toc-section-list" in app_js
    assert "collapsed" in app_js
    assert "button.dataset.target === id" in app_js
    assert "层级：" not in json_path.read_text(encoding="utf-8")
    assert "先修：" not in json_path.read_text(encoding="utf-8")


def test_export_digital_book_splits_chapter_into_section_tasks(tmp_path: Path) -> None:
    chunks = [
        EvidenceChunk(
            chunk_id="C1",
            asset_id="C1",
            title="基本原理",
            content="基本原理内容",
            summary="基本原理摘要",
            keywords=["基本原理"],
            subject="焊接技术",
            material_block="钨极氩弧焊",
            material_block_code="tig_welding",
            recommended_chapter="钨极氩弧焊",
            locator=EvidenceLocator(),
            score=EvidenceScore(teaching_value=0.8),
            review_status="approved",
        ),
        EvidenceChunk(
            chunk_id="C2",
            asset_id="C2",
            title="送丝操作",
            content="送丝操作内容",
            summary="送丝操作摘要",
            keywords=["送丝"],
            subject="焊接技术",
            material_block="钨极氩弧焊",
            material_block_code="tig_welding",
            recommended_chapter="钨极氩弧焊",
            locator=EvidenceLocator(),
            score=EvidenceScore(teaching_value=0.8),
            review_status="approved",
        ),
    ]
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊",
        learning_goals=["理解钨极氩弧焊"],
        knowledge_points=[
            KnowledgePoint("kp_01", "基本原理", ["C1"], order_index=1),
            KnowledgePoint("kp_02", "送丝操作", ["C2"], order_index=2),
        ],
        evidence_chunk_ids=["C1", "C2"],
    )
    book_plan = BookPlan(
        book_id="sample",
        title="样书",
        planning_strategy="manifest_xlsx_first",
        chapters=[
            BookChapterPlan(
                chapter_id="chapter_01",
                chapter_no=1,
                title="钨极氩弧焊",
                learning_goals=["理解钨极氩弧焊"],
                sections=[
                    BookSectionPlan(
                        section_id="chapter_01_section_01",
                        section_no="1.1",
                        title="基本原理",
                        knowledge_point_ids=["基本原理"],
                        primary_material_ids=["C1"],
                    ),
                    BookSectionPlan(
                        section_id="chapter_01_section_02",
                        section_no="1.2",
                        title="送丝操作",
                        knowledge_point_ids=["送丝操作"],
                        primary_material_ids=["C2"],
                    ),
                ],
                primary_material_ids=["C1", "C2"],
            )
        ],
    )

    book, _, _ = export_digital_book(
        title="样书",
        plans=[plan],
        chunks=chunks,
        output_dir=tmp_path / "digital_book",
        book_plan=book_plan,
    )

    tasks = book.projects[0].tasks
    assert [task.title for task in tasks] == ["1.1 基本原理", "1.2 送丝操作"]
    assert [task.knowledge_points for task in tasks] == [["基本原理"], ["送丝操作"]]
    assert tasks[0].evidence_chunk_ids == ["C1"]
    assert tasks[1].evidence_chunk_ids == ["C2"]


def test_export_digital_book_default_student_copy_is_readable_utf8(tmp_path: Path) -> None:
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="钨极氩弧焊基本操作",
        learning_goals=["理解钨极氩弧焊基本操作"],
        knowledge_points=[],
        evidence_chunk_ids=[],
    )

    book, _, index_path = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[],
        output_dir=tmp_path / "digital_book",
    )

    project = book.projects[0]
    task = project.tasks[0]
    visible_text = "\n".join(
        [
            project.title,
            project.project_intro,
            *project.ability_map,
            task.title,
            *[block.title for block in task.blocks],
            *[block.markdown for block in task.blocks],
        ]
    )
    assert "第1章 钨极氩弧焊基本操作" in visible_text
    assert "本章围绕“钨极氩弧焊基本操作”展开学习" in visible_text
    assert "情境导入" in visible_text
    assert "学习路径" not in visible_text
    assert "示范观察与要点提取" in visible_text
    assert "数字教材" in index_path.read_text(encoding="utf-8")
    assert not any(token in visible_text for token in ["鎯", "瀛", "璇", "閽", "鏁", "鈥"])


def test_write_student_digital_book_package_strips_teacher_traces(tmp_path: Path) -> None:
    source_dir = tmp_path / "digital_book"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (source_dir / "app.js").write_text("block.type || block.block_type; [2, 3, 4];", encoding="utf-8")
    (source_dir / "styles.css").write_text("", encoding="utf-8")
    (source_dir / "ask_config.js").write_text("", encoding="utf-8")
    (source_dir / "digital_book_review.json").write_text("{}", encoding="utf-8")
    (source_dir / "digital_book_review.md").write_text("review", encoding="utf-8")
    (source_dir / "digital_book.json").write_text(
        json.dumps(
            {
                "book_id": "book",
                "title": "样章",
                "metadata": {"format": "materials2textbook.digital_book.v1"},
                "projects": [
                    {
                        "project_id": "project_01",
                        "title": "项目1",
                        "project_intro": "intro",
                        "ability_map": [],
                        "learning_goals": [],
                        "tasks": [
                            {
                                "task_id": "task_01",
                                "title": "任务1",
                                "evidence_chunk_ids": ["C000001"],
                                "blocks": [
                                    {
                                        "block_id": "v1",
                                        "type": "video",
                                        "title": "送丝 视频片段",
                                        "src": "../converted/demo.mp4",
                                        "poster": "../frames/frame.jpg",
                                    },
                                    {
                                        "block_id": "b1",
                                        "type": "implementation",
                                        "title": "送丝操作要点",
                                        "markdown": "学生正文",
                                        "evidence_chunk_ids": ["C000001"],
                                        "metadata": {
                                            "teacher_evidence": [{"chunk_id": "C000001"}],
                                            "student_text_method": "extractive",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "assets": {"videos": [{"src": "assets/videos/demo.mp4", "chunk_id": "C000001"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fallback_zip = tmp_path / "old.zip"
    with zipfile.ZipFile(fallback_zip, "w") as archive:
        archive.writestr("digital_book/assets/videos/demo.mp4", b"video")
        archive.writestr("digital_book/assets/keyframes/frame.jpg", b"jpg")

    output_zip = write_student_digital_book_package(
        source_dir=source_dir,
        output_zip=tmp_path / "student.zip",
        asset_fallback_zip=fallback_zip,
    )

    with zipfile.ZipFile(output_zip) as archive:
        names = archive.namelist()
        packaged_json = archive.read("digital_book/digital_book.json").decode("utf-8")
    assert "digital_book/digital_book_review.json" not in names
    assert "digital_book/digital_book_review.md" not in names
    assert "digital_book/assets/videos/demo.mp4" in names
    assert "teacher_evidence" not in packaged_json
    assert "evidence_chunk_ids" not in packaged_json
    assert "C000001" not in packaged_json
    assert "chunk_id" not in packaged_json
    assert "assets/videos/demo.mp4" in packaged_json
    assert "assets/keyframes/frame.jpg" in packaged_json
    assert "../converted/demo.mp4" not in packaged_json
    assert validate_student_digital_book_package(output_zip) == []


def test_student_package_rejects_unsafe_asset_fallback_zip_paths(tmp_path: Path) -> None:
    source_dir = tmp_path / "digital_book"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (source_dir / "app.js").write_text("block.type || block.block_type; [2, 3, 4];", encoding="utf-8")
    (source_dir / "styles.css").write_text("", encoding="utf-8")
    (source_dir / "ask_config.js").write_text("", encoding="utf-8")
    (source_dir / "digital_book.json").write_text(
        json.dumps({"book_id": "book", "title": "样章", "projects": [], "assets": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    fallback_zip = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(fallback_zip, "w") as archive:
        archive.writestr("digital_book/assets/../../outside.mp4", b"bad")

    with pytest.raises(ValueError, match="Unsafe asset path"):
        write_student_digital_book_package(
            source_dir=source_dir,
            output_zip=tmp_path / "student.zip",
            asset_fallback_zip=fallback_zip,
        )


def test_student_package_rewrites_media_refs_with_same_basename_by_media_kind(tmp_path: Path) -> None:
    source_dir = tmp_path / "digital_book"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (source_dir / "app.js").write_text("block.type || block.block_type; [2, 3, 4];", encoding="utf-8")
    (source_dir / "styles.css").write_text("", encoding="utf-8")
    (source_dir / "ask_config.js").write_text("", encoding="utf-8")
    (source_dir / "digital_book.json").write_text(
        json.dumps(
            {
                "book_id": "book",
                "title": "样章",
                "projects": [
                    {
                        "title": "项目1",
                        "project_intro": "",
                        "ability_map": [],
                        "learning_goals": [],
                        "tasks": [
                            {
                                "title": "任务1",
                                "blocks": [
                                    {
                                        "type": "video",
                                        "title": "视频片段",
                                        "src": "../converted/shared.dat",
                                        "poster": "../frames/shared.dat",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "assets": {"videos": [{"src": "../converted/shared.dat", "poster": "../frames/shared.dat"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fallback_zip = tmp_path / "old.zip"
    with zipfile.ZipFile(fallback_zip, "w") as archive:
        archive.writestr("digital_book/assets/videos/shared.dat", b"video")
        archive.writestr("digital_book/assets/keyframes/shared.dat", b"frame")

    output_zip = write_student_digital_book_package(
        source_dir=source_dir,
        output_zip=tmp_path / "student.zip",
        asset_fallback_zip=fallback_zip,
    )

    with zipfile.ZipFile(output_zip) as archive:
        book = json.loads(archive.read("digital_book/digital_book.json").decode("utf-8"))
    block = book["projects"][0]["tasks"][0]["blocks"][0]
    asset = book["assets"]["videos"][0]
    assert block["src"] == "assets/videos/shared.dat"
    assert block["poster"] == "assets/keyframes/shared.dat"
    assert asset["src"] == "assets/videos/shared.dat"
    assert asset["poster"] == "assets/keyframes/shared.dat"


def test_validate_student_digital_book_package_reports_unsafe_paths(tmp_path: Path) -> None:
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "block.type || block.block_type; [2, 3, 4];")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/assets/videos/demo.mp4", b"video")
        archive.writestr("digital_book/assets/../../outside.mp4", b"bad")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps({"book_id": "book", "title": "样章", "projects": [], "assets": {}}, ensure_ascii=False),
        )

    issues = validate_student_digital_book_package(package)

    assert any("Unsafe zip member paths" in issue for issue in issues)


def test_validate_student_digital_book_package_reports_duplicate_members(tmp_path: Path) -> None:
    package = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "block.type || block.block_type; [2, 3, 4];")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/assets/videos/demo.mp4", b"video")
        archive.writestr("digital_book/digital_book.json", '{"projects":[]}')
        archive.writestr("digital_book/digital_book.json", '{"projects":[]}')

    issues = validate_student_digital_book_package(package)

    assert any("Duplicate zip member paths" in issue for issue in issues)


def test_validate_student_digital_book_package_reports_size_and_asset_limits(tmp_path: Path) -> None:
    package = tmp_path / "large.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "block.type || block.block_type; [2, 3, 4];")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/assets/videos/demo1.mp4", b"video")
        archive.writestr("digital_book/assets/videos/demo2.mp4", b"video")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps({"book_id": "book", "title": "样章", "projects": [], "assets": {}}, ensure_ascii=False),
        )

    issues = validate_student_digital_book_package(
        package,
        max_package_bytes=1,
        max_asset_files=1,
    )

    assert any("Package is too large" in issue for issue in issues)
    assert any("Too many packaged media assets" in issue for issue in issues)


def test_validate_student_digital_book_package_reports_external_media_refs(tmp_path: Path) -> None:
    package = tmp_path / "external_refs.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "block.type || block.block_type; [2, 3, 4];")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/assets/videos/demo.mp4", b"video")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps(
                {
                    "book_id": "book",
                    "title": "样章",
                    "projects": [
                        {
                            "title": "项目1",
                            "project_intro": "",
                            "ability_map": [],
                            "learning_goals": [],
                            "tasks": [
                                {
                                    "title": "任务1",
                                    "blocks": [
                                        {
                                            "type": "video",
                                            "title": "视频片段",
                                            "src": "../../02_working_processing/demo.mp4",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "assets": {"videos": [{"src": "assets/videos/missing.mp4"}]},
                },
                ensure_ascii=False,
            ),
        )

    issues = validate_student_digital_book_package(package)

    assert any("must stay under assets/" in issue for issue in issues)
    assert any("missing from zip" in issue for issue in issues)


def test_validate_student_digital_book_package_reports_release_blockers(tmp_path: Path) -> None:
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "const oldAsk = true;")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/digital_book_review.md", "review")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps(
                {
                    "book_id": "book",
                    "title": "样章",
                    "metadata": {},
                    "projects": [
                        {
                            "title": "项目1",
                            "project_intro": "证据：C000001",
                            "ability_map": [],
                            "learning_goals": [],
                            "tasks": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

    issues = validate_student_digital_book_package(package)

    assert any("Teacher review files" in issue for issue in issues)
    assert any("No packaged media assets" in issue for issue in issues)
    assert any("Student JSON contains internal terms" in issue for issue in issues)
    assert any("Student-visible text contains internal terms" in issue for issue in issues)
    assert any("block type compatibility" in issue for issue in issues)
    assert any("Chinese short-term splitting" in issue for issue in issues)


def test_smoke_test_student_package_ask_checks_packaged_local_retrieval(tmp_path: Path) -> None:
    package = tmp_path / "book.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "block.type || block.block_type; [2, 3, 4];")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/assets/videos/demo.mp4", b"video")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps(
                {
                    "book_id": "book",
                    "title": "样章",
                    "metadata": {},
                    "projects": [
                        {
                            "title": "项目1 钨极氩弧焊",
                            "project_intro": "",
                            "ability_map": [],
                            "learning_goals": [],
                            "tasks": [
                                {
                                    "title": "任务1.1 钨极氩弧焊",
                                    "blocks": [
                                        {
                                            "type": "learning_nav",
                                            "title": "学习路径",
                                            "items": ["1. 送丝操作要点（层级：实操）"],
                                        },
                                        {
                                            "type": "implementation",
                                            "title": "送丝操作要点",
                                            "markdown": "送丝操作需要保持焊丝角度稳定，并观察熔池变化。",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                    "assets": {"videos": [{"src": "assets/videos/demo.mp4"}]},
                },
                ensure_ascii=False,
            ),
        )

    assert (
        smoke_test_student_package_ask(
            package,
            question="送丝操作要注意什么",
            expected_terms=["送丝操作要点", "熔池"],
            forbidden_terms=["学习路径", "C000"],
        )
        == []
    )
    issues = smoke_test_student_package_ask(
        package,
        question="送丝操作要注意什么",
        expected_terms=["不存在的术语"],
    )
    assert any("missing expected terms" in issue for issue in issues)


def test_smoke_test_student_package_static_assets_checks_html_and_media_refs(tmp_path: Path) -> None:
    package = tmp_path / "book.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "digital_book/index.html",
            '<link rel="stylesheet" href="styles.css?v=1"><script src="app.js?v=1"></script>',
        )
        archive.writestr("digital_book/app.js", "block.type || block.block_type; [2, 3, 4];")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/assets/videos/demo.mp4", b"video")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps(
                {
                    "book_id": "book",
                    "title": "样章",
                    "projects": [
                        {
                            "title": "项目1",
                            "project_intro": "",
                            "ability_map": [],
                            "learning_goals": [],
                            "tasks": [
                                {
                                    "title": "任务1",
                                    "blocks": [
                                        {"type": "video", "title": "视频片段", "src": "assets/videos/demo.mp4"}
                                    ],
                                }
                            ],
                        }
                    ],
                    "assets": {"videos": [{"src": "assets/videos/demo.mp4"}]},
                },
                ensure_ascii=False,
            ),
        )

    assert smoke_test_student_package_static_assets(package) == []

    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr(
            "digital_book/index.html",
            '<link rel="stylesheet" href="missing.css"><script src="app.js"></script>',
        )
        archive.writestr("digital_book/app.js", "")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/digital_book.json", '{"projects":[]}')
    issues = smoke_test_student_package_static_assets(missing)
    assert any("missing HTML asset: missing.css" in issue for issue in issues)


def test_validate_digital_book_package_cli_fails_for_bad_package(tmp_path: Path) -> None:
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("digital_book/index.html", "<html></html>")
        archive.writestr("digital_book/app.js", "const oldAsk = true;")
        archive.writestr("digital_book/styles.css", "")
        archive.writestr("digital_book/ask_config.js", "")
        archive.writestr("digital_book/digital_book_review.md", "review")
        archive.writestr(
            "digital_book/digital_book.json",
            json.dumps({"book_id": "book", "title": "样章", "projects": []}, ensure_ascii=False),
        )

    result = subprocess.run(
        [sys.executable, "scripts/validate_digital_book_package.py", str(package)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "validation failed" in result.stdout
    assert "Teacher review files" in result.stdout


def test_package_digital_book_cli_removes_invalid_output(tmp_path: Path) -> None:
    source_dir = tmp_path / "digital_book"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (source_dir / "app.js").write_text("const oldAsk = true;", encoding="utf-8")
    (source_dir / "styles.css").write_text("", encoding="utf-8")
    (source_dir / "ask_config.js").write_text("", encoding="utf-8")
    (source_dir / "digital_book_review.md").write_text("review", encoding="utf-8")
    (source_dir / "digital_book.json").write_text(
        json.dumps({"book_id": "book", "title": "样章", "projects": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    output_zip = tmp_path / "student.zip"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/package_digital_book.py",
            str(source_dir),
            "--output",
            str(output_zip),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "validation failed" in result.stdout
    assert not output_zip.exists()


def test_package_digital_book_cli_preserves_existing_output_on_validation_failure(tmp_path: Path) -> None:
    source_dir = tmp_path / "digital_book"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (source_dir / "app.js").write_text("const oldAsk = true;", encoding="utf-8")
    (source_dir / "styles.css").write_text("", encoding="utf-8")
    (source_dir / "ask_config.js").write_text("", encoding="utf-8")
    (source_dir / "digital_book.json").write_text(
        json.dumps({"book_id": "book", "title": "样章", "projects": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    output_zip = tmp_path / "student.zip"
    output_zip.write_bytes(b"previous-good-package")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/package_digital_book.py",
            str(source_dir),
            "--output",
            str(output_zip),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "validation failed" in result.stdout
    assert output_zip.read_bytes() == b"previous-good-package"
    assert not (tmp_path / ".student.zip.tmp").exists()


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


def test_student_markdown_hides_evidence_codes_and_sources(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="C000001",
        asset_id="A1",
        title="送丝",
        content="[00:00:01 --> 00:00:03] 送丝时应保持焊丝端部位于气体保护区内，并观察熔池变化。",
        summary="送丝时应保持焊丝端部位于气体保护区内。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(original_path="raw/demo.mp4"),
        score=EvidenceScore(teaching_value=0.8),
        review_status="approved",
        metadata={"source_video": "demo.mp4", "start_time": "00:00:01", "end_time": "00:00:03"},
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C000001"])],
        evidence_chunk_ids=["C000001"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "C000001" not in implementation.markdown
    assert "demo.mp4" not in implementation.markdown
    assert "approved" not in implementation.markdown
    assert "待人工" not in implementation.markdown
    assert "送丝时应保持焊丝端部位于气体保护区内" in implementation.markdown
    assert implementation.metadata["teacher_evidence"][0]["chunk_id"] == "C000001"
    assert implementation.metadata["teacher_evidence"][0]["review_status"] == "approved"


def test_student_markdown_skips_obvious_low_quality_asr(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="基本原理",
        content="夫妻娘不怕,有分成四個部份,無幾壓護罕。",
        summary="夫妻娘不怕,有分成四個部份。",
        keywords=["基本原理"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="Pending_Manual_Timecode",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本原理"],
        knowledge_points=[KnowledgePoint("kp_01", "基本原理", ["C1"])],
        evidence_chunk_ids=["C1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "夫妻娘不怕" not in implementation.markdown
    assert "本节围绕“基本原理”展开学习" in implementation.markdown


def test_student_markdown_skips_internal_review_phrasing(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝",
        content="送丝时要根据熔池状态调整焊丝角度。",
        summary="送丝候选片段 1，待人工根据画面和字幕确认边界。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="Pending_Manual_Timecode",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C1"], "送丝候选片段 1，待人工确认边界。")],
        evidence_chunk_ids=["C1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "候选片段" not in implementation.markdown
    assert "待人工" not in implementation.markdown
    assert "确认边界" not in implementation.markdown
    assert "送丝时要根据熔池状态调整焊丝角度" in implementation.markdown


def test_student_markdown_skips_unapproved_video_transcript_content(tmp_path: Path) -> None:
    video_chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="A1",
        title="送丝",
        content="第二回饭,採用左手送司法,一座漢师向前移动,送入龙磁。",
        summary="送丝候选片段 1，待人工确认边界。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="video_segment",
        review_status="Pending_Manual_Timecode",
    )
    document_chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="送丝",
        content="送丝速度应与焊接电流、焊接速度和接头间隙相匹配。",
        summary="送丝速度应与焊接电流、焊接速度和接头间隙相匹配。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="Pending_Agent_Review",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["C1", "D1"])],
        evidence_chunk_ids=["C1", "D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[video_chunk, document_chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "漢师" not in implementation.markdown
    assert "龙磁" not in implementation.markdown
    assert "送丝速度应与焊接电流" in implementation.markdown


def test_student_markdown_removes_file_names_and_low_value_slide_text(tmp_path: Path) -> None:
    noisy_chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="送丝",
        content="7.1 焊前准备 7.2 焊接操作 目录 CONTENTS",
        summary="7.1 焊前准备 7.2 焊接操作 目录 CONTENTS",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="Pending_Agent_Review",
    )
    useful_chunk = EvidenceChunk(
        chunk_id="D2",
        asset_id="D2",
        title="送丝",
        content="焊接操作.mp4 送丝时应观察熔池形态，并保持焊丝处在气体保护区内。送丝讲解.mp3 应作为音频文件名清理。",
        summary="焊接操作.mp4 送丝时应观察熔池形态，并保持焊丝处在气体保护区内。送丝讲解.mp3 应作为音频文件名清理。",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="Pending_Agent_Review",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["D1", "D2"])],
        evidence_chunk_ids=["D1", "D2"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[noisy_chunk, useful_chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "目录" not in implementation.markdown
    assert "CONTENTS" not in implementation.markdown
    assert ".mp4" not in implementation.markdown
    assert ".mp3" not in implementation.markdown
    assert "送丝时应观察熔池形态" in implementation.markdown


def test_student_markdown_skips_off_topic_welding_stress_sentences(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="基本原理",
        content=(
            "钨极氩弧焊采用钨极作为电极，并以氩气作为保护气体。"
            "焊接应力与变形可以采用温差拉伸法和振动时效法处理。"
        ),
        summary="",
        keywords=["基本原理"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解基本原理"],
        knowledge_points=[KnowledgePoint("kp_01", "基本原理", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "钨极氩弧焊采用钨极作为电极" in implementation.markdown
    assert "焊接应力" not in implementation.markdown
    assert "振动时效法" not in implementation.markdown


def test_export_digital_book_limits_repeated_video_blocks_per_point(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    first_video = source_dir / "demo1.mp4"
    second_video = source_dir / "demo2.mp4"
    first_video.write_bytes(b"video")
    second_video.write_bytes(b"video")
    chunks = []
    for index in range(1, 5):
        chunks.append(
            EvidenceChunk(
                chunk_id=f"C{index}",
                asset_id=f"A{index}",
                title="送丝",
                content=f"送丝片段 {index}",
                summary=f"送丝片段 {index}",
                keywords=["送丝"],
                subject="焊接技术",
                material_block="钨极氩弧焊",
                material_block_code="tig_welding",
                recommended_chapter="基本操作",
                locator=EvidenceLocator(path=str(first_video if index <= 3 else second_video)),
                score=EvidenceScore(teaching_value=0.8),
                source_type="video_segment",
                review_status="approved",
            )
        )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", [chunk.chunk_id for chunk in chunks])],
        evidence_chunk_ids=[chunk.chunk_id for chunk in chunks],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=chunks,
        output_dir=tmp_path / "digital_book",
    )

    video_blocks = [
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "video"
    ]
    assert len(video_blocks) == 2
    assert {block.src for block in video_blocks} == {"assets/videos/demo1.mp4", "assets/videos/demo2.mp4"}


def test_student_markdown_uses_textbook_sections_instead_of_raw_long_notes(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="送丝",
        content=(
            "送丝速度应与焊接电流、焊接速度和接头间隙相匹配。"
            "操作时观察熔池形态，将焊丝端部送入熔池。"
            "焊丝应保持在气体保护区内，避免氧化。"
            "如果操作不当，容易出现气孔、夹钨或熔合不良。"
        ),
        summary="",
        keywords=["送丝"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "操作步骤：" not in implementation.markdown
    assert "注意事项：" not in implementation.markdown
    assert "常见问题：" not in implementation.markdown
    assert not any(line.startswith("- ") for line in implementation.markdown.splitlines())
    assert "本节围绕“送丝”展开学习" in implementation.markdown
    assert "送丝速度应与焊接电流、焊接速度和接头间隙相匹配" in implementation.markdown
    assert "操作观察的重点在于" in implementation.markdown
    assert "质量控制和安全操作的重点在于" in implementation.markdown
    assert "气孔、夹钨或熔合不良" in implementation.markdown
    assert all(len(line) <= 240 for line in implementation.markdown.splitlines() if line.strip())


def test_student_markdown_strips_slide_outline_labels(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="焊接参数",
        content=(
            "3. 手工钨极氩弧焊焊接参数 （ 6 ）钨极直径与形状应根据焊接电流选择。"
            "一、安全检查 1. 钨极氩弧焊的原理是利用钨极和工件之间的电弧加热母材。"
            "T1G 焊接时喷嘴与焊件间的距离应保持稳定。"
        ),
        summary="",
        keywords=["焊接参数"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解参数"],
        knowledge_points=[KnowledgePoint("kp_01", "焊接参数", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "3. 手工钨极氩弧焊焊接参数" not in implementation.markdown
    assert "一、安全检查" not in implementation.markdown
    assert "T1G" not in implementation.markdown
    assert "TIG" in implementation.markdown
    assert "钨极直径与形状应根据焊接电流选择" in implementation.markdown


def test_student_markdown_filters_course_labels_and_diagram_captions(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="设备组成",
        content=(
            "1 焊接方法的分类及常用的焊接方法 课程 2-3 焊接基础知识。"
            "钨极氩弧焊的原理、特点及应用 （ 1 ）钨极氩弧焊的 基本原理 TIG 焊原理图 1— 钨极 2— 惰性气体 3— 喷嘴。"
            "手工钨极氩弧焊设备 （ 1 ）手工 TIG 焊设备包括焊接电源、控制系统、引弧装置和焊枪。"
            "操作时观察熔池形态，将焊丝端部送入熔池。"
        ),
        summary="",
        keywords=["设备组成"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解设备"],
        knowledge_points=[KnowledgePoint("kp_01", "设备组成", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "焊接方法的分类" not in implementation.markdown
    assert "原理图" not in implementation.markdown
    assert "包括焊接电源" in implementation.markdown
    assert "1. 包括焊接电源" not in implementation.markdown
    assert "操作观察的重点在于操作时观察熔池形态" in implementation.markdown


def test_student_markdown_filters_short_slide_headings_and_ocr_typos(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="应用范围",
        content=(
            "焊接材料 （ 2 ）钨极。"
            "1 ） 纯钨极 其牌号是 W1、W2，纯度 99.85% 以上。"
            "钨极氩弧焊的原理、特点及应用 （ 3 ）钨极氩弧焊的 应用。"
            "TIG 焊适合焊接不锈钢、高文刚、钛及钛合金。"
            "⑤在清除焊缝或铸件缺陷时，被刨削面光洁铮亮。"
        ),
        summary="",
        keywords=["应用范围"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解应用范围"],
        knowledge_points=[KnowledgePoint("kp_01", "应用范围", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "焊接材料 （ 2 ）钨极" not in implementation.markdown
    assert "钨极氩弧焊的 应用" not in implementation.markdown
    assert "清除焊缝或铸件缺陷" not in implementation.markdown
    assert "高文刚" not in implementation.markdown
    assert "高温钢" in implementation.markdown


def test_student_markdown_renders_textbook_paragraphs_without_internal_traces(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="C000321",
        asset_id="C000321",
        title="送丝操作",
        content=(
            "证据：C000321 来源：PPT_demo.pptx Pending_review。"
            "送丝操作需要与焊接电流、焊接速度和接头间隙相匹配。"
            "操作时应观察熔池形态，使焊丝端部稳定送入熔池。"
            "焊丝应保持在气体保护区内，防止端部氧化。"
            "如果控制不当，容易出现气孔、夹钨或熔合不良。"
        ),
        summary="chunk_id C000321 待人工复核",
        keywords=["送丝操作"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="video_segment",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解送丝操作"],
        knowledge_points=[KnowledgePoint("kp_01", "送丝操作", ["C000321"])],
        evidence_chunk_ids=["C000321"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "送丝操作需要与焊接电流、焊接速度和接头间隙相匹配" in implementation.markdown
    assert "操作时应观察熔池形态" in implementation.markdown
    assert "观看示范视频时" in implementation.markdown
    assert "如果控制不当，容易出现如果控制不当" not in implementation.markdown
    assert "概念说明：" not in implementation.markdown
    assert not any(line.startswith("- ") for line in implementation.markdown.splitlines())
    for forbidden in ["chunk_id", "C000321", "证据：", "来源：", "PPT_", "Pending_", ".pptx"]:
        assert forbidden not in implementation.markdown


def test_student_markdown_uses_cleaned_pending_practice_transcript(tmp_path: Path) -> None:
    chunk = EvidenceChunk(
        chunk_id="C1",
        asset_id="C1",
        title="收弧操作",
        content=(
            "[00:00:12 --> 00:00:22] 漢阶结束时,由于收骨的方法不正确,在汉后结尾处容易产生骨坑和骨坑练纹、气孔、烧穿等确线。"
            "[00:00:30 --> 00:00:38] 如果漢阶带由电流衰竭框框制,收骨时,新疆融持铁碼,然后按动电流衰竭按钮。"
            "[00:00:38 --> 00:00:42] 使汉阶电流逐渐紧小,最后细灭电骨。"
        ),
        summary="收弧操作候选片段 1，待人工根据画面和字幕确认边界。",
        keywords=["收弧操作"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="video_segment",
        review_status="Pending_Manual_Timecode",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解收弧操作"],
        knowledge_points=[KnowledgePoint("kp_01", "收弧操作", ["C1"], difficulty_level="practice")],
        evidence_chunk_ids=["C1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "收弧" in implementation.markdown
    assert "弧坑裂纹" in implementation.markdown
    assert "电流衰减" in implementation.markdown
    assert "电弧" in implementation.markdown
    assert "收骨" not in implementation.markdown
    assert "骨坑" not in implementation.markdown
    assert "框框制" not in implementation.markdown


def test_student_markdown_can_use_llm_polished_text(tmp_path: Path) -> None:
    provider = FakeLLMProvider(
        "钨极氩弧焊利用钨极与工件之间的电弧加热母材，并由氩气保护熔池。学习时需要理解电极材料、保护气体和熔池状态之间的关系。\n\n"
        "操作时应观察熔池形态，保持焊枪角度稳定。钨极伸出长度应结合接头形式和观察范围进行调整。"
    )
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="基本原理",
        content="钨极氩弧焊采用钨丝作为电极材料，并以氩气作为保护气体。操作时观察熔池形态。",
        summary="",
        keywords=["基本原理"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解原理"],
        knowledge_points=[KnowledgePoint("kp_01", "基本原理", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
        llm_provider=provider,
        use_llm=True,
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "利用钨极与工件之间的电弧加热母材" in implementation.markdown
    assert implementation.metadata["student_text_method"] == "llm_polished"
    assert provider.messages
    assert "禁止输出证据编号" in provider.messages[0][0]["content"]
    assert "正式教材自然段" in provider.messages[0][1]["content"]


def test_student_markdown_rejects_llm_internal_traces(tmp_path: Path) -> None:
    provider = FakeLLMProvider("证据：C000123 来源：demo.mp4 chunk_id: C000123\n这是一段学生不应看到的内容。")
    chunk = EvidenceChunk(
        chunk_id="D1",
        asset_id="D1",
        title="基本原理",
        content="钨极氩弧焊采用钨丝作为电极材料，并以氩气作为保护气体。",
        summary="",
        keywords=["基本原理"],
        subject="焊接技术",
        material_block="钨极氩弧焊",
        material_block_code="tig_welding",
        recommended_chapter="基本操作",
        locator=EvidenceLocator(),
        score=EvidenceScore(teaching_value=0.8),
        source_type="ppt_slide",
        review_status="approved",
    )
    plan = ChapterPlan(
        chapter_id="chapter_01",
        title="基本操作",
        learning_goals=["理解原理"],
        knowledge_points=[KnowledgePoint("kp_01", "基本原理", ["D1"])],
        evidence_chunk_ids=["D1"],
    )

    book, _, _ = export_digital_book(
        title="样章",
        plans=[plan],
        chunks=[chunk],
        output_dir=tmp_path / "digital_book",
        llm_provider=provider,
        use_llm=True,
    )

    implementation = next(
        block
        for block in book.projects[0].tasks[0].blocks
        if block.type == "implementation"
    )
    assert "C000123" not in implementation.markdown
    assert "demo.mp4" not in implementation.markdown
    assert "钨极氩弧焊采用钨丝作为电极材料" in implementation.markdown
    assert implementation.metadata["student_text_method"] == "extractive"
    assert implementation.metadata["student_text_polish_status"] == "llm_rejected"
