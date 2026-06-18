from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.exporters.digital_book import (
    smoke_test_student_package_static_assets,
    smoke_test_student_package_ask,
    validate_student_digital_book_package,
    write_student_digital_book_package,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package a student-facing digital book zip.")
    parser.add_argument("source_dir", type=Path, help="Directory containing index.html and digital_book.json.")
    parser.add_argument("--output", type=Path, default=Path("digital_book.zip"), help="Output zip path.")
    parser.add_argument(
        "--asset-fallback-zip",
        type=Path,
        default=None,
        help="Optional existing zip to reuse digital_book/assets when source_dir has no copied media.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Write the zip without running student-package validation.",
    )
    parser.add_argument(
        "--ask-smoke-question",
        default="",
        help="Optional local ask-book smoke-test question to run against the packaged book.",
    )
    parser.add_argument(
        "--ask-smoke-expected",
        action="append",
        default=[],
        help="Expected term for --ask-smoke-question. Can be passed multiple times.",
    )
    parser.add_argument(
        "--max-package-mb",
        type=float,
        default=2048.0,
        help="Maximum allowed student package size in MB. Use 0 to disable.",
    )
    parser.add_argument(
        "--max-asset-files",
        type=int,
        default=0,
        help="Maximum allowed number of packaged media asset files. Use 0 to disable.",
    )
    parser.add_argument(
        "--skip-static-smoke",
        action="store_true",
        help="Skip static HTML/JSON/media reference smoke test.",
    )
    args = parser.parse_args()

    final_output = args.output
    staged_output = _staged_output_path(final_output)
    staged_output.unlink(missing_ok=True)
    output = write_student_digital_book_package(
        source_dir=args.source_dir,
        output_zip=staged_output,
        asset_fallback_zip=args.asset_fallback_zip,
    )
    if not args.skip_validation:
        issues = validate_student_digital_book_package(
            output,
            max_package_bytes=int(args.max_package_mb * 1024 * 1024),
            max_asset_files=args.max_asset_files,
        )
        if args.ask_smoke_question:
            issues.extend(
                smoke_test_student_package_ask(
                    output,
                    question=args.ask_smoke_question,
                    expected_terms=args.ask_smoke_expected,
                )
            )
        if not args.skip_static_smoke:
            issues.extend(smoke_test_student_package_static_assets(output))
        if issues:
            print("Student digital book package validation failed:")
            for issue in issues:
                print(f"- {issue}")
            output.unlink(missing_ok=True)
            raise SystemExit(1)
    final_output.parent.mkdir(parents=True, exist_ok=True)
    output.replace(final_output)
    print(f"Student digital book package written: {final_output}")


def _staged_output_path(output: Path) -> Path:
    output = Path(output)
    return output.with_name(f".{output.name}.tmp")


if __name__ == "__main__":
    main()
