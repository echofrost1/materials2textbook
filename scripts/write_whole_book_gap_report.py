#!/usr/bin/env python
"""Write a whole-book material readiness and gap report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root

import pandas as pd


CHAPTERS = [
    ("welding_equipment_safety", "焊接安全与设备基础"),
    ("shielded_metal_arc_welding", "焊条电弧焊"),
    ("tig_welding", "钨极氩弧焊"),
    ("welding_basic_operation", "焊接基本操作"),
    ("welding_defects_quality", "焊接缺陷与质量检验"),
    ("welding_training_assessment", "综合实训与考核"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write whole-book gap report.")
    parser.add_argument(
        "--chapter-work-root",
        type=Path,
        default=default_work_root() / "05_final_deliverables" / "chapter_work",
    )
    parser.add_argument("--output", type=Path, default=Path("docs") / "whole_book_next_gap_report.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.chapter_work_root.resolve()
    lines = [
        "# 整本焊接数字教材补齐缺口报告",
        "",
        f"记录时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（Asia/Shanghai）",
        "",
        "本报告用于把当前“整本精选演示版”继续推进到“整本试交付版”。核心原则是：不强行要求每章都有视频、PPT、参考文本，但每章必须有足够可追溯、可审核的证据。素材不足的章先进入缺口池，不硬写成正文。",
        "",
        "## 总体结论",
        "",
        "- 已具备较好素材基础的章节：焊接安全与设备基础、焊条电弧焊、钨极氩弧焊、焊接基本操作。",
        "- 仍需补齐的章节：焊接缺陷与质量检验、综合实训与考核。",
        "- 已固化两个关键脚本：`scripts/build_fixed_book_inputs.py` 和 `scripts/cut_reviewed_video_clips.py`。",
        "- 下一版整本应固定目录，不再让 LLM 自动合并章节。",
        "",
        "## 章节状态",
        "",
    ]

    table_rows = []
    for slug, title in CHAPTERS:
        chapter_root = root / slug
        summary = read_json(chapter_root / "chapter_evidence_pack_summary.json")
        readiness = read_readiness(chapter_root / "chapter_readiness_report.xlsx")
        gap_log = read_readiness(chapter_root / "chapter_evidence_gap_log.xlsx")
        reviewed_counts = {
            "video": count_jsonl(
                find_existing(chapter_root, ["chapter_video_segments.jsonl", f"{slug}_video_keep_reviewed.jsonl", "tig_chapter_video_keep_reviewed.jsonl"])
            ),
            "ppt": count_jsonl(
                find_existing(chapter_root, ["chapter_ppt_assets.jsonl", f"{slug}_ppt_keep_reviewed.jsonl", "tig_chapter_ppt_keep_reviewed.jsonl"])
            ),
            "document": count_jsonl(
                find_existing(
                    chapter_root,
                    ["chapter_document_segments.jsonl", f"{slug}_document_keep_reviewed.jsonl", "tig_chapter_document_keep_reviewed.jsonl"],
                )
            ),
        }
        ready_count = int((readiness["readiness_status"] == "ready").sum()) if not readiness.empty else 0
        partial_count = int((readiness["readiness_status"] == "partial_ready").sum()) if not readiness.empty else 0
        not_ready_count = int((readiness["readiness_status"] == "not_ready").sum()) if not readiness.empty else 0
        total_points = len(readiness)
        status = chapter_status(ready_count, total_points, not_ready_count)
        table_rows.append(
            {
                "章节": title,
                "状态": status,
                "知识点": total_points,
                "ready": ready_count,
                "partial": partial_count,
                "not_ready": not_ready_count,
                "Agent视频": reviewed_counts["video"],
                "Agent PPT": reviewed_counts["ppt"],
                "Agent文档": reviewed_counts["document"],
            }
        )

        lines.extend(
            [
                f"### {title}",
                "",
                f"- 状态：{status}",
                f"- 素材包记录数：{summary.get('selected_records', 0) if summary else 0}",
                f"- Agent_Keep 素材：视频 {reviewed_counts['video']}，PPT {reviewed_counts['ppt']}，文档/结构化 {reviewed_counts['document']}",
                f"- readiness：ready {ready_count} / partial {partial_count} / not_ready {not_ready_count}",
            ]
        )
        if not readiness.empty:
            weak = readiness[readiness["readiness_status"] != "ready"]
            if not weak.empty:
                lines.append("- 需补知识点：")
                for _, row in weak.iterrows():
                    lines.append(
                        f"  - {row.get('knowledge_point')}: {row.get('readiness_status')}，当前证据 {int(row.get('total_evidence_count', 0) or 0)} 条"
                    )
            else:
                lines.append("- 当前知识点均达到 ready。")
        if not gap_log.empty:
            lines.append("- 缺口记录：")
            for _, row in gap_log.head(8).iterrows():
                lines.append(f"  - {row.get('knowledge_point')}: {row.get('gap_description')}")
        lines.append("")

    lines.extend(["## 汇总表", "", markdown_table(table_rows), ""])
    lines.extend(
        [
            "## 下一步执行建议",
            "",
            "1. 用 `scripts/build_fixed_book_inputs.py` 生成固定目录整本输入，默认只纳入成熟章节。",
            "2. 用 `scripts/run_full_digital_textbook.py --book-mode` 跑固定目录整本精选版。",
            "3. 用 `scripts/cut_reviewed_video_clips.py` 对生成后的 `digital_book/` 做物理切片并重新打包。",
            "4. 针对“焊接缺陷与质量检验”“综合实训与考核”回到 00/01 台账补找 PPT、视频、题库或评分表。",
            "5. 修 writer prompt，把每个事实段落强制绑定证据 ID，目标是把 `citation_coverage_rate` 提升到 0.8 以上。",
            "",
        ]
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)
    return 0


def chapter_status(ready: int, total: int, not_ready: int) -> str:
    if total == 0:
        return "missing"
    if not_ready == 0 and ready == total:
        return "ready"
    if ready / total >= 0.6:
        return "usable_with_gaps"
    return "needs_material_backfill"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_readiness(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_excel(path)


def find_existing(root: Path, names: list[str]) -> Path | None:
    for name in names:
        path = root / name
        if path.exists():
            return path
    return None


def count_jsonl(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def markdown_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
