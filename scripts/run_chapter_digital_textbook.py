#!/usr/bin/env python
"""Run chapter-scoped digital textbook generation.

The script follows docs/textbook_generation_optimization_plan.md:

1. Build a chapter evidence pack and readiness report.
2. Use only that chapter's video/PPT/document JSONL inputs.
3. Generate a chapter-scoped draft/digital book in an isolated output folder.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATERIAL_ROOT = ROOT / "work_materials" / "work_material1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one chapter through the digital textbook workflow.")
    parser.add_argument("--material-root", type=Path, default=DEFAULT_MATERIAL_ROOT)
    parser.add_argument("--chapter", default="钨极氩弧焊")
    parser.add_argument("--title", default=None)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--max-input-tokens", type=int, default=120000)
    parser.add_argument("--max-chunks-per-knowledge-point", type=int, default=12)
    parser.add_argument("--max-video-records", type=int, default=0)
    parser.add_argument("--max-document-records", type=int, default=0)
    parser.add_argument("--review-rounds", type=int, default=1)
    parser.add_argument("--skip-build-pack", action="store_true")
    parser.add_argument("--copy-media-assets", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    material_root = args.material_root.resolve()
    chapter_slug = "tig_welding" if ("钨极" in args.chapter or "氩弧" in args.chapter) else safe_slug(args.chapter)
    chapter_root = material_root / "05_final_deliverables" / "chapter_work" / chapter_slug
    output_dir = chapter_root / "agent_workflow"
    package_output = chapter_root / "digital_book.zip"
    cache_path = output_dir / "llm_cache.json"

    if not args.skip_build_pack:
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_chapter_evidence_pack.py"),
                "--material-root",
                str(material_root),
                "--chapter",
                args.chapter,
                "--output-root",
                str(chapter_root),
            ]
        )

    video_segments = chapter_root / "chapter_video_segments.jsonl"
    ppt_assets = chapter_root / "chapter_ppt_assets.jsonl"
    document_segments = chapter_root / "chapter_document_segments.jsonl"
    for path in [video_segments, ppt_assets, document_segments]:
        if not path.exists():
            raise SystemExit(f"Missing chapter input: {path}")

    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_full_digital_textbook.py"),
        "--material-root",
        str(material_root),
        "--title",
        args.title or f"{args.chapter}数字教材样章",
        "--segments",
        str(video_segments),
        "--ppt-assets",
        str(ppt_assets),
        "--document-segments",
        str(document_segments),
        "--output-dir",
        str(output_dir),
        "--student-package-output",
        str(package_output),
        "--max-video-records",
        str(args.max_video_records),
        "--max-document-records",
        str(args.max_document_records),
        "--max-input-tokens",
        str(args.max_input_tokens),
        "--max-tokens-per-evidence-chunk",
        "1200",
        "--summarize-over-budget",
        "--summary-token-reserve-ratio",
        "0.25",
        "--max-tokens-per-summary-chunk",
        "700",
        "--max-summary-source-chunks",
        "8",
        "--max-chunks-per-knowledge-point",
        str(args.max_chunks_per_knowledge_point),
        "--review-rounds",
        str(args.review_rounds),
        "--llm-cache-path",
        str(cache_path),
    ]
    if args.use_llm:
        command.append("--use-llm")
    if args.copy_media_assets:
        command.append("--copy-media-assets")

    run(command)
    print("Chapter digital textbook generated:")
    print(f"- chapter_root: {chapter_root}")
    print(f"- readiness_report: {chapter_root / 'chapter_readiness_report.xlsx'}")
    print(f"- final_markdown: {output_dir / 'textbook_final.md'}")
    print(f"- digital_book_index: {chapter_root / 'digital_book' / 'index.html'}")
    print(f"- package: {package_output}")
    return 0


def run(command: list[str]) -> None:
    printable = " ".join(f'"{part}"' if " " in part else part for part in command)
    print(f"[chapter-runner] {printable}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def safe_slug(value: str) -> str:
    import re

    slug = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", value).strip("_")
    return slug or "chapter"


if __name__ == "__main__":
    raise SystemExit(main())
