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
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a student-facing digital_book.zip package.")
    parser.add_argument("package_zip", type=Path, help="Path to digital_book.zip.")
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

    issues = validate_student_digital_book_package(
        args.package_zip,
        max_package_bytes=int(args.max_package_mb * 1024 * 1024),
        max_asset_files=args.max_asset_files,
    )
    if args.ask_smoke_question:
        issues.extend(
            smoke_test_student_package_ask(
                args.package_zip,
                question=args.ask_smoke_question,
                expected_terms=args.ask_smoke_expected,
            )
        )
    if not args.skip_static_smoke:
        issues.extend(smoke_test_student_package_static_assets(args.package_zip))

    if issues:
        print("Student digital book package validation failed:")
        for issue in issues:
            print(f"- {issue}")
        raise SystemExit(1)
    print(f"Student digital book package validation passed: {args.package_zip}")


if __name__ == "__main__":
    main()
