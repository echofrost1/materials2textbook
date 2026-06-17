#!/usr/bin/env python
"""Merge a validated material-processing batch into the main JSONL/XLSX inputs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


PROJECT_ROOT = Path("work_materials") / "work_material1"
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
JSON_DIR = PROJECT_ROOT / "02_working_processing" / "json"
BACKUP_DIR = PROJECT_ROOT / "02_working_processing" / "json" / "backups"
REVIEW_DIR = PROJECT_ROOT / "03_review_manual_check"

VIDEO_MAIN_JSONL = JSON_DIR / "video_segments.jsonl"
PPT_MAIN_JSONL = JSON_DIR / "ppt_assets.jsonl"
AUDIO_MAIN_JSONL = JSON_DIR / "audio_segments.jsonl"
STRUCTURED_MAIN_JSONL = JSON_DIR / "structured_assets.jsonl"
REFERENCE_MAIN_JSONL = JSON_DIR / "reference_text_assets.jsonl"
VIDEO_MAIN_XLSX = MANIFEST_DIR / "video_segments.xlsx"
PPT_MAIN_XLSX = MANIFEST_DIR / "ppt_assets.xlsx"
AUDIO_MAIN_XLSX = MANIFEST_DIR / "audio_segments.xlsx"
STRUCTURED_MAIN_XLSX = MANIFEST_DIR / "structured_assets.xlsx"
REFERENCE_MAIN_XLSX = MANIFEST_DIR / "reference_text_assets.xlsx"


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_excel_with_fallback(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.applymap(lambda value: re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", value) if isinstance(value, str) else value)
    try:
        df.to_excel(path, index=False, engine="openpyxl")
        return path
    except PermissionError:
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        df.to_excel(fallback, index=False, engine="openpyxl")
        return fallback


def infer_type(path: Path) -> str:
    name = path.name
    if "video_segments" in name:
        return "video"
    if "ppt_assets" in name:
        return "ppt"
    if "audio_segments" in name:
        return "audio"
    if "structured_assets" in name:
        return "structured"
    if "reference_text_assets" in name:
        return "reference"
    raise ValueError(f"Cannot infer batch type from {path}")


def required_validation_summary(batch_path: Path) -> Path:
    return REVIEW_DIR / f"{batch_path.stem}_validation_summary.json"


def load_validation_summary(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Validation summary not found: {path}. Run scripts/validate_material_batch.py first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def backup(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, out)
    return out


def id_field_for(batch_type: str) -> str:
    return {
        "video": "clip_id",
        "ppt": "ppt_asset_id",
        "audio": "audio_segment_id",
        "structured": "structured_asset_id",
        "reference": "reference_text_id",
    }[batch_type]


def main_paths_for(batch_type: str) -> tuple[Path, Path]:
    if batch_type == "video":
        return VIDEO_MAIN_JSONL, VIDEO_MAIN_XLSX
    if batch_type == "ppt":
        return PPT_MAIN_JSONL, PPT_MAIN_XLSX
    if batch_type == "audio":
        return AUDIO_MAIN_JSONL, AUDIO_MAIN_XLSX
    if batch_type == "structured":
        return STRUCTURED_MAIN_JSONL, STRUCTURED_MAIN_XLSX
    return REFERENCE_MAIN_JSONL, REFERENCE_MAIN_XLSX


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-jsonl", required=True, type=Path)
    parser.add_argument("--batch-type", choices=["video", "ppt", "audio", "structured", "reference", "auto"], default="auto")
    parser.add_argument("--allow-warnings", action="store_true")
    parser.add_argument("--skip-validation-check", action="store_true")
    args = parser.parse_args()

    batch_path = args.batch_jsonl
    batch_type = infer_type(batch_path) if args.batch_type == "auto" else args.batch_type
    summary_path = required_validation_summary(batch_path)
    if not args.skip_validation_check:
        summary = load_validation_summary(summary_path)
        if not summary.get("pass_validation"):
            raise SystemExit(f"Batch validation did not pass: {summary_path}")
        if summary.get("warning_count", 0) and not args.allow_warnings:
            raise SystemExit(
                f"Batch has {summary.get('warning_count')} warning(s). "
                "Review the validation report or pass --allow-warnings."
            )

    main_jsonl, main_xlsx = main_paths_for(batch_type)
    batch_rows = read_jsonl(batch_path)
    main_rows = read_jsonl(main_jsonl)
    field = id_field_for(batch_type)
    existing = {str(row.get(field)) for row in main_rows}
    duplicate = [str(row.get(field)) for row in batch_rows if str(row.get(field)) in existing]
    if duplicate:
        raise SystemExit(f"Cannot merge; duplicate {field} already exists in main: {duplicate[:10]}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_backup = backup(main_jsonl, stamp)
    xlsx_backup = backup(main_xlsx, stamp)
    merged = main_rows + batch_rows
    write_jsonl(main_jsonl, merged)
    xlsx_path = write_excel_with_fallback(pd.DataFrame(merged), main_xlsx)
    print("Merged batch:")
    print(f"  batch_jsonl: {batch_path}")
    print(f"  batch_type: {batch_type}")
    print(f"  batch_rows: {len(batch_rows)}")
    print(f"  main_rows_after: {len(merged)}")
    if json_backup:
        print(f"  json_backup: {json_backup}")
    if xlsx_backup:
        print(f"  xlsx_backup: {xlsx_backup}")
    print(f"  main_jsonl: {main_jsonl}")
    print(f"  main_xlsx: {xlsx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
