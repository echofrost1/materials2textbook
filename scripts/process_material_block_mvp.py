#!/usr/bin/env python
"""Process one material block into video/PPT JSONL evidence.

This is a small, repo-local MVP for continuing upstream material processing
after the TIG pilot. It reads the existing manifest/block mapping, selects a
limited batch for one material block, and writes processed evidence to batch
files by default:

  local_runs/work_material1/01_manifest_inventory/video_segments.xlsx
  local_runs/work_material1/02_working_processing/json/video_segments.jsonl
  local_runs/work_material1/01_manifest_inventory/ppt_assets.xlsx
  local_runs/work_material1/02_working_processing/json/ppt_assets.jsonl

Use --merge-main only after validation if you want to append batch rows to the
main textbook-generation inputs. It is intentionally conservative: process a
small batch, keep raw files unchanged, and write enough provenance for
downstream textbook generation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any, Dict, Iterable, List
from xml.etree import ElementTree as ET

import pandas as pd


os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

PROJECT_ROOT = default_work_root()
RAW_DIR = default_raw_root() / "谢志怡工作整理"
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
WORK_DIR = PROJECT_ROOT / "02_working_processing"
JSON_DIR = WORK_DIR / "json"
CONVERTED_PPTX_DIR = WORK_DIR / "converted_pptx"

VIDEO_SEGMENTS_XLSX = MANIFEST_DIR / "video_segments.xlsx"
VIDEO_SEGMENTS_JSONL = JSON_DIR / "video_segments.jsonl"
PPT_ASSETS_XLSX = MANIFEST_DIR / "ppt_assets.xlsx"
PPT_ASSETS_JSONL = JSON_DIR / "ppt_assets.jsonl"
BATCH_JSON_DIR = JSON_DIR / "batches"
BATCH_MANIFEST_DIR = MANIFEST_DIR / "batches"
SOFFICE_BIN = Path("/opt/libreoffice7.6/program/soffice")

BLOCK_CHAPTERS = {
    "shielded_metal_arc_welding": ("焊条电弧焊", "焊条电弧焊基本操作"),
    "welding_basic_operation": ("焊接基本操作", "焊接基本操作"),
    "welding_equipment_safety": ("焊接设备与安全", "焊接设备与安全"),
    "welding_defects_quality": ("焊接缺陷与质量检验", "焊接缺陷与质量检验"),
    "welding_training_assessment": ("综合实训与考核", "综合实训与考核"),
    "gas_welding_and_cutting": ("气焊与气割", "气焊与气割基本操作"),
    "tig_welding": ("钨极氩弧焊", "钨极氩弧焊基本操作"),
}

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def clean_text(value: Any, limit: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, float) and math.isnan(value):
        text = ""
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit is not None else text


def safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r'[<>:"/\\|?*\s]+', "_", stem).strip("_")
    return stem or "asset"


def source_path_for(asset: pd.Series) -> Path:
    absolute = clean_text(asset.get("absolute_path"))
    if absolute:
        path = Path(absolute)
        if path.exists():
            return path
    return RAW_DIR / str(asset["original_path"])


def ensure_pptx_for_processing(asset_id: str, source_path: Path) -> Path | None:
    suffix = source_path.suffix.lower()
    if suffix == ".pptx":
        return source_path
    if suffix != ".ppt":
        return None
    if not SOFFICE_BIN.exists():
        print(f"Skipping .ppt without LibreOffice {asset_id} {source_path.name}")
        return None

    out_dir = CONVERTED_PPTX_DIR / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = out_dir / f"{source_path.stem}.pptx"
    if expected.exists() and expected.stat().st_size > 0:
        return expected

    print(f"Converting PPT {asset_id} {source_path.name}")
    profile_dir = out_dir / "_lo_profile"
    result = subprocess.run(
        [
            str(SOFFICE_BIN),
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation=file://{profile_dir.resolve()}",
            "--convert-to",
            "pptx",
            "--outdir",
            str(out_dir),
            str(source_path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        print(f"Skipping unconverted ppt {asset_id}: {clean_text(result.stderr or result.stdout, 300)}")
        return None

    converted = sorted(path for path in out_dir.glob("*.pptx") if path.stat().st_size > 0)
    if not converted:
        print(f"Skipping unconverted ppt {asset_id}: no pptx output")
        return None
    return converted[0]


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
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


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def existing_source_asset_ids(file_type: str) -> set[str]:
    if file_type == "video":
        paths = [VIDEO_SEGMENTS_JSONL, *BATCH_JSON_DIR.glob("*_video_segments_*.jsonl")]
    else:
        paths = [PPT_ASSETS_JSONL, *BATCH_JSON_DIR.glob("*_ppt_assets_*.jsonl")]
    ids: set[str] = set()
    for path in paths:
        ids.update(str(row.get("source_asset_id")) for row in read_jsonl(path) if row.get("source_asset_id"))
    return ids


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


def safe_duration(value: Any) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(duration) or math.isinf(duration):
        return 0.0
    return max(0.0, duration)


def run_ffmpeg(args: List[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])


def format_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def parse_time(value: str) -> int:
    parts = [int(float(part)) for part in str(value).split(":") if part != ""]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def choose_segment_ranges(duration: float) -> List[tuple[float, float]]:
    duration = safe_duration(duration)
    if duration <= 0:
        return [(0, 90)]
    if duration <= 90:
        return [(0, duration)]
    parts = max(2, min(5, math.ceil(duration / 120)))
    step = duration / parts
    ranges = []
    for i in range(parts):
        start = i * step
        end = duration if i == parts - 1 else (i + 1) * step
        if end - start >= 12:
            ranges.append((start, end))
    return ranges


def load_source_tables() -> pd.DataFrame:
    manifest = pd.read_excel(MANIFEST_DIR / "assets_manifest.xlsx")
    active = pd.read_excel(MANIFEST_DIR / "active_assets.xlsx")
    blocks = pd.read_excel(MANIFEST_DIR / "asset_block_map.xlsx")
    return (
        blocks.merge(active[["asset_id", "active_for_processing"]], on="asset_id", how="left")
        .merge(manifest, on="asset_id", how="left", suffixes=("_block", ""))
    )


def select_assets(target_block: str, file_type: str, limit: int) -> pd.DataFrame:
    if limit <= 0:
        return pd.DataFrame()
    df = load_source_tables()
    df = df[
        df["material_block_code"].eq(target_block)
        & df["file_type"].eq(file_type)
        & df["active_for_processing"].eq(True)
    ].copy()
    existing_ids = existing_source_asset_ids(file_type)
    df = df[~df["asset_id"].astype(str).isin(existing_ids)].copy()
    df["duration_sort"] = df["duration_seconds"].fillna(999999).astype(float)
    relation_priority = {"primary": 0, "secondary": 1, "candidate": 2, "exclude": 9}
    df["relation_priority"] = df["relation_type"].map(relation_priority).fillna(8)
    df = df.sort_values(
        ["relation_priority", "confidence", "duration_sort", "asset_id"],
        ascending=[True, False, True, True],
    ).drop(columns=["relation_priority"])
    df = df.head(limit)
    return df


def load_asr_model() -> Any:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        print(f"warning: faster-whisper unavailable, ASR skipped: {exc}")
        return None
    try:
        return WhisperModel("small", device="cuda", compute_type="float16")
    except Exception as exc:
        print(f"warning: faster-whisper CUDA unavailable, falling back to CPU int8: {exc}")
        try:
            return WhisperModel("small", device="cpu", compute_type="int8")
        except Exception as cpu_exc:
            print(f"warning: faster-whisper unavailable, ASR skipped: {cpu_exc}")
            return None


def transcribe_audio(model: Any, wav_path: Path) -> str:
    if model is None:
        return ""
    try:
        segments, _info = model.transcribe(str(wav_path), language="zh", vad_filter=True)
        lines = []
        for seg in segments:
            lines.append(f"[{format_time(seg.start)} --> {format_time(seg.end)}] {seg.text.strip()}")
        return "\n".join(lines)
    except Exception as exc:
        print(f"warning: ASR failed for {wav_path.name}: {exc}")
        return ""


def extract_keyframes(mp4_path: Path, asset_id: str, duration: float) -> str:
    out_dir = WORK_DIR / "keyframes" / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = safe_duration(duration)
    if duration <= 0:
        duration = ffprobe_duration(mp4_path)
    points = [duration * ratio for ratio in (0.2, 0.5, 0.8) if duration > 0]
    paths: List[str] = []
    for idx, point in enumerate(points, start=1):
        out = out_dir / f"frame_{idx:02d}_{int(point)}s.jpg"
        if not out.exists():
            try:
                run_ffmpeg(["-ss", str(point), "-i", str(mp4_path), "-frames:v", "1", "-q:v", "2", str(out)])
            except Exception as exc:
                print(f"warning: keyframe failed {asset_id} {point:.1f}s: {exc}")
                continue
        if out.exists() and out.stat().st_size > 0:
            paths.append(out.relative_to(PROJECT_ROOT).as_posix())
        else:
            print(f"warning: keyframe missing after extraction {asset_id} {point:.1f}s")
    return ";".join(paths)


def convert_and_extract(asset: pd.Series) -> tuple[Path, Path, float, str]:
    asset_id = str(asset["asset_id"])
    raw_path = source_path_for(asset)
    stem = f"{asset_id}_{safe_stem(str(asset['filename']))}"
    mp4_path = WORK_DIR / "converted_mp4" / f"{stem}.mp4"
    wav_path = WORK_DIR / "audio" / f"{stem}.wav"
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    if not mp4_path.exists():
        if raw_path.suffix.lower() == ".mp4":
            shutil.copy2(raw_path, mp4_path)
        else:
            run_ffmpeg(["-i", str(raw_path), "-c:v", "libx264", "-c:a", "aac", str(mp4_path)])
    audio_status = "DONE"
    if not wav_path.exists():
        try:
            run_ffmpeg(["-i", str(mp4_path), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)])
        except RuntimeError as exc:
            print(f"warning: audio extraction failed for {asset_id}: {exc}")
            audio_status = "NO_AUDIO"
    duration = safe_duration(asset.get("duration_seconds")) or ffprobe_duration(mp4_path)
    return mp4_path, wav_path, duration, audio_status


def next_clip_number(existing: List[Dict[str, Any]]) -> int:
    max_num = 0
    for row in existing:
        match = re.search(r"(\d+)$", str(row.get("clip_id", "")))
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


def process_videos(target_block: str, limit: int, merge_main: bool, batch_id: str) -> int:
    block_name, chapter = BLOCK_CHAPTERS.get(target_block, (target_block, target_block))
    selected = select_assets(target_block, "video", limit)
    if selected.empty:
        print("No new videos selected.")
        return 0
    existing = read_jsonl(VIDEO_SEGMENTS_JSONL)
    next_num = next_clip_number(existing)
    model = load_asr_model()
    new_rows: List[Dict[str, Any]] = []
    for _, asset in selected.iterrows():
        asset_id = str(asset["asset_id"])
        print(f"Processing video {asset_id} {asset['filename']}")
        try:
            mp4_path, wav_path, duration, audio_status = convert_and_extract(asset)
        except Exception as exc:
            print(f"warning: video conversion failed for {asset_id}: {clean_text(exc, 300)}")
            continue
        transcript_path = WORK_DIR / "transcripts" / f"{asset_id}_{safe_stem(str(asset['filename']))}.txt"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        if transcript_path.exists():
            transcript = transcript_path.read_text(encoding="utf-8", errors="ignore")
        elif audio_status == "NO_AUDIO":
            transcript = ""
            transcript_path.write_text(transcript, encoding="utf-8")
        else:
            transcript = transcribe_audio(model, wav_path)
            transcript_path.write_text(transcript, encoding="utf-8")
        keyframes = extract_keyframes(mp4_path, asset_id, duration)
        ranges = choose_segment_ranges(duration)
        kp = clean_text(asset.get("knowledge_point_cn")) or clean_text(asset.get("filename"))
        for index, (start, end) in enumerate(ranges, start=1):
            clip_id = f"C{next_num:06d}"
            next_num += 1
            text = transcript_for_range(transcript, start, end)
            if not clean_text(text):
                text = clean_text(
                    f"{block_name}视频候选片段：{asset['filename']}；"
                    f"知识点：{kp}；时间范围：{format_time(start)}-{format_time(end)}；"
                    "当前环境未安装 ASR，证据先由文件名、素材块、时间段和关键帧生成，待后续转写补充。"
                )
            row = {
                "clip_id": clip_id,
                "source_asset_id": asset_id,
                "source_video": asset["filename"],
                "original_path": asset["original_path"],
                "start_time": format_time(start),
                "end_time": format_time(end),
                "subject": "焊接技术",
                "material_block": block_name,
                "material_block_code": target_block,
                "knowledge_point": kp,
                "clip_summary": f"{kp}候选片段 {index}，由 {block_name} 批处理自动生成。",
                "tags": f"{block_name};操作演示;批处理MVP",
                "recommended_chapter": chapter,
                "usefulness_score": 0.6,
                "quality_score": "",
                "transcript_status": "DONE" if transcript else "PENDING_ASR",
                "transcript_text": text,
                "ocr_text": "",
                "keyframe_paths": keyframes,
                "evidence_text": text,
                "boundary_reason": "MVP 阶段按视频时长粗分，后续需要结合字幕、画面变化和人工/agent 审核调整边界。",
                "review_status": "Pending_Agent_Review",
                "review_comment": "",
                "clip_output_path": "",
                "processing_batch_id": f"{target_block}_{datetime.now().strftime('%Y%m%d')}",
                "processing_route": "material_block_mvp",
                "convert_status": "DONE",
                "audio_status": audio_status,
                "converted_mp4": mp4_path.relative_to(PROJECT_ROOT).as_posix(),
                "audio_wav": wav_path.relative_to(PROJECT_ROOT).as_posix(),
                "transcript_txt": transcript_path.relative_to(PROJECT_ROOT).as_posix(),
            }
            new_rows.append(row)
    BATCH_JSON_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    batch_json = BATCH_JSON_DIR / f"{target_block}_video_segments_{batch_id}.jsonl"
    batch_xlsx = BATCH_MANIFEST_DIR / f"{target_block}_video_segments_{batch_id}.xlsx"
    write_jsonl(batch_json, new_rows)
    write_excel_with_fallback(pd.DataFrame(new_rows), batch_xlsx)
    if merge_main:
        all_rows = existing + new_rows
        write_jsonl(VIDEO_SEGMENTS_JSONL, all_rows)
        write_excel_with_fallback(pd.DataFrame(all_rows), VIDEO_SEGMENTS_XLSX)
    return len(new_rows)


def ppt_slide_paths(zf: zipfile.ZipFile) -> List[str]:
    slides = [name for name in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)]
    return sorted(slides, key=lambda value: int(re.search(r"slide(\d+)\.xml", value).group(1)))


def extract_slide_text(zf: zipfile.ZipFile, slide_path: str) -> str:
    try:
        root = ET.fromstring(zf.read(slide_path))
    except Exception:
        return ""
    texts = [node.text or "" for node in root.findall(".//a:t", NS)]
    return clean_text(" ".join(texts))


def slide_relationships(zf: zipfile.ZipFile, slide_path: str) -> Dict[str, str]:
    rel_path = slide_path.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"
    if rel_path not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(rel_path))
    except Exception:
        return {}
    rels: Dict[str, str] = {}
    for rel in root:
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rid and "media/" in target:
            rels[rid] = str((Path("ppt/slides") / target).resolve()).replace("\\", "/")
    return rels


def extract_slide_images(zf: zipfile.ZipFile, slide_path: str, asset_id: str, slide_index: int) -> str:
    rels = slide_relationships(zf, slide_path)
    if not rels:
        return ""
    root = ET.fromstring(zf.read(slide_path))
    out_dir = WORK_DIR / "ppt_images" / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[str] = []
    for idx, blip in enumerate(root.findall(".//a:blip", NS), start=1):
        rid = blip.attrib.get(f"{{{NS['r']}}}embed", "")
        media_path = rels.get(rid)
        if not media_path or media_path not in zf.namelist():
            continue
        ext = Path(media_path).suffix or ".png"
        out = out_dir / f"slide_{slide_index:03d}_image_{idx:02d}{ext}"
        if not out.exists():
            out.write_bytes(zf.read(media_path))
        paths.append(out.relative_to(PROJECT_ROOT).as_posix())
    return ";".join(paths)


def next_ppt_asset_id(asset_id: str, slide_index: int) -> str:
    return f"PPT_{asset_id}_S{slide_index:03d}"


def image_label(text: str) -> str:
    if any(word in text for word in ["设备", "焊机", "焊钳", "电缆"]):
        return "设备示意"
    if any(word in text for word in ["坡口", "焊缝", "熔池", "电弧"]):
        return "焊接示意"
    if any(word in text for word in ["步骤", "操作", "目录"]):
        return "操作步骤图"
    return "课件图片"


def process_ppts(target_block: str, limit: int, merge_main: bool, batch_id: str) -> int:
    block_name, chapter = BLOCK_CHAPTERS.get(target_block, (target_block, target_block))
    selected = select_assets(target_block, "ppt", limit)
    if selected.empty:
        print("No new PPT selected.")
        return 0
    existing = read_jsonl(PPT_ASSETS_JSONL)
    new_rows: List[Dict[str, Any]] = []
    for _, asset in selected.iterrows():
        asset_id = str(asset["asset_id"])
        raw_path = ensure_pptx_for_processing(asset_id, source_path_for(asset))
        if raw_path is None:
            print(f"Skipping non-pptx {asset_id} {asset['filename']}")
            continue
        print(f"Processing PPT {asset_id} {asset['filename']}")
        try:
            zf_context = zipfile.ZipFile(raw_path)
        except zipfile.BadZipFile:
            print(f"Skipping invalid pptx {asset_id} {asset['filename']}")
            continue
        with zf_context as zf:
            for slide_index, slide_path in enumerate(ppt_slide_paths(zf), start=1):
                text = extract_slide_text(zf, slide_path)
                images = extract_slide_images(zf, slide_path, asset_id, slide_index)
                if not text and not images:
                    continue
                kp = clean_text(asset.get("knowledge_point_cn")) or block_name
                img_count = len([p for p in images.split(";") if p])
                row = {
                    "ppt_asset_id": next_ppt_asset_id(asset_id, slide_index),
                    "source_asset_id": asset_id,
                    "source_ppt": asset["filename"],
                    "original_path": asset["original_path"],
                    "slide_index": slide_index,
                    "slide_title": clean_text(text, 40),
                    "slide_text": text,
                    "subject": "焊接技术",
                    "material_block": block_name,
                    "material_block_code": target_block,
                    "knowledge_point": kp,
                    "recommended_chapter": chapter,
                    "evidence_text": clean_text(text, 500),
                    "image_paths": images,
                    "image_count": img_count,
                    "image_ocr_text": "",
                    "image_label": image_label(text) if img_count else "",
                    "usefulness_score": 0.75 if text else 0.55,
                    "review_status": "Pending_Agent_Review",
                    "review_comment": "",
                }
                new_rows.append(row)
    BATCH_JSON_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    batch_json = BATCH_JSON_DIR / f"{target_block}_ppt_assets_{batch_id}.jsonl"
    batch_xlsx = BATCH_MANIFEST_DIR / f"{target_block}_ppt_assets_{batch_id}.xlsx"
    write_jsonl(batch_json, new_rows)
    write_excel_with_fallback(pd.DataFrame(new_rows), batch_xlsx)
    if merge_main:
        all_rows = existing + new_rows
        write_jsonl(PPT_ASSETS_JSONL, all_rows)
        write_excel_with_fallback(pd.DataFrame(all_rows), PPT_ASSETS_XLSX)
    return len(new_rows)


def print_dry_run(target_block: str, limit_videos: int, limit_ppt: int) -> None:
    video = select_assets(target_block, "video", limit_videos)
    ppt = select_assets(target_block, "ppt", limit_ppt)
    print("Dry run:")
    print(f"  target_block: {target_block}")
    print(f"  videos_selected: {len(video)}")
    for _, row in video.iterrows():
        print(f"    video {row['asset_id']} {row['filename']} [{row.get('relation_type', '')}, confidence={row.get('confidence', '')}]")
    print(f"  ppt_selected: {len(ppt)}")
    for _, row in ppt.iterrows():
        print(f"    ppt   {row['asset_id']} {row['filename']} [{row.get('relation_type', '')}, confidence={row.get('confidence', '')}]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-block", default="shielded_metal_arc_welding")
    parser.add_argument("--limit-videos", type=int, default=8)
    parser.add_argument("--limit-ppt", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Only print selected assets; do not process media.")
    parser.add_argument(
        "--merge-main",
        action="store_true",
        help="Append batch outputs to main video_segments/ppt_assets. Default only writes batch files.",
    )
    args = parser.parse_args()
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.dry_run:
        print_dry_run(args.target_block, args.limit_videos, args.limit_ppt)
        return 0
    video_rows = process_videos(args.target_block, args.limit_videos, args.merge_main, batch_id)
    ppt_rows = process_ppts(args.target_block, args.limit_ppt, args.merge_main, batch_id)
    print("Done.")
    print(f"target_block={args.target_block}")
    print(f"batch_id={batch_id}")
    print(f"merge_main={args.merge_main}")
    print(f"new_video_segments={video_rows}")
    print(f"new_ppt_assets={ppt_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
