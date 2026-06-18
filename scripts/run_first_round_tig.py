#!/usr/bin/env python
"""Run the first-round TIG material block pilot.

The pilot intentionally stays small:
- select 5 TIG-related videos from one source folder
- keep raw files untouched
- convert/copy to MP4 workspace files
- extract WAV audio
- run ASR if a local ASR package is available, otherwise write ASR_PENDING
- extract keyframes
- create manual-timecode-friendly video_segments.xlsx and JSONL
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any, Dict, Iterable, List, Optional, Tuple

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import pandas as pd


PROJECT_ROOT = default_work_root()
RAW_DIR = default_raw_root() / "谢志怡工作整理"
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
WORK_DIR = PROJECT_ROOT / "02_working_processing"
JSON_DIR = WORK_DIR / "json"

MATERIAL_BLOCK_CN = "钨极氩弧焊"
MATERIAL_BLOCK_CODE = "tig_welding"
PILOT_CHAPTER = "钨极氩弧焊基本操作"

SELECTED_REL_PATHS = [
    "第四周/初级标准7/基本原理.flv",
    "第四周/初级标准7/特点和适用范围.flv",
    "第四周/初级标准7/非接触引弧.flv",
    "第四周/初级标准7/送丝.flv",
    "第四周/初级标准7/收弧操作.flv",
]


@dataclass
class SelectedAsset:
    asset_id: str
    original_path: Path
    raw_relative_path: str
    filename: str
    file_stem: str
    duration_seconds: Optional[float]
    duration_or_pages: str
    converted_mp4: Path
    audio_wav: Path
    transcript_txt: Path
    keyframe_dir: Path


def run_command(args: List[str], timeout: int = 300) -> Tuple[int, str, str]:
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def safe_name(value: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in invalid else ch for ch in value).strip()
    return "_".join(cleaned.split()) or "asset"


def seconds_to_hhmmss(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def ffprobe_duration(path: Path) -> Optional[float]:
    code, out, _err = run_command(
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
        timeout=60,
    )
    if code != 0 or not out:
        return None
    try:
        return float(out)
    except ValueError:
        return None


def ensure_dirs() -> None:
    for path in [
        MANIFEST_DIR,
        WORK_DIR / "converted_mp4",
        WORK_DIR / "audio",
        WORK_DIR / "transcripts",
        WORK_DIR / "keyframes",
        JSON_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def build_assets() -> List[SelectedAsset]:
    assets: List[SelectedAsset] = []
    for idx, rel in enumerate(SELECTED_REL_PATHS, start=1):
        source = RAW_DIR / rel
        if not source.exists():
            raise FileNotFoundError(source)
        asset_id = f"TIG{idx:03d}"
        stem = source.stem
        safe_stem = safe_name(stem)
        duration = ffprobe_duration(source)
        assets.append(
            SelectedAsset(
                asset_id=asset_id,
                original_path=source,
                raw_relative_path=rel.replace("\\", "/"),
                filename=source.name,
                file_stem=stem,
                duration_seconds=duration,
                duration_or_pages=seconds_to_hhmmss(duration),
                converted_mp4=WORK_DIR / "converted_mp4" / f"{asset_id}_{safe_stem}.mp4",
                audio_wav=WORK_DIR / "audio" / f"{asset_id}_{safe_stem}.wav",
                transcript_txt=WORK_DIR / "transcripts" / f"{asset_id}_{safe_stem}.txt",
                keyframe_dir=WORK_DIR / "keyframes" / asset_id,
            )
        )
    return assets


def convert_to_mp4(asset: SelectedAsset) -> str:
    if asset.converted_mp4.exists():
        return "EXISTS"
    if asset.original_path.suffix.lower() == ".mp4":
        shutil.copy2(asset.original_path, asset.converted_mp4)
        return "COPIED"
    code, _out, err = run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(asset.original_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(asset.converted_mp4),
        ],
        timeout=900,
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg convert failed for {asset.filename}: {err[-1000:]}")
    return "CONVERTED"


def extract_audio(asset: SelectedAsset) -> str:
    if asset.audio_wav.exists():
        return "EXISTS"
    code, _out, err = run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(asset.converted_mp4),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(asset.audio_wav),
        ],
        timeout=600,
    )
    if code != 0:
        raise RuntimeError(f"ffmpeg audio failed for {asset.filename}: {err[-1000:]}")
    return "EXTRACTED"


def try_asr(asset: SelectedAsset) -> Tuple[str, str]:
    """Return (status, transcript_text)."""
    if asset.transcript_txt.exists():
        text = asset.transcript_txt.read_text(encoding="utf-8", errors="ignore")
        if text and not text.startswith("ASR_PENDING"):
            return "DONE", text

    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(asset.audio_wav), language="zh", vad_filter=True)
        lines = []
        for seg in segments:
            lines.append(f"[{seconds_to_hhmmss(seg.start)} --> {seconds_to_hhmmss(seg.end)}] {seg.text.strip()}")
        text = "\n".join(lines).strip()
        asset.transcript_txt.write_text(text + "\n", encoding="utf-8")
        return "DONE", text
    except Exception:
        pass

    try:
        import whisper  # type: ignore

        model = whisper.load_model("base")
        result = model.transcribe(str(asset.audio_wav), language="zh")
        lines = []
        for seg in result.get("segments", []):
            lines.append(
                f"[{seconds_to_hhmmss(seg.get('start'))} --> {seconds_to_hhmmss(seg.get('end'))}] {str(seg.get('text', '')).strip()}"
            )
        text = "\n".join(lines).strip() or str(result.get("text", "")).strip()
        asset.transcript_txt.write_text(text + "\n", encoding="utf-8")
        return "DONE", text
    except Exception:
        pending = (
            "ASR_PENDING\n"
            f"asset_id: {asset.asset_id}\n"
            f"source: {asset.raw_relative_path}\n"
            "reason: No supported local ASR package is installed. Install faster-whisper or whisper and rerun.\n"
        )
        asset.transcript_txt.write_text(pending, encoding="utf-8")
        return "PENDING_ASR", ""


def extract_keyframes(asset: SelectedAsset) -> List[str]:
    asset.keyframe_dir.mkdir(parents=True, exist_ok=True)
    duration = asset.duration_seconds or ffprobe_duration(asset.converted_mp4) or 0
    if duration <= 0:
        points = [1, 10, 20]
    else:
        points = sorted({max(1, int(duration * ratio)) for ratio in (0.1, 0.5, 0.9)})
    paths: List[str] = []
    for idx, point in enumerate(points, start=1):
        out = asset.keyframe_dir / f"frame_{idx:02d}_{point}s.jpg"
        if not out.exists():
            code, _stdout, err = run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(point),
                    "-i",
                    str(asset.converted_mp4),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(out),
                ],
                timeout=120,
            )
            if code != 0:
                print(f"warning: keyframe failed for {asset.asset_id} at {point}s: {err[-300:]}")
                continue
        if out.exists():
            paths.append(out.relative_to(PROJECT_ROOT).as_posix())
    return paths


def split_ranges(duration: Optional[float], parts: int = 3) -> List[Tuple[str, str]]:
    if not duration or duration <= 0:
        return [("00:00:00", "00:01:00")]
    total = int(duration)
    parts = max(1, min(parts, math.ceil(total / 30)))
    step = max(20, total // parts)
    ranges = []
    start = 0
    while start < total:
        end = min(total, start + step)
        if end - start < 10 and ranges:
            prev_start, _prev_end = ranges[-1]
            ranges[-1] = (prev_start, seconds_to_hhmmss(total))
            break
        ranges.append((seconds_to_hhmmss(start), seconds_to_hhmmss(end)))
        start = end
        if len(ranges) >= 5:
            if start < total:
                prev_start, _prev_end = ranges[-1]
                ranges[-1] = (prev_start, seconds_to_hhmmss(total))
            break
    return ranges


def hhmmss_to_seconds(value: str) -> int:
    parts = [int(float(part)) for part in str(value).split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def transcript_for_range(transcript_text: str, start_time: str, end_time: str) -> str:
    if not transcript_text:
        return ""
    start = hhmmss_to_seconds(start_time)
    end = hhmmss_to_seconds(end_time)
    lines: List[str] = []
    pattern = re.compile(r"^\[(\d\d:\d\d:\d\d)\s+-->\s+(\d\d:\d\d:\d\d)\]\s*(.*)$")
    for line in transcript_text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        seg_start = hhmmss_to_seconds(match.group(1))
        seg_end = hhmmss_to_seconds(match.group(2))
        if seg_end >= start and seg_start <= end:
            lines.append(line.strip())
    return "\n".join(lines).strip()


def knowledge_from_filename(filename: str) -> str:
    if "非接触引弧" in filename:
        return "非接触引弧"
    if "送丝" in filename:
        return "送丝"
    if "收弧" in filename:
        return "收弧操作"
    if "特点" in filename or "适用范围" in filename:
        return "钨极氩弧焊特点和适用范围"
    if "基本原理" in filename:
        return "钨极氩弧焊基本原理"
    return "钨极氩弧焊基本操作"


def write_outputs(asset_rows: List[Dict[str, Any]], segment_rows: List[Dict[str, Any]]) -> None:
    selected_df = pd.DataFrame(asset_rows)
    segments_df = pd.DataFrame(segment_rows)
    selected_path = MANIFEST_DIR / "first_round_selected_assets.xlsx"
    segments_path = MANIFEST_DIR / "video_segments.xlsx"
    jsonl_path = JSON_DIR / "video_segments.jsonl"
    test_path = MANIFEST_DIR / "chapter_material_test.xlsx"

    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        selected_df.to_excel(selected_path, index=False, engine="openpyxl")
    except PermissionError:
        selected_path = selected_path.with_name(f"{selected_path.stem}_{suffix}{selected_path.suffix}")
        selected_df.to_excel(selected_path, index=False, engine="openpyxl")

    try:
        segments_df.to_excel(segments_path, index=False, engine="openpyxl")
    except PermissionError:
        segments_path = segments_path.with_name(f"{segments_path.stem}_{suffix}{segments_path.suffix}")
        segments_df.to_excel(segments_path, index=False, engine="openpyxl")

    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in segment_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    chapter_df = segments_df[
        [
            "clip_id",
            "source_asset_id",
            "source_video",
            "start_time",
            "end_time",
            "knowledge_point",
            "clip_summary",
            "usefulness_score",
            "review_status",
            "evidence_text",
        ]
    ].copy()
    chapter_df.insert(0, "pilot_chapter", PILOT_CHAPTER)
    try:
        chapter_df.to_excel(test_path, index=False, engine="openpyxl")
    except PermissionError:
        test_path = test_path.with_name(f"{test_path.stem}_{suffix}{test_path.suffix}")
        chapter_df.to_excel(test_path, index=False, engine="openpyxl")

    print("Wrote:")
    for path in [selected_path, segments_path, jsonl_path, test_path]:
        print(f"  {path.resolve()}")


def main() -> int:
    ensure_dirs()
    assets = build_assets()
    asset_rows: List[Dict[str, Any]] = []
    segment_rows: List[Dict[str, Any]] = []

    clip_counter = 1
    for asset in assets:
        print(f"Processing {asset.asset_id} {asset.filename}")
        convert_status = convert_to_mp4(asset)
        audio_status = extract_audio(asset)
        asr_status, transcript_text = try_asr(asset)
        keyframes = extract_keyframes(asset)
        knowledge_point = knowledge_from_filename(asset.filename)

        asset_rows.append(
            {
                "asset_id": asset.asset_id,
                "material_block_cn": MATERIAL_BLOCK_CN,
                "material_block_code": MATERIAL_BLOCK_CODE,
                "filename": asset.filename,
                "file_type": "video",
                "original_path": asset.raw_relative_path,
                "duration_or_pages": asset.duration_or_pages,
                "converted_mp4": asset.converted_mp4.relative_to(PROJECT_ROOT).as_posix(),
                "audio_wav": asset.audio_wav.relative_to(PROJECT_ROOT).as_posix(),
                "transcript_txt": asset.transcript_txt.relative_to(PROJECT_ROOT).as_posix(),
                "keyframe_dir": asset.keyframe_dir.relative_to(PROJECT_ROOT).as_posix(),
                "convert_status": convert_status,
                "audio_status": audio_status,
                "asr_status": asr_status,
                "selected_reason": "第一轮钨极氩弧焊素材大块试点，来自同一目录以减少跨周重复干扰",
            }
        )

        ranges = split_ranges(asset.duration_seconds, parts=3)
        for idx, (start, end) in enumerate(ranges, start=1):
            transcript_excerpt = transcript_for_range(transcript_text, start, end)
            evidence = transcript_excerpt or "当前时间段没有匹配到 ASR 字幕；需要看原视频和关键帧复核。"
            segment_rows.append(
                {
                    "clip_id": f"C{clip_counter:06d}",
                    "source_asset_id": asset.asset_id,
                    "source_video": asset.filename,
                    "original_path": asset.raw_relative_path,
                    "start_time": start,
                    "end_time": end,
                    "subject": "焊接技术",
                    "material_block": MATERIAL_BLOCK_CN,
                    "material_block_code": MATERIAL_BLOCK_CODE,
                    "knowledge_point": knowledge_point,
                    "clip_summary": f"{knowledge_point}候选片段 {idx}，待人工根据画面和字幕确认边界。",
                    "tags": "钨极氩弧焊;操作演示;第一轮试点",
                    "recommended_chapter": PILOT_CHAPTER,
                    "usefulness_score": 0.6 if asr_status == "DONE" else 0.4,
                    "quality_score": "",
                    "transcript_status": asr_status,
                    "transcript_text": transcript_excerpt,
                    "ocr_text": "",
                    "keyframe_paths": ";".join(keyframes),
                    "evidence_text": evidence,
                    "boundary_reason": "MVP 阶段按视频时长粗分，需人工调整 start_time / end_time。",
                    "review_status": "Pending_Manual_Timecode",
                    "review_comment": "",
                    "clip_output_path": "",
                }
            )
            clip_counter += 1

    write_outputs(asset_rows, segment_rows)
    print(f"Done. assets={len(asset_rows)}, candidate_segments={len(segment_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
