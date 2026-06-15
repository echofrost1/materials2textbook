#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.io_utils import read_jsonl, write_json, write_text
from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider
from materials2textbook.validators.video_segments import (
    render_segment_validation_markdown,
    validate_video_segments,
)
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.orchestrator import TextbookWorkflow


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Validate upstream segments, then run the textbook workflow.")
    parser.add_argument(
        "--segments",
        type=Path,
        default=ROOT / "work_material1" / "02_working_processing" / "json" / "video_segments.jsonl",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables",
    )
    parser.add_argument("--title", default="钨极氩弧焊数字教材样章")
    parser.add_argument("--fail-on-high", action="store_true", help="Stop when validation finds high-risk issues.")
    parser.add_argument("--approved-only", action="store_true")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--min-teaching-value", type=float, default=0.0)
    parser.add_argument("--max-chunks-per-knowledge-point", type=int, default=None)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--llm-model", default=None)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    validation_dir = output_root / "validation"
    workflow_dir = output_root / "agent_workflow"

    records = read_jsonl(args.segments.resolve())
    validation_report = validate_video_segments(records)
    validation_markdown = render_segment_validation_markdown(validation_report)
    write_json(validation_dir / "validation_report.json", validation_report)
    write_text(validation_dir / "validation_report.md", validation_markdown)

    print("Validation:")
    print(f"- records: {validation_report.total_records}")
    print(
        "- issues high/medium/low: "
        f"{validation_report.high_issue_count}/{validation_report.medium_issue_count}/{validation_report.low_issue_count}"
    )
    print(f"- report: {validation_dir / 'validation_report.md'}")

    if args.fail_on_high and validation_report.high_issue_count:
        print("Stopped because --fail-on-high was set and high-risk issues were found.")
        raise SystemExit(2)

    llm_provider = None
    if args.use_llm:
        llm_config = OpenAICompatibleConfig.from_env()
        if args.llm_base_url:
            llm_config.base_url = args.llm_base_url
        if args.llm_api_key:
            llm_config.api_key = args.llm_api_key
        if args.llm_model:
            llm_config.model = args.llm_model
        if not llm_config.is_configured:
            raise SystemExit(
                "LLM is enabled but not configured. Set ECNU_PLUS_API_KEY, "
                "ECNU_PLUS_BASE_URL, ECNU_PLUS_MODEL or pass --llm-* options."
            )
        llm_provider = OpenAICompatibleProvider(llm_config)

    workflow = TextbookWorkflow(llm_provider=llm_provider, use_llm=args.use_llm)
    config = WorkflowConfig(
        include_pending=not args.approved_only,
        include_rejected=args.include_rejected,
        min_teaching_value=args.min_teaching_value,
        max_chunks_per_knowledge_point=args.max_chunks_per_knowledge_point,
    )
    outputs = workflow.run(args.segments.resolve(), workflow_dir, args.title, config)

    print("")
    print("Workflow:")
    print(f"- outline: {outputs.outline_markdown_path}")
    print(f"- evidence_index: {outputs.evidence_markdown_path}")
    print(f"- draft: {outputs.draft_path}")
    print(f"- final: {outputs.final_path}")
    print(f"- final_docx: {outputs.final_docx_path}")


if __name__ == "__main__":
    main()
