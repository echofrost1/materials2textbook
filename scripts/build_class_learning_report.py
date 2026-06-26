from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.learning_analytics import (
    build_class_learning_report,
    load_study_data_records,
    render_class_learning_report_html,
    render_class_learning_report_markdown,
)
from material_paths import default_work_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a class learning report from exported study data JSON files.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing study data JSON files exported by the digital book reader.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_work_root() / "05_final_deliverables" / "class_learning_report",
        help="Directory for class_learning_report.json and class_learning_report.md.",
    )
    parser.add_argument(
        "--pattern",
        default="*.json",
        help="Glob pattern for study data files under --input-dir.",
    )
    args = parser.parse_args()

    paths = sorted(path for path in args.input_dir.glob(args.pattern) if path.is_file())
    records, invalid_sources = load_study_data_records(paths)
    report = build_class_learning_report(records, invalid_sources)
    markdown = render_class_learning_report_markdown(report)
    html = render_class_learning_report_html(report)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "class_learning_report.json"
    md_path = args.output_dir / "class_learning_report.md"
    html_path = args.output_dir / "class_learning_report.html"
    write_json(json_path, report)
    write_text(md_path, markdown)
    write_text(html_path, html)

    print(f"Study data files: {len(paths)}")
    print(f"Valid/invalid: {report.valid_records}/{report.invalid_records}")
    print(f"Average progress: {report.average_progress:.1f}%")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    print(f"HTML report: {html_path}")


if __name__ == "__main__":
    main()
