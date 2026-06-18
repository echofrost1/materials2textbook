#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.io_utils import read_jsonl, write_json, write_text
from materials2textbook.validators.video_segments import (
    render_segment_validation_markdown,
    validate_video_segments,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate upstream video_segments.jsonl before textbook workflow.")
    parser.add_argument(
        "--segments",
        type=Path,
        default=ROOT / "work_material1" / "02_working_processing" / "json" / "video_segments.jsonl",
        help="Input video_segments.jsonl from the material-processing pipeline.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables" / "validation",
        help="Directory for validation_report.json and validation_report.md.",
    )
    parser.add_argument(
        "--fail-on-high",
        action="store_true",
        help="Exit with code 2 when high-severity issues are found.",
    )
    args = parser.parse_args()

    records = read_jsonl(args.segments.resolve())
    report = validate_video_segments(records)
    markdown = render_segment_validation_markdown(report)

    output_dir = args.output_dir.resolve()
    json_path = output_dir / "validation_report.json"
    md_path = output_dir / "validation_report.md"
    write_json(json_path, report)
    write_text(md_path, markdown)

    print(f"Validation records: {report.total_records}")
    print(f"Issues high/medium/low: {report.high_issue_count}/{report.medium_issue_count}/{report.low_issue_count}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")

    if args.fail_on_high and report.high_issue_count:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
