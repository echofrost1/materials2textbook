#!/usr/bin/env python
"""Process queued audio and spreadsheet assets into batch evidence files.

This complements process_material_block_mvp.py, which handles video and PPT.
It reads next_processing_queue.xlsx so the same front classification and block
mapping drive all material types.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any

import pandas as pd

from process_material_block_mvp import (
    clean_text,
    format_time,
    load_asr_model,
    parse_time,
    safe_stem,
    transcribe_audio,
)


PROJECT_ROOT = default_work_root()
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
WORK_DIR = PROJECT_ROOT / "02_working_processing"
JSON_DIR = WORK_DIR / "json"
BATCH_JSON_DIR = JSON_DIR / "batches"
BATCH_MANIFEST_DIR = MANIFEST_DIR / "batches"

AUDIO_MAIN_JSONL = JSON_DIR / "audio_segments.jsonl"
AUDIO_MAIN_XLSX = MANIFEST_DIR / "audio_segments.xlsx"
STRUCTURED_MAIN_JSONL = JSON_DIR / "structured_assets.jsonl"
STRUCTURED_MAIN_XLSX = MANIFEST_DIR / "structured_assets.xlsx"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def source_path_for(row: pd.Series) -> Path:
    absolute = clean_text(row.get("absolute_path"))
    if absolute:
        path = Path(absolute)
        if path.exists():
            return path
    return PROJECT_ROOT / clean_text(row.get("original_path"))


def ffprobe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])


def next_number(rows: list[dict[str, Any]], field: str) -> int:
    max_num = 0
    for row in rows:
        match = re.search(r"(\d+)$", clean_text(row.get(field)))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1


def transcript_for_range(transcript: str, start: float, end: float) -> str:
    lines = []
    for line in transcript.splitlines():
        match = re.match(r"\[(\d+:\d+:\d+)\s+-->\s+(\d+:\d+:\d+)\]\s*(.*)", line)
        if not match:
            continue
        line_start = parse_time(match.group(1))
        line_end = parse_time(match.group(2))
        if line_end >= start and line_start <= end:
            lines.append(line)
    return "\n".join(lines) if lines else transcript[:1200]


def choose_audio_ranges(duration: float) -> list[tuple[float, float]]:
    if duration <= 0:
        return [(0, 90)]
    if duration <= 150:
        return [(0, duration)]
    parts = max(2, min(4, math.ceil(duration / 150)))
    step = duration / parts
    ranges = []
    for index in range(parts):
        start = index * step
        end = duration if index == parts - 1 else (index + 1) * step
        if end - start >= 15:
            ranges.append((start, end))
    return ranges


def load_queue() -> pd.DataFrame:
    queue = pd.read_excel(MANIFEST_DIR / "next_processing_queue.xlsx")
    return queue[queue["queue_status"].eq("Queued")].copy()


def select_queue_rows(target_block: str, action: str, limit: int) -> pd.DataFrame:
    queue = load_queue()
    selected = queue[
        queue["material_block_code"].eq(target_block)
        & queue["recommended_action"].eq(action)
    ].copy()
    if selected.empty:
        return selected
    relation_priority = {"primary": 0, "secondary": 1, "candidate": 2}
    selected["_relation_priority"] = selected["relation_type"].map(relation_priority).fillna(8)
    selected = selected.sort_values(["_relation_priority", "confidence", "asset_id"], ascending=[True, False, True])
    selected = selected.drop(columns=["_relation_priority"])
    if limit > 0:
        selected = selected.head(limit)
    return selected


def process_audio(target_block: str, limit: int, batch_id: str, dry_run: bool) -> int:
    selected = select_queue_rows(target_block, "process_audio_mvp", limit)
    if dry_run:
        print(f"audio_selected: {len(selected)}")
        for _, row in selected.iterrows():
            print(f"  audio {row['asset_id']} {row['filename']} [{row['relation_type']}]")
        return 0
    if selected.empty:
        print("No queued audio selected.")
        return 0

    existing = read_jsonl(AUDIO_MAIN_JSONL)
    next_id = next_number(existing, "audio_segment_id")
    model = load_asr_model()
    rows: list[dict[str, Any]] = []
    for _, asset in selected.iterrows():
        asset_id = clean_text(asset.get("asset_id"))
        filename = clean_text(asset.get("filename"))
        print(f"Processing audio {asset_id} {filename}")
        source_path = source_path_for(asset)
        wav_path = WORK_DIR / "audio" / f"{asset_id}_{safe_stem(filename)}.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        if not wav_path.exists():
            run_ffmpeg(["-i", str(source_path), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)])
        duration = float(asset.get("duration_or_pages") or 0) or ffprobe_duration(source_path)
        transcript_path = WORK_DIR / "transcripts" / f"{asset_id}_{safe_stem(filename)}.txt"
        if transcript_path.exists():
            transcript = transcript_path.read_text(encoding="utf-8", errors="ignore")
        else:
            transcript = transcribe_audio(model, wav_path)
            transcript_path.write_text(transcript, encoding="utf-8")
        for start, end in choose_audio_ranges(duration):
            segment_text = transcript_for_range(transcript, start, end)
            audio_segment_id = f"AUD{next_id:06d}"
            next_id += 1
            rows.append(
                {
                    "audio_segment_id": audio_segment_id,
                    "source_asset_id": asset_id,
                    "source_audio": filename,
                    "original_path": clean_text(asset.get("original_path")),
                    "start_time": format_time(start),
                    "end_time": format_time(end),
                    "subject": clean_text(asset.get("material_block_cn")),
                    "material_block": clean_text(asset.get("material_block_cn")),
                    "material_block_code": target_block,
                    "knowledge_point": clean_text(asset.get("knowledge_point_cn")) or Path(filename).stem,
                    "segment_summary": clean_text(segment_text, 220),
                    "tags": ";".join(filter(None, [clean_text(asset.get("material_block_cn")), Path(filename).stem])),
                    "recommended_chapter": clean_text(asset.get("material_block_cn")),
                    "usefulness_score": 3,
                    "transcript_status": "DONE" if segment_text else "EMPTY",
                    "transcript_text": segment_text,
                    "evidence_text": segment_text,
                    "boundary_reason": "audio_mvp rough split by duration; review before textbook use",
                    "review_status": "Pending_Agent_Review",
                    "review_comment": "",
                    "audio_wav": wav_path.relative_to(PROJECT_ROOT).as_posix(),
                    "classification_basis": "queue + asr",
                    "generated_time": datetime.now().isoformat(timespec="seconds"),
                }
            )
    out_jsonl = BATCH_JSON_DIR / f"{target_block}_audio_segments_{batch_id}.jsonl"
    out_xlsx = BATCH_MANIFEST_DIR / f"{target_block}_audio_segments_{batch_id}.xlsx"
    write_jsonl(out_jsonl, rows)
    write_excel_with_fallback(pd.DataFrame(rows), out_xlsx)
    return len(rows)


def detect_table_type(df: pd.DataFrame) -> str:
    text = " ".join(str(item) for item in list(df.columns) + df.head(5).fillna("").astype(str).to_numpy().ravel().tolist())
    if any(term in text for term in ["题", "答案", "选择", "判断", "试卷", "考试"]):
        return "question_bank"
    if any(term in text for term in ["评分", "鉴定", "考核", "工位", "操作"]):
        return "assessment_sheet"
    if any(term in text for term in ["材料", "设备", "清单", "数量", "规格"]):
        return "checklist_or_bom"
    return "spreadsheet_reference"


def preview_table(df: pd.DataFrame, max_rows: int = 20, max_chars: int = 1800) -> str:
    sample = df.head(max_rows).fillna("")
    text = sample.to_csv(index=False)
    return clean_text(text, max_chars)


def process_structured(target_block: str, limit: int, batch_id: str, dry_run: bool) -> int:
    selected = select_queue_rows(target_block, "extract_structured_evidence", limit)
    if dry_run:
        print(f"structured_selected: {len(selected)}")
        for _, row in selected.iterrows():
            print(f"  structured {row['asset_id']} {row['filename']} [{row['relation_type']}]")
        return 0
    if selected.empty:
        print("No queued structured assets selected.")
        return 0

    existing = read_jsonl(STRUCTURED_MAIN_JSONL)
    next_id = next_number(existing, "structured_asset_id")
    rows: list[dict[str, Any]] = []
    for _, asset in selected.iterrows():
        asset_id = clean_text(asset.get("asset_id"))
        filename = clean_text(asset.get("filename"))
        print(f"Processing structured {asset_id} {filename} for {target_block}")
        source_path = source_path_for(asset)
        try:
            sheets = pd.read_excel(source_path, sheet_name=None)
        except Exception as exc:
            sheets = {"read_error": pd.DataFrame([{"error": str(exc)}])}
        for sheet_name, df in sheets.items():
            table_type = detect_table_type(df)
            evidence = preview_table(df)
            structured_asset_id = f"STR{next_id:06d}"
            next_id += 1
            rows.append(
                {
                    "structured_asset_id": structured_asset_id,
                    "source_asset_id": asset_id,
                    "source_file": filename,
                    "original_path": clean_text(asset.get("original_path")),
                    "sheet_name": clean_text(sheet_name),
                    "table_type": table_type,
                    "subject": clean_text(asset.get("material_block_cn")),
                    "material_block": clean_text(asset.get("material_block_cn")),
                    "material_block_code": target_block,
                    "knowledge_point": clean_text(asset.get("knowledge_point_cn")) or Path(filename).stem,
                    "recommended_chapter": clean_text(asset.get("material_block_cn")),
                    "row_count": len(df),
                    "column_count": len(df.columns),
                    "columns": ";".join(clean_text(col) for col in df.columns),
                    "extracted_text": evidence,
                    "evidence_text": evidence,
                    "classification_basis": "queue + spreadsheet_preview",
                    "review_status": "Pending_Agent_Review",
                    "review_comment": "",
                    "generated_time": datetime.now().isoformat(timespec="seconds"),
                }
            )
    out_jsonl = BATCH_JSON_DIR / f"{target_block}_structured_assets_{batch_id}.jsonl"
    out_xlsx = BATCH_MANIFEST_DIR / f"{target_block}_structured_assets_{batch_id}.xlsx"
    write_jsonl(out_jsonl, rows)
    write_excel_with_fallback(pd.DataFrame(rows), out_xlsx)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-block", required=True)
    parser.add_argument("--limit-audio", type=int, default=3)
    parser.add_argument("--limit-structured", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_count = process_audio(args.target_block, args.limit_audio, batch_id, args.dry_run)
    structured_count = process_structured(args.target_block, args.limit_structured, batch_id, args.dry_run)
    if not args.dry_run:
        print("Done.")
        print(f"target_block={args.target_block}")
        print(f"batch_id={batch_id}")
        print(f"new_audio_segments={audio_count}")
        print(f"new_structured_assets={structured_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
