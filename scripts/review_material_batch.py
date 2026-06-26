#!/usr/bin/env python
"""Review and score a material-processing batch before merge.

Validation answers "is the row structurally usable?". This script answers a
different question: "is the row good enough to enter the main textbook material
pool by default?" It is intentionally rule-based for the MVP pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any

import pandas as pd


PROJECT_ROOT = default_work_root()
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
WORK_DIR = PROJECT_ROOT / "02_working_processing"
JSON_DIR = WORK_DIR / "json"
BATCH_JSON_DIR = JSON_DIR / "batches"
REVIEW_DIR = PROJECT_ROOT / "03_review_manual_check"
BATCH_XLSX_DIR = MANIFEST_DIR / "batches"

DOMAIN_TERMS = [
    "焊接",
    "焊条",
    "焊芯",
    "药皮",
    "电弧",
    "焊钳",
    "焊机",
    "熔池",
    "引弧",
    "收弧",
    "运条",
    "坡口",
    "焊缝",
    "焊丝",
    "焊枪",
    "气割",
    "气焊",
    "钨极",
    "氩弧",
    "安全",
    "防护",
    "电流",
    "电压",
    "定位焊",
    "打底层",
    "填充层",
    "盖面层",
]

COMMON_ASR_ERROR_TERMS = [
    "夫妻",
    "漢",
    "估計",
    "壞",
    "龍磁",
    "電湖",
    "亞氣",
    "焊奉",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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


def parse_time(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parts = [int(float(part)) for part in text.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 1:
        return parts[0]
    return None


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def term_hits(text: str, terms: list[str] = DOMAIN_TERMS) -> list[str]:
    return [term for term in terms if term in text]


def repeated_line_ratio(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    counts = Counter(lines)
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / len(lines)


def asr_error_hits(text: str) -> list[str]:
    return [term for term in COMMON_ASR_ERROR_TERMS if term in text]


def existing_paths(path_text: str) -> tuple[int, int]:
    paths = [item.strip() for item in clean_text(path_text).split(";") if item.strip()]
    existing = sum(1 for path in paths if (PROJECT_ROOT / path).exists())
    return existing, len(paths)


def decision_from_score(score: float, hard_reject: bool = False) -> str:
    if hard_reject or score < 0.45:
        return "reject"
    if score < 0.70:
        return "needs_review"
    return "keep"


def review_video(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    clip_id = clean_text(row.get("clip_id"))
    evidence = clean_text(row.get("evidence_text") or row.get("transcript_text"))
    transcript = clean_text(row.get("transcript_text"))
    kp = clean_text(row.get("knowledge_point"))
    start = parse_time(row.get("start_time"))
    end = parse_time(row.get("end_time"))
    duration = (end - start) if start is not None and end is not None else None
    keyframes_existing, keyframes_total = existing_paths(clean_text(row.get("keyframe_paths")))
    hits = term_hits(f"{kp} {evidence}")
    errors = asr_error_hits(transcript)
    repeat_ratio = repeated_line_ratio(transcript)

    score = 0.40
    reasons: list[str] = []

    if duration is None or duration <= 0:
        score -= 0.35
        reasons.append("bad_time_range")
    elif 30 <= duration <= 180:
        score += 0.15
    elif 15 <= duration < 30 or 180 < duration <= 300:
        score += 0.05
        reasons.append("duration_edge")
    else:
        score -= 0.12
        reasons.append("duration_not_mvp_preferred")

    if len(evidence) >= 180:
        score += 0.18
    elif len(evidence) >= 60:
        score += 0.08
        reasons.append("evidence_short")
    else:
        score -= 0.20
        reasons.append("evidence_too_short")

    if hits:
        score += min(0.18, 0.04 * len(hits))
    else:
        score -= 0.12
        reasons.append("no_domain_term_hit")

    if keyframes_total and keyframes_existing == keyframes_total:
        score += 0.12
    elif keyframes_existing:
        score += 0.04
        reasons.append("partial_keyframes")
    else:
        score -= 0.15
        reasons.append("missing_keyframes")

    if errors:
        penalty = min(0.18, 0.04 * len(errors))
        score -= penalty
        reasons.append("asr_error_terms:" + ",".join(errors[:4]))
    if repeat_ratio > 0.35:
        score -= 0.12
        reasons.append("repeated_transcript_lines")

    score = round(clamp(score), 3)
    hard_reject = duration is not None and duration < 8
    decision = decision_from_score(score, hard_reject)
    if decision == "keep":
        review_status = "Agent_Keep"
    elif decision == "needs_review":
        review_status = "Needs_Review"
    else:
        review_status = "Agent_Reject"

    reviewed = dict(row)
    reviewed.update(
        {
            "quality_score": score,
            "auto_review_decision": decision,
            "review_status": review_status,
            "review_comment": "; ".join(reasons) if reasons else "auto review passed",
            "review_basis": "rule_score: duration + evidence_text + domain_terms + keyframes + ASR anomaly checks",
            "domain_term_hits": ";".join(hits),
            "asr_error_terms": ";".join(errors),
            "reviewed_time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    report = {
        "asset_unit_id": clip_id,
        "source_asset_id": clean_text(row.get("source_asset_id")),
        "asset_type": "video",
        "decision": decision,
        "quality_score": score,
        "duration_seconds": duration,
        "evidence_length": len(evidence),
        "domain_term_hits": ";".join(hits),
        "keyframes_existing": keyframes_existing,
        "keyframes_total": keyframes_total,
        "asr_error_terms": ";".join(errors),
        "repeat_ratio": round(repeat_ratio, 3),
        "reasons": "; ".join(reasons) if reasons else "auto review passed",
    }
    return reviewed, report


def review_ppt(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ppt_asset_id = clean_text(row.get("ppt_asset_id"))
    text = clean_text(row.get("evidence_text") or row.get("slide_text"))
    slide_title = clean_text(row.get("slide_title"))
    kp = clean_text(row.get("knowledge_point"))
    image_existing, image_total = existing_paths(clean_text(row.get("image_paths")))
    hits = term_hits(f"{kp} {slide_title} {text}")

    score = 0.38
    reasons: list[str] = []

    if len(text) >= 120:
        score += 0.22
    elif len(text) >= 25:
        score += 0.12
    elif image_total:
        score += 0.04
        reasons.append("image_only_or_low_text")
    else:
        score -= 0.18
        reasons.append("weak_slide_evidence")

    if hits:
        score += min(0.20, 0.04 * len(hits))
    else:
        score -= 0.10
        reasons.append("no_domain_term_hit")

    if image_total and image_existing == image_total:
        score += 0.12
    elif image_existing:
        score += 0.06
        reasons.append("partial_images")
    elif image_total:
        score -= 0.08
        reasons.append("missing_images")

    if re.search(r"^\d+\.?\s*$", text) or len(set(text)) <= 3:
        score -= 0.20
        reasons.append("likely_page_number_or_blank")

    score = round(clamp(score), 3)
    decision = decision_from_score(score)
    if decision == "keep":
        review_status = "Agent_Keep"
    elif decision == "needs_review":
        review_status = "Needs_Review"
    else:
        review_status = "Agent_Reject"

    reviewed = dict(row)
    reviewed.update(
        {
            "quality_score": score,
            "auto_review_decision": decision,
            "review_status": review_status,
            "review_comment": "; ".join(reasons) if reasons else "auto review passed",
            "review_basis": "rule_score: slide_text + domain_terms + extracted_images",
            "domain_term_hits": ";".join(hits),
            "reviewed_time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    report = {
        "asset_unit_id": ppt_asset_id,
        "source_asset_id": clean_text(row.get("source_asset_id")),
        "asset_type": "ppt",
        "decision": decision,
        "quality_score": score,
        "evidence_length": len(text),
        "domain_term_hits": ";".join(hits),
        "images_existing": image_existing,
        "images_total": image_total,
        "reasons": "; ".join(reasons) if reasons else "auto review passed",
    }
    return reviewed, report


def review_audio(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    audio_segment_id = clean_text(row.get("audio_segment_id"))
    evidence = clean_text(row.get("evidence_text") or row.get("transcript_text"))
    hits = term_hits(evidence)
    duration = 0
    start = parse_time(row.get("start_time"))
    end = parse_time(row.get("end_time"))
    if start is not None and end is not None:
        duration = max(0, end - start)
    score = 0.40
    score += clamp(len(evidence) / 500) * 0.35
    score += min(len(hits), 3) * 0.06
    if 30 <= duration <= 240:
        score += 0.12
    elif duration > 0:
        score += 0.05
    if clean_text(row.get("transcript_status")) == "EMPTY":
        score -= 0.25
    decision = decision_from_score(score)
    reviewed = dict(row)
    reviewed.update(
        {
            "quality_score": round(score, 3),
            "auto_review_decision": decision,
            "review_status": "Agent_Keep" if decision == "keep" else ("Agent_Reject" if decision == "reject" else "Needs_Review"),
            "review_comment": "auto audio review",
            "review_basis": "rule_score: transcript_length + duration + domain_terms",
            "domain_term_hits": ";".join(hits),
            "reviewed_time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    report = {
        "asset_unit_id": audio_segment_id,
        "source_asset_id": clean_text(row.get("source_asset_id")),
        "asset_type": "audio",
        "decision": decision,
        "quality_score": round(score, 3),
        "evidence_length": len(evidence),
        "duration_seconds": duration,
        "domain_term_hits": ";".join(hits),
    }
    return reviewed, report


def review_structured(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    structured_asset_id = clean_text(row.get("structured_asset_id"))
    evidence = clean_text(row.get("evidence_text") or row.get("extracted_text"))
    hits = term_hits(evidence)
    row_count = int(float(row.get("row_count") or 0))
    col_count = int(float(row.get("column_count") or 0))
    score = 0.35
    score += clamp(len(evidence) / 800) * 0.35
    score += min(len(hits), 3) * 0.05
    if row_count > 0 and col_count > 0:
        score += 0.15
    if clean_text(row.get("table_type")) in {"question_bank", "assessment_sheet"}:
        score += 0.08
    decision = decision_from_score(score)
    reviewed = dict(row)
    reviewed.update(
        {
            "quality_score": round(score, 3),
            "auto_review_decision": decision,
            "review_status": "Agent_Keep" if decision == "keep" else ("Agent_Reject" if decision == "reject" else "Needs_Review"),
            "review_comment": "auto structured review",
            "review_basis": "rule_score: table_preview + dimensions + table_type",
            "domain_term_hits": ";".join(hits),
            "reviewed_time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    report = {
        "asset_unit_id": structured_asset_id,
        "source_asset_id": clean_text(row.get("source_asset_id")),
        "asset_type": "structured",
        "decision": decision,
        "quality_score": round(score, 3),
        "evidence_length": len(evidence),
        "table_type": clean_text(row.get("table_type")),
        "row_count": row_count,
        "column_count": col_count,
        "domain_term_hits": ";".join(hits),
    }
    return reviewed, report


def review_reference(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    reference_text_id = clean_text(row.get("reference_text_id"))
    evidence = clean_text(row.get("evidence_text") or row.get("extracted_text"))
    hits = term_hits(evidence)
    score = 0.35
    score += clamp(len(evidence) / 900) * 0.40
    score += min(len(hits), 4) * 0.05
    if clean_text(row.get("text_extract_method")):
        score += 0.08
    decision = decision_from_score(score)
    reviewed = dict(row)
    reviewed.update(
        {
            "quality_score": round(score, 3),
            "auto_review_decision": decision,
            "review_status": "Agent_Keep" if decision == "keep" else ("Agent_Reject" if decision == "reject" else "Needs_Review"),
            "review_comment": "auto reference-text review",
            "review_basis": "rule_score: text_length + domain_terms + extraction_method",
            "domain_term_hits": ";".join(hits),
            "reviewed_time": datetime.now().isoformat(timespec="seconds"),
        }
    )
    report = {
        "asset_unit_id": reference_text_id,
        "source_asset_id": clean_text(row.get("source_asset_id")),
        "asset_type": "reference",
        "decision": decision,
        "quality_score": round(score, 3),
        "evidence_length": len(evidence),
        "domain_term_hits": ";".join(hits),
    }
    return reviewed, report


def output_paths(batch_path: Path, output_prefix: str, keep_only: bool) -> tuple[Path, Path, Path, Path]:
    prefix = output_prefix or batch_path.stem
    suffix = "keep_reviewed" if keep_only else "reviewed"
    report_suffix = "keep_review" if keep_only else "review"
    reviewed_jsonl = batch_path.with_name(f"{prefix}_{suffix}.jsonl")
    reviewed_xlsx = BATCH_XLSX_DIR / f"{prefix}_{suffix}.xlsx"
    report_xlsx = REVIEW_DIR / f"{prefix}_{report_suffix}.xlsx"
    summary_json = REVIEW_DIR / f"{prefix}_{report_suffix}_summary.json"
    return reviewed_jsonl, reviewed_xlsx, report_xlsx, summary_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-jsonl", required=True, type=Path)
    parser.add_argument("--batch-type", choices=["video", "ppt", "audio", "structured", "reference", "auto"], default="auto")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--keep-only", action="store_true", help="Write only keep rows to reviewed JSONL/XLSX.")
    args = parser.parse_args()

    batch_path = args.batch_jsonl
    batch_type = infer_type(batch_path) if args.batch_type == "auto" else args.batch_type
    rows = read_jsonl(batch_path)
    reviewed_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    for row in rows:
        if batch_type == "video":
            reviewed, report = review_video(row)
        elif batch_type == "ppt":
            reviewed, report = review_ppt(row)
        elif batch_type == "audio":
            reviewed, report = review_audio(row)
        elif batch_type == "structured":
            reviewed, report = review_structured(row)
        else:
            reviewed, report = review_reference(row)
        reviewed_rows.append(reviewed)
        report_rows.append(report)

    output_rows = [row for row in reviewed_rows if row.get("auto_review_decision") == "keep"] if args.keep_only else reviewed_rows
    reviewed_jsonl, reviewed_xlsx, report_xlsx, summary_json = output_paths(batch_path, args.output_prefix, args.keep_only)
    write_jsonl(reviewed_jsonl, output_rows)
    reviewed_xlsx_path = write_excel_with_fallback(pd.DataFrame(output_rows), reviewed_xlsx)
    report_xlsx_path = write_excel_with_fallback(pd.DataFrame(report_rows), report_xlsx)

    decision_counts = Counter(row.get("auto_review_decision") for row in reviewed_rows)
    summary = {
        "batch_jsonl": str(batch_path),
        "batch_type": batch_type,
        "input_rows": len(rows),
        "reviewed_rows_written": len(output_rows),
        "keep_only": args.keep_only,
        "decision_counts": dict(decision_counts),
        "reviewed_jsonl": str(reviewed_jsonl),
        "reviewed_xlsx": str(reviewed_xlsx_path),
        "report_xlsx": str(report_xlsx_path),
    }
    write_json(summary_json, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
