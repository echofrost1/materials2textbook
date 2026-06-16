#!/usr/bin/env python
"""Validate a material-processing batch before it can be merged.

The forward plan requires deep-processing outputs to be reviewable and
traceable before they enter the main textbook-generation inputs. This validator
checks batch JSONL files under:

  work_materials/work_material1/02_working_processing/json/batches/

It writes a human-readable xlsx report plus a JSON summary.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd


PROJECT_ROOT = Path("work_materials") / "work_material1"
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
WORK_DIR = PROJECT_ROOT / "02_working_processing"
JSON_DIR = WORK_DIR / "json"
BATCH_JSON_DIR = JSON_DIR / "batches"
REVIEW_DIR = PROJECT_ROOT / "03_review_manual_check"

ASSETS_MANIFEST = MANIFEST_DIR / "assets_manifest.xlsx"
VIDEO_MAIN_JSONL = JSON_DIR / "video_segments.jsonl"
PPT_MAIN_JSONL = JSON_DIR / "ppt_assets.jsonl"
AUDIO_MAIN_JSONL = JSON_DIR / "audio_segments.jsonl"
STRUCTURED_MAIN_JSONL = JSON_DIR / "structured_assets.jsonl"

VIDEO_REQUIRED = [
    "clip_id",
    "source_asset_id",
    "source_video",
    "original_path",
    "start_time",
    "end_time",
    "material_block",
    "material_block_code",
    "knowledge_point",
    "recommended_chapter",
    "transcript_status",
    "evidence_text",
    "boundary_reason",
    "review_status",
]

PPT_REQUIRED = [
    "ppt_asset_id",
    "source_asset_id",
    "source_ppt",
    "original_path",
    "slide_index",
    "material_block",
    "material_block_code",
    "knowledge_point",
    "recommended_chapter",
    "evidence_text",
    "review_status",
]

AUDIO_REQUIRED = [
    "audio_segment_id",
    "source_asset_id",
    "source_audio",
    "original_path",
    "start_time",
    "end_time",
    "material_block",
    "material_block_code",
    "knowledge_point",
    "transcript_status",
    "evidence_text",
    "boundary_reason",
    "review_status",
]

STRUCTURED_REQUIRED = [
    "structured_asset_id",
    "source_asset_id",
    "source_file",
    "original_path",
    "sheet_name",
    "table_type",
    "material_block",
    "material_block_code",
    "knowledge_point",
    "evidence_text",
    "review_status",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_excel_with_fallback(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_excel(path, index=False, engine="openpyxl")
        return path
    except PermissionError:
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        df.to_excel(fallback, index=False, engine="openpyxl")
        return fallback


def parse_time(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(float(part)) for part in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return None


def manifest_asset_ids() -> set[str]:
    if not ASSETS_MANIFEST.exists():
        return set()
    df = pd.read_excel(ASSETS_MANIFEST)
    return set(df["asset_id"].fillna("").astype(str))


def existing_ids(path: Path, id_field: str) -> set[str]:
    return {clean_text(row.get(id_field)) for row in read_jsonl(path)}


def validate_required(row: Dict[str, Any], required: Iterable[str], row_label: str) -> List[Dict[str, Any]]:
    issues = []
    for field in required:
        if not clean_text(row.get(field)):
            issues.append(issue(row_label, "error", "missing_field", field, "必填字段为空"))
    return issues


def issue(row_label: str, severity: str, code: str, field: str, message: str) -> Dict[str, Any]:
    return {
        "row_label": row_label,
        "severity": severity,
        "issue_code": code,
        "field": field,
        "message": message,
    }


def validate_video_rows(rows: List[Dict[str, Any]], asset_ids: set[str]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    ids = [clean_text(row.get("clip_id")) for row in rows]
    duplicates = {key for key, count in Counter(ids).items() if key and count > 1}
    existing = existing_ids(VIDEO_MAIN_JSONL, "clip_id")
    for index, row in enumerate(rows, start=1):
        label = clean_text(row.get("clip_id")) or f"video_row_{index}"
        issues.extend(validate_required(row, VIDEO_REQUIRED, label))
        if clean_text(row.get("clip_id")) in duplicates:
            issues.append(issue(label, "error", "duplicate_in_batch", "clip_id", "clip_id 在本批次中重复"))
        if clean_text(row.get("clip_id")) in existing:
            issues.append(issue(label, "error", "duplicate_in_main", "clip_id", "clip_id 已存在于主 video_segments.jsonl"))
        if clean_text(row.get("source_asset_id")) not in asset_ids:
            issues.append(issue(label, "error", "source_asset_missing", "source_asset_id", "source_asset_id 不在 assets_manifest.xlsx"))
        start = parse_time(row.get("start_time"))
        end = parse_time(row.get("end_time"))
        if start is None or end is None or end <= start:
            issues.append(issue(label, "error", "bad_time_range", "start_time/end_time", "时间码无效或 end_time 不大于 start_time"))
        evidence = clean_text(row.get("evidence_text"))
        if len(evidence) < 20:
            issues.append(issue(label, "warning", "weak_evidence", "evidence_text", "证据文本过短，建议复核 ASR 或人工补充"))
        keyframes = [item for item in clean_text(row.get("keyframe_paths")).split(";") if item]
        if not keyframes:
            issues.append(issue(label, "warning", "missing_keyframes", "keyframe_paths", "没有关键帧路径"))
        else:
            missing = [path for path in keyframes if not (PROJECT_ROOT / path).exists()]
            if missing:
                issues.append(issue(label, "warning", "keyframe_missing_file", "keyframe_paths", "部分关键帧文件不存在：" + ";".join(missing[:3])))
    return issues


def validate_ppt_rows(rows: List[Dict[str, Any]], asset_ids: set[str]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    ids = [clean_text(row.get("ppt_asset_id")) for row in rows]
    duplicates = {key for key, count in Counter(ids).items() if key and count > 1}
    existing = existing_ids(PPT_MAIN_JSONL, "ppt_asset_id")
    for index, row in enumerate(rows, start=1):
        label = clean_text(row.get("ppt_asset_id")) or f"ppt_row_{index}"
        issues.extend(validate_required(row, PPT_REQUIRED, label))
        if clean_text(row.get("ppt_asset_id")) in duplicates:
            issues.append(issue(label, "error", "duplicate_in_batch", "ppt_asset_id", "ppt_asset_id 在本批次中重复"))
        if clean_text(row.get("ppt_asset_id")) in existing:
            issues.append(issue(label, "error", "duplicate_in_main", "ppt_asset_id", "ppt_asset_id 已存在于主 ppt_assets.jsonl"))
        if clean_text(row.get("source_asset_id")) not in asset_ids:
            issues.append(issue(label, "error", "source_asset_missing", "source_asset_id", "source_asset_id 不在 assets_manifest.xlsx"))
        evidence = clean_text(row.get("evidence_text"))
        if len(evidence) < 10 and int(row.get("image_count") or 0) <= 0:
            issues.append(issue(label, "warning", "weak_slide_evidence", "evidence_text", "PPT 页文字和图片证据都偏弱"))
        image_paths = [item for item in clean_text(row.get("image_paths")).split(";") if item]
        missing = [path for path in image_paths if not (PROJECT_ROOT / path).exists()]
        if missing:
            issues.append(issue(label, "warning", "ppt_image_missing_file", "image_paths", "部分 PPT 图片文件不存在：" + ";".join(missing[:3])))
    return issues


def validate_audio_rows(rows: List[Dict[str, Any]], asset_ids: set[str]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    ids = [clean_text(row.get("audio_segment_id")) for row in rows]
    duplicates = {key for key, count in Counter(ids).items() if key and count > 1}
    existing = existing_ids(AUDIO_MAIN_JSONL, "audio_segment_id")
    for index, row in enumerate(rows, start=1):
        label = clean_text(row.get("audio_segment_id")) or f"audio_row_{index}"
        issues.extend(validate_required(row, AUDIO_REQUIRED, label))
        if clean_text(row.get("audio_segment_id")) in duplicates:
            issues.append(issue(label, "error", "duplicate_in_batch", "audio_segment_id", "audio_segment_id duplicate in batch"))
        if clean_text(row.get("audio_segment_id")) in existing:
            issues.append(issue(label, "error", "duplicate_in_main", "audio_segment_id", "audio_segment_id already exists in audio_segments.jsonl"))
        if clean_text(row.get("source_asset_id")) not in asset_ids:
            issues.append(issue(label, "error", "source_asset_missing", "source_asset_id", "source_asset_id not in assets_manifest.xlsx"))
        start = parse_time(row.get("start_time"))
        end = parse_time(row.get("end_time"))
        if start is None or end is None or end <= start:
            issues.append(issue(label, "error", "bad_time_range", "start_time/end_time", "invalid audio time range"))
        if len(clean_text(row.get("evidence_text"))) < 20:
            issues.append(issue(label, "warning", "weak_evidence", "evidence_text", "audio transcript evidence is short"))
    return issues


def validate_structured_rows(rows: List[Dict[str, Any]], asset_ids: set[str]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    ids = [clean_text(row.get("structured_asset_id")) for row in rows]
    duplicates = {key for key, count in Counter(ids).items() if key and count > 1}
    existing = existing_ids(STRUCTURED_MAIN_JSONL, "structured_asset_id")
    for index, row in enumerate(rows, start=1):
        label = clean_text(row.get("structured_asset_id")) or f"structured_row_{index}"
        issues.extend(validate_required(row, STRUCTURED_REQUIRED, label))
        if clean_text(row.get("structured_asset_id")) in duplicates:
            issues.append(issue(label, "error", "duplicate_in_batch", "structured_asset_id", "structured_asset_id duplicate in batch"))
        if clean_text(row.get("structured_asset_id")) in existing:
            issues.append(issue(label, "error", "duplicate_in_main", "structured_asset_id", "structured_asset_id already exists in structured_assets.jsonl"))
        if clean_text(row.get("source_asset_id")) not in asset_ids:
            issues.append(issue(label, "error", "source_asset_missing", "source_asset_id", "source_asset_id not in assets_manifest.xlsx"))
        if len(clean_text(row.get("evidence_text"))) < 20:
            issues.append(issue(label, "warning", "weak_evidence", "evidence_text", "structured evidence preview is short"))
    return issues


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
    raise ValueError(f"Cannot infer batch type from {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-jsonl", required=True, type=Path)
    parser.add_argument("--batch-type", choices=["video", "ppt", "audio", "structured", "auto"], default="auto")
    parser.add_argument("--output-prefix", default="")
    args = parser.parse_args()

    batch_path = args.batch_jsonl
    batch_type = infer_type(batch_path) if args.batch_type == "auto" else args.batch_type
    rows = read_jsonl(batch_path)
    asset_ids = manifest_asset_ids()
    if batch_type == "video":
        issues = validate_video_rows(rows, asset_ids)
    elif batch_type == "ppt":
        issues = validate_ppt_rows(rows, asset_ids)
    elif batch_type == "audio":
        issues = validate_audio_rows(rows, asset_ids)
    else:
        issues = validate_structured_rows(rows, asset_ids)

    severity_counts = Counter(item["severity"] for item in issues)
    pass_validation = severity_counts.get("error", 0) == 0
    prefix = args.output_prefix or batch_path.stem
    report_xlsx = REVIEW_DIR / f"{prefix}_validation.xlsx"
    summary_json = REVIEW_DIR / f"{prefix}_validation_summary.json"
    issue_df = pd.DataFrame(issues, columns=["row_label", "severity", "issue_code", "field", "message"])
    if issue_df.empty:
        issue_df = pd.DataFrame(columns=["row_label", "severity", "issue_code", "field", "message"])
    report_path = write_excel_with_fallback(issue_df, report_xlsx)
    summary = {
        "batch_jsonl": str(batch_path),
        "batch_type": batch_type,
        "row_count": len(rows),
        "pass_validation": pass_validation,
        "error_count": severity_counts.get("error", 0),
        "warning_count": severity_counts.get("warning", 0),
        "report_xlsx": str(report_path),
    }
    write_json(summary_json, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if pass_validation else 2


if __name__ == "__main__":
    raise SystemExit(main())
