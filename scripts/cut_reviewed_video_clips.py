#!/usr/bin/env python
"""Cut reviewed video time ranges into physical MP4 clips.

This prevents the student reader from loading long source videos when the
textbook only needs a short reviewed segment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cut digital-book video blocks into physical clips.")
    parser.add_argument("--book-dir", type=Path, required=True, help="Directory containing digital_book.json.")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--reencode", action="store_true", help="Re-encode clips for accurate boundaries.")
    parser.add_argument("--package-output", type=Path, default=None, help="Optional zip package to rebuild.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = args.book_dir.resolve()
    book_json = book_dir / "digital_book.json"
    if not book_json.exists():
        raise SystemExit(f"Missing digital_book.json: {book_json}")

    data = json.loads(book_json.read_text(encoding="utf-8"))
    clip_dir = book_dir / "assets" / "videos" / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)

    cut_rows = []
    skipped_rows = []
    for block in iter_video_blocks(data):
        src = str(block.get("src") or "")
        if not src or "/clips/" in src.replace("\\", "/"):
            skipped_rows.append({"block_id": block.get("block_id"), "reason": "already_clip_or_no_src"})
            continue

        start_text = str(block.get("start_time") or "")
        end_text = str(block.get("end_time") or "")
        start_seconds = time_to_seconds(start_text)
        end_seconds = time_to_seconds(end_text)
        if end_seconds <= start_seconds:
            skipped_rows.append({"block_id": block.get("block_id"), "reason": "invalid_time_range"})
            continue

        source_path = (book_dir / src).resolve()
        if not source_path.exists():
            skipped_rows.append({"block_id": block.get("block_id"), "reason": f"missing_source:{source_path}"})
            continue

        duration = end_seconds - start_seconds
        clip_name = f"{safe_name(str(block.get('block_id') or source_path.stem))}_{start_seconds}_{end_seconds}.mp4"
        clip_path = clip_dir / clip_name
        cut_clip(
            ffmpeg=args.ffmpeg,
            source_path=source_path,
            clip_path=clip_path,
            start_seconds=start_seconds,
            duration_seconds=duration,
            reencode=args.reencode,
        )

        metadata = dict(block.get("metadata") or {})
        metadata["source_video_src"] = src
        metadata["source_start_time"] = start_text
        metadata["source_end_time"] = end_text
        block["metadata"] = metadata
        block["src"] = "assets/videos/clips/" + clip_name
        block["start_time"] = "00:00:00"
        block["end_time"] = seconds_to_time(duration)
        cut_rows.append(
            {
                "block_id": block.get("block_id"),
                "clip": block["src"],
                "source": src,
                "source_start_time": start_text,
                "source_end_time": end_text,
                "duration_seconds": duration,
            }
        )

    book_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "book_dir": str(book_dir),
        "clips_cut": len(cut_rows),
        "clips": cut_rows,
        "skipped": skipped_rows,
    }
    report_path = book_dir / "video_clip_cut_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.package_output:
        write_zip(book_dir, args.package_output.resolve())

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def iter_video_blocks(data: dict):
    for project in data.get("projects", []):
        for task in project.get("tasks", []):
            for block in task.get("blocks", []):
                if block.get("type") == "video":
                    yield block


def cut_clip(
    *,
    ffmpeg: str,
    source_path: Path,
    clip_path: Path,
    start_seconds: int,
    duration_seconds: int,
    reencode: bool,
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-ss", str(start_seconds), "-i", str(source_path), "-t", str(duration_seconds)]
    if reencode:
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart"])
    else:
        command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero"])
    command.append(str(clip_path))
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_zip(book_dir: Path, output_zip: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_zip, "w", ZIP_DEFLATED) as archive:
        for path in book_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(book_dir.parent).as_posix())


def time_to_seconds(value: str) -> int:
    parts = [int(float(part)) for part in str(value or "").split(":") if part != ""]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def seconds_to_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def safe_name(value: str) -> str:
    keep = []
    for char in value:
        if char.isalnum() or char in {"_", "-"}:
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep).strip("_") or "clip"


if __name__ == "__main__":
    raise SystemExit(main())
