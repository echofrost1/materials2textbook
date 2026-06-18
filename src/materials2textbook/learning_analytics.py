from __future__ import annotations

import json
import html
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STUDY_DATA_SCHEMA = "materials2textbook.study_data.v1"


@dataclass
class StudyDataRecord:
    source_path: str
    book_id: str
    book_title: str
    progress: float
    active_id: str = ""
    font_size: float = 0.0
    note: str = ""
    bookmarks: list[dict[str, str]] = field(default_factory=list)
    exported_at: str = ""


@dataclass
class ClassLearningReport:
    book_id: str
    book_title: str
    total_records: int
    valid_records: int
    invalid_records: int
    average_progress: float
    completed_count: int
    note_count: int
    bookmark_count: int
    active_section_counts: dict[str, int] = field(default_factory=dict)
    popular_bookmarks: dict[str, int] = field(default_factory=dict)
    invalid_sources: list[str] = field(default_factory=list)


def load_study_data_records(paths: list[Path]) -> tuple[list[StudyDataRecord], list[str]]:
    records: list[StudyDataRecord] = []
    invalid_sources: list[str] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            records.append(parse_study_data(payload, path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            invalid_sources.append(str(path))
    return records, invalid_sources


def parse_study_data(payload: dict[str, Any], source_path: Path | str = "") -> StudyDataRecord:
    if payload.get("schema") != STUDY_DATA_SCHEMA:
        raise ValueError("Unsupported study data schema.")
    bookmarks = [
        {"id": str(item.get("id", "")), "text": str(item.get("text", ""))}
        for item in payload.get("bookmarks", [])
        if isinstance(item, dict) and item.get("id") and item.get("text")
    ]
    progress = _clamp_float(payload.get("progress"), 0.0, 100.0)
    return StudyDataRecord(
        source_path=str(source_path),
        book_id=str(payload.get("book_id", "")),
        book_title=str(payload.get("book_title", "")),
        progress=progress,
        active_id=str(payload.get("active_id", "")),
        font_size=_clamp_float(payload.get("font_size"), 0.0, 100.0),
        note=str(payload.get("note", "")),
        bookmarks=bookmarks,
        exported_at=str(payload.get("exported_at", "")),
    )


def build_class_learning_report(records: list[StudyDataRecord], invalid_sources: list[str] | None = None) -> ClassLearningReport:
    invalid_sources = invalid_sources or []
    valid_count = len(records)
    book_id = _most_common([record.book_id for record in records])
    book_title = _most_common([record.book_title for record in records])
    average_progress = round(sum(record.progress for record in records) / valid_count, 2) if valid_count else 0.0
    active_sections = Counter(record.active_id or "unknown" for record in records)
    bookmark_counter: Counter[str] = Counter()
    for record in records:
        for bookmark in record.bookmarks:
            bookmark_counter[bookmark["text"]] += 1
    return ClassLearningReport(
        book_id=book_id,
        book_title=book_title,
        total_records=valid_count + len(invalid_sources),
        valid_records=valid_count,
        invalid_records=len(invalid_sources),
        average_progress=average_progress,
        completed_count=sum(1 for record in records if record.progress >= 90),
        note_count=sum(1 for record in records if record.note.strip()),
        bookmark_count=sum(len(record.bookmarks) for record in records),
        active_section_counts=dict(active_sections.most_common()),
        popular_bookmarks=dict(bookmark_counter.most_common(10)),
        invalid_sources=invalid_sources,
    )


def render_class_learning_report_markdown(report: ClassLearningReport) -> str:
    lines = [
        f"# {report.book_title or report.book_id or '数字教材'} 班级学习报告",
        "",
        "## 总览",
        "",
        f"- 数据包总数：{report.total_records}",
        f"- 有效数据包：{report.valid_records}",
        f"- 无效数据包：{report.invalid_records}",
        f"- 平均阅读进度：{report.average_progress:.1f}%",
        f"- 接近完成学习人数：{report.completed_count}",
        f"- 提交笔记人数：{report.note_count}",
        f"- 书签总数：{report.bookmark_count}",
        "",
        "## 当前学习位置",
        "",
    ]
    if report.active_section_counts:
        for section, count in report.active_section_counts.items():
            lines.append(f"- `{section}`：{count}")
    else:
        lines.append("- 暂无当前学习位置数据。")

    lines.extend(["", "## 热门书签", ""])
    if report.popular_bookmarks:
        for bookmark, count in report.popular_bookmarks.items():
            lines.append(f"- {bookmark}：{count}")
    else:
        lines.append("- 暂无书签数据。")

    if report.invalid_sources:
        lines.extend(["", "## 无效数据包", ""])
        for source in report.invalid_sources:
            lines.append(f"- {source}")
    return "\n".join(lines).rstrip() + "\n"


def render_class_learning_report_html(report: ClassLearningReport) -> str:
    title = html.escape(report.book_title or report.book_id or "数字教材")
    active_rows = _render_metric_rows(report.active_section_counts, empty_text="暂无当前学习位置数据。")
    bookmark_rows = _render_metric_rows(report.popular_bookmarks, empty_text="暂无书签数据。")
    invalid_rows = "".join(f"<li>{html.escape(source)}</li>" for source in report.invalid_sources)
    invalid_block = f"<section><h2>无效数据包</h2><ul>{invalid_rows}</ul></section>" if invalid_rows else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} 班级学习报告</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      color: #20242a;
      background: #f6f7f9;
    }}
    header {{
      padding: 28px 32px;
      background: #16324f;
      color: #fff;
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    header p {{ margin: 0; color: #d8e3ee; }}
    main {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 24px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .metric, section {{
      background: #fff;
      border: 1px solid #dfe3e8;
      border-radius: 8px;
      padding: 16px;
    }}
    .metric span {{
      display: block;
      color: #5f6b7a;
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric strong {{
      font-size: 24px;
      color: #16324f;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid #e6ebf0;
      text-align: left;
    }}
    th {{ color: #5f6b7a; font-weight: 600; }}
    ul {{ margin: 0; padding-left: 18px; }}
  </style>
</head>
<body>
  <header>
    <h1>{title} 班级学习报告</h1>
    <p>由数字教材阅读器导出的学习数据包离线聚合生成。</p>
  </header>
  <main>
    <div class="metrics">
      <div class="metric"><span>数据包总数</span><strong>{report.total_records}</strong></div>
      <div class="metric"><span>有效数据包</span><strong>{report.valid_records}</strong></div>
      <div class="metric"><span>平均阅读进度</span><strong>{report.average_progress:.1f}%</strong></div>
      <div class="metric"><span>接近完成人数</span><strong>{report.completed_count}</strong></div>
      <div class="metric"><span>提交笔记人数</span><strong>{report.note_count}</strong></div>
      <div class="metric"><span>书签总数</span><strong>{report.bookmark_count}</strong></div>
    </div>
    <div class="grid">
      <section>
        <h2>当前学习位置</h2>
        {active_rows}
      </section>
      <section>
        <h2>热门书签</h2>
        {bookmark_rows}
      </section>
    </div>
    {invalid_block}
  </main>
</body>
</html>
"""


def _render_metric_rows(values: dict[str, int], empty_text: str) -> str:
    if not values:
        return f"<p>{html.escape(empty_text)}</p>"
    rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{count}</td></tr>"
        for label, count in values.items()
    )
    return f"<table><thead><tr><th>项目</th><th>人数/次数</th></tr></thead><tbody>{rows}</tbody></table>"


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _most_common(values: list[str]) -> str:
    non_empty = [value for value in values if value]
    if not non_empty:
        return ""
    return Counter(non_empty).most_common(1)[0][0]
