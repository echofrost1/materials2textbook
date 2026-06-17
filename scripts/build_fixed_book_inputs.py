#!/usr/bin/env python
"""Build whole-book inputs from fixed chapter packs.

The output is three JSONL files that can be passed to run_full_digital_textbook.py.
It keeps the curriculum order fixed and samples reviewed evidence per chapter.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_CHAPTERS = [
    {
        "slug": "welding_equipment_safety",
        "title": "焊接安全与设备基础",
        "include": True,
        "limits": {"video": 6, "ppt": 25, "document": 15},
    },
    {
        "slug": "shielded_metal_arc_welding",
        "title": "焊条电弧焊",
        "include": True,
        "limits": {"video": 6, "ppt": 25, "document": 15},
    },
    {
        "slug": "tig_welding",
        "title": "钨极氩弧焊",
        "include": True,
        "limits": {"video": 6, "ppt": 25, "document": 15},
    },
    {
        "slug": "welding_basic_operation",
        "title": "焊接基本操作",
        "include": True,
        "limits": {"video": 6, "ppt": 25, "document": 15},
    },
    {
        "slug": "welding_defects_quality",
        "title": "焊接缺陷与质量检验",
        "include": False,
        "limits": {"video": 4, "ppt": 15, "document": 12},
    },
    {
        "slug": "welding_training_assessment",
        "title": "综合实训与考核",
        "include": False,
        "limits": {"video": 4, "ppt": 15, "document": 12},
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build fixed-order whole-book JSONL inputs.")
    parser.add_argument(
        "--chapter-work-root",
        type=Path,
        default=Path("work_materials") / "work_material1" / "05_final_deliverables" / "chapter_work",
    )
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--include-thin-chapters", action="store_true")
    parser.add_argument("--chapter-config", type=Path, default=None, help="Optional JSON chapter config.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    chapter_root = args.chapter_work_root.resolve()
    output_root = (args.output_root or chapter_root / "whole_book_fixed_inputs").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    chapters = load_chapters(args.chapter_config)

    outputs = {"video": [], "ppt": [], "document": []}
    chapter_rows = []
    for order, chapter in enumerate(chapters, start=1):
        slug = chapter["slug"]
        title = chapter["title"]
        root = chapter_root / slug
        readiness = read_readiness(root / "chapter_readiness_report.xlsx")
        readiness_counts = (
            {str(key): int(value) for key, value in readiness["readiness_status"].value_counts().items()}
            if not readiness.empty
            else {}
        )
        include = bool(chapter.get("include")) or args.include_thin_chapters
        if not include:
            include = is_generation_ready(readiness)
        chapter_summary = {
            "order": order,
            "slug": slug,
            "title": title,
            "include_in_generation": include,
            "readiness_counts": readiness_counts,
        }
        for kind in outputs:
            rows = load_reviewed_rows(root, slug, kind)
            selected = balanced_take(rows, int(chapter.get("limits", {}).get(kind, 0) or 0)) if include else []
            for row in selected:
                row["book_chapter_order"] = order
                row["recommended_chapter"] = title
                row["material_block"] = title
                row["source_chapter_pack"] = slug
            outputs[kind].extend(selected)
            chapter_summary[f"{kind}_reviewed_rows"] = len(rows)
            chapter_summary[f"{kind}_selected_rows"] = len(selected)
        chapter_rows.append(chapter_summary)

    paths = {}
    for kind, rows in outputs.items():
        path = output_root / f"whole_book_{kind}_fixed.jsonl"
        write_jsonl(path, rows)
        paths[kind] = str(path)

    summary = {
        "chapter_work_root": str(chapter_root),
        "output_root": str(output_root),
        "outputs": paths,
        "selected_counts": {kind: len(rows) for kind, rows in outputs.items()},
        "chapters": chapter_rows,
    }
    (output_root / "whole_book_fixed_inputs_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pd.DataFrame(chapter_rows).to_excel(output_root / "whole_book_fixed_inputs_summary.xlsx", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def load_chapters(path: Path | None) -> list[dict[str, Any]]:
    if not path:
        return DEFAULT_CHAPTERS
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("chapter config must be a JSON list")
    return data


def read_readiness(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_excel(path)


def is_generation_ready(readiness: pd.DataFrame) -> bool:
    if readiness.empty or "readiness_status" not in readiness:
        return False
    counts = readiness["readiness_status"].value_counts()
    ready = int(counts.get("ready", 0))
    total = len(readiness)
    return total > 0 and ready / total >= 0.6


def load_reviewed_rows(root: Path, slug: str, kind: str) -> list[dict[str, Any]]:
    candidates = []
    if kind == "video":
        candidates = ["chapter_video_segments.jsonl", f"{slug}_video_keep_reviewed.jsonl", "tig_chapter_video_keep_reviewed.jsonl"]
    elif kind == "ppt":
        candidates = ["chapter_ppt_assets.jsonl", f"{slug}_ppt_keep_reviewed.jsonl", "tig_chapter_ppt_keep_reviewed.jsonl"]
    else:
        candidates = [
            "chapter_document_segments.jsonl",
            f"{slug}_document_keep_reviewed.jsonl",
            "tig_chapter_document_keep_reviewed.jsonl",
        ]

    for name in candidates:
        path = root / name
        if path.exists():
            return [
                row
                for row in read_jsonl(path)
                if str(row.get("quality_gate_status", "")).lower() not in {"reject", "rejected"}
            ]
    return []


def balanced_take(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_kp[str(row.get("knowledge_point") or "unknown")].append(row)
    selected = []
    seen = set()
    for kp_rows in by_kp.values():
        kp_rows.sort(key=row_score, reverse=True)
        for row in kp_rows[:2]:
            key = stable_key(row)
            if key not in seen:
                selected.append(row)
                seen.add(key)
    for row in sorted(rows, key=row_score, reverse=True):
        if len(selected) >= limit:
            break
        key = stable_key(row)
        if key in seen:
            continue
        selected.append(row)
        seen.add(key)
    return selected[:limit]


def row_score(row: dict[str, Any]) -> tuple[float, int]:
    try:
        quality = float(row.get("quality_score") or 0)
    except (TypeError, ValueError):
        quality = 0.0
    text = str(
        row.get("evidence_text")
        or row.get("transcript_text")
        or row.get("slide_text")
        or row.get("extracted_text")
        or ""
    )
    return quality, min(len(text), 2000)


def stable_key(row: dict[str, Any]) -> str:
    for key in (
        "clip_id",
        "ppt_asset_id",
        "reference_text_id",
        "structured_asset_id",
        "assessment_id",
        "table_asset_id",
        "audio_segment_id",
    ):
        if row.get(key):
            return f"{key}:{row[key]}"
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
