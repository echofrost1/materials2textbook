import json

from materials2textbook.learning_analytics import (
    STUDY_DATA_SCHEMA,
    build_class_learning_report,
    load_study_data_records,
    parse_study_data,
    render_class_learning_report_html,
    render_class_learning_report_markdown,
)


def study_payload(progress: int, *, active_id: str = "task_01", note: str = "", bookmark: str = "任务1") -> dict:
    return {
        "schema": STUDY_DATA_SCHEMA,
        "book_id": "tig-book",
        "book_title": "钨极氩弧焊数字教材样章",
        "progress": progress,
        "active_id": active_id,
        "font_size": 17,
        "note": note,
        "bookmarks": [{"id": active_id, "text": bookmark}],
        "exported_at": "2026-06-16T00:00:00Z",
    }


def test_parse_study_data_clamps_progress_and_keeps_bookmarks() -> None:
    record = parse_study_data(study_payload(120, note="复习送丝"))

    assert record.progress == 100.0
    assert record.book_id == "tig-book"
    assert record.note == "复习送丝"
    assert record.bookmarks == [{"id": "task_01", "text": "任务1"}]


def test_build_class_learning_report_summarizes_records() -> None:
    records = [
        parse_study_data(study_payload(40, active_id="task_01", note="问题1", bookmark="任务1")),
        parse_study_data(study_payload(100, active_id="task_02", bookmark="任务2")),
        parse_study_data(study_payload(70, active_id="task_02", note="问题3", bookmark="任务2")),
    ]

    report = build_class_learning_report(records, ["bad.json"])

    assert report.total_records == 4
    assert report.valid_records == 3
    assert report.invalid_records == 1
    assert report.average_progress == 70.0
    assert report.completed_count == 1
    assert report.note_count == 2
    assert report.active_section_counts == {"task_02": 2, "task_01": 1}
    assert report.popular_bookmarks == {"任务2": 2, "任务1": 1}


def test_load_study_data_records_and_render_markdown(tmp_path) -> None:
    valid = tmp_path / "student_a.json"
    invalid = tmp_path / "bad.json"
    valid.write_text(json.dumps(study_payload(80), ensure_ascii=False), encoding="utf-8")
    invalid.write_text("not json", encoding="utf-8")

    records, invalid_sources = load_study_data_records([valid, invalid])
    report = build_class_learning_report(records, invalid_sources)
    markdown = render_class_learning_report_markdown(report)

    assert len(records) == 1
    assert invalid_sources == [str(invalid)]
    assert "# 钨极氩弧焊数字教材样章 班级学习报告" in markdown
    assert "平均阅读进度：80.0%" in markdown
    assert str(invalid) in markdown


def test_load_study_data_records_accepts_utf8_bom(tmp_path) -> None:
    path = tmp_path / "student_bom.json"
    path.write_text(json.dumps(study_payload(55), ensure_ascii=False), encoding="utf-8-sig")

    records, invalid_sources = load_study_data_records([path])

    assert invalid_sources == []
    assert records[0].progress == 55


def test_render_class_learning_report_html_contains_metrics_and_escapes() -> None:
    records = [
        parse_study_data(study_payload(100, active_id="<script>", note="ok", bookmark="<b>任务</b>")),
    ]
    report = build_class_learning_report(records, [])
    html = render_class_learning_report_html(report)

    assert "班级学习报告" in html
    assert "平均阅读进度" in html
    assert "100.0%" in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;任务&lt;/b&gt;" in html
