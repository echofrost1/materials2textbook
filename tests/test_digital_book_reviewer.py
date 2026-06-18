from pathlib import Path

from materials2textbook.agents.digital_book_reviewer import (
    DigitalBookReviewerAgent,
    render_digital_book_review_markdown,
)
from materials2textbook.schemas import DigitalBook, DigitalBookBlock, DigitalBookProject, DigitalBookTask


def make_book(video_src: str = "assets/videos/demo.mp4") -> DigitalBook:
    return DigitalBook(
        book_id="demo",
        title="样章",
        metadata={},
        projects=[
            DigitalBookProject(
                project_id="project_01",
                title="项目1",
                project_intro="导学",
                ability_map=["观察"],
                learning_goals=["理解"],
                tasks=[
                    DigitalBookTask(
                        task_id="task_01",
                        title="任务1",
                        knowledge_points=["送丝"],
                        key_terms=["送丝"],
                        evidence_chunk_ids=["C1"],
                        blocks=[
                            DigitalBookBlock("b1", "scenario", "情境导入", markdown="导入", evidence_chunk_ids=["C1"]),
                            DigitalBookBlock("b2", "learning_nav", "学习导航", items=["送丝"], evidence_chunk_ids=["C1"]),
                            DigitalBookBlock(
                                "b3",
                                "implementation",
                                "任务实施",
                                markdown="概念说明：\n- 送丝时应观察熔池状态，并保持焊丝端部处于气体保护区内。",
                                evidence_chunk_ids=["C1"],
                            ),
                            DigitalBookBlock("b4", "video", "视频", src=video_src, poster="poster.jpg", evidence_chunk_ids=["C1"]),
                            DigitalBookBlock("b5", "assessment", "任务评价", items=["评价"], evidence_chunk_ids=["C1"]),
                            DigitalBookBlock("b6", "exercises", "思考与练习", items=["练习"], evidence_chunk_ids=["C1"]),
                        ],
                    )
                ],
            )
        ],
    )


def test_digital_book_reviewer_accepts_complete_package(tmp_path: Path) -> None:
    video = tmp_path / "assets" / "videos" / "demo.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")

    issues = DigitalBookReviewerAgent().run(make_book(), {"C1"}, tmp_path)

    assert issues == []


def test_digital_book_reviewer_detects_missing_task_structure(tmp_path: Path) -> None:
    book = make_book(video_src="")
    book.projects[0].tasks[0].blocks = book.projects[0].tasks[0].blocks[:3]

    issues = DigitalBookReviewerAgent().run(book, {"C1"}, tmp_path)
    messages = [issue.message for issue in issues]

    assert "缺少视频资源块。" in messages
    assert "缺少任务评价。" in messages
    assert "缺少思考与练习。" in messages


def test_digital_book_reviewer_detects_student_visible_internal_traces(tmp_path: Path) -> None:
    video = tmp_path / "assets" / "videos" / "demo.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    book = make_book()
    book.projects[0].tasks[0].blocks[2].markdown = "证据：C000123 来源：demo.mp4 chunk_id: C000123 待人工复核。"

    issues = DigitalBookReviewerAgent().run(book, {"C1"}, tmp_path)
    messages = [issue.message for issue in issues]

    assert "学生端正文包含内部证据或素材处理痕迹。" in messages
    assert any(issue.severity == "high" and issue.location == "b3" for issue in issues)


def test_digital_book_reviewer_detects_project_and_item_internal_traces(tmp_path: Path) -> None:
    video = tmp_path / "assets" / "videos" / "demo.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    book = make_book()
    book.projects[0].ability_map = ["素材观察与证据定位", "操作过程分析与人工复核"]
    book.projects[0].tasks[0].blocks[1].items = ["1. 送丝（难度：basic；先修：无）"]
    book.projects[0].tasks[0].blocks[5].items = ["basic/observation：请在教材中输入证据编号 C000123。"]

    issues = DigitalBookReviewerAgent().run(book, {"C1"}, tmp_path)
    messages = [issue.message for issue in issues]

    assert "项目导学信息包含内部证据或素材处理痕迹。" in messages
    assert "学生端列表条目包含内部证据或素材处理痕迹。" in messages


def test_digital_book_reviewer_detects_duplicate_videos_and_placeholder_text(tmp_path: Path) -> None:
    video = tmp_path / "assets" / "videos" / "demo.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    book = make_book()
    task = book.projects[0].tasks[0]
    task.blocks[2].markdown = "围绕“送丝”观察示范视频，理解关键概念。"
    task.blocks.insert(
        4,
        DigitalBookBlock("b4_dup", "video", "视频2", src="assets/videos/demo.mp4", poster="poster.jpg", evidence_chunk_ids=["C1"]),
    )

    issues = DigitalBookReviewerAgent().run(book, {"C1"}, tmp_path)
    messages = [issue.message for issue in issues]

    assert "同一任务中存在重复视频资源。" in messages
    assert "知识点正文主要是泛化占位提示。" in messages


def test_render_digital_book_review_markdown_contains_counts() -> None:
    markdown = render_digital_book_review_markdown("样章", [])

    assert "# 样章 电子教材结构审核" in markdown
    assert "暂未发现电子教材结构问题" in markdown
