#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.workflow.orchestrator import TextbookWorkflow
from materials2textbook.workflow.config import WorkflowConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MVP multi-agent textbook workflow.")
    parser.add_argument(
        "--segments",
        type=Path,
        default=ROOT / "work_material1" / "02_working_processing" / "json" / "video_segments.jsonl",
        help="Input video_segments.jsonl from the material-processing pipeline.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables" / "agent_workflow",
        help="Directory for evidence chunks, chapter plan, draft, reviews, and final markdown.",
    )
    parser.add_argument("--title", default="钨极氩弧焊数字教材样章")
    parser.add_argument(
        "--approved-only",
        action="store_true",
        help="Only include segments whose review_status contains approved.",
    )
    parser.add_argument(
        "--min-teaching-value",
        type=float,
        default=0.0,
        help="Filter out chunks below this usefulness/teaching-value score.",
    )
    parser.add_argument(
        "--max-chunks-per-knowledge-point",
        type=int,
        default=None,
        help="Limit evidence shown for each knowledge point in the chapter plan.",
    )
    args = parser.parse_args()

    workflow = TextbookWorkflow()
    config = WorkflowConfig(
        include_pending=not args.approved_only,
        min_teaching_value=args.min_teaching_value,
        max_chunks_per_knowledge_point=args.max_chunks_per_knowledge_point,
    )
    outputs = workflow.run(args.segments.resolve(), args.output_dir.resolve(), args.title, config)
    print("Workflow outputs:")
    print(f"- evidence_chunks: {outputs.evidence_chunks_path}")
    print(f"- chapter_plan: {outputs.chapter_plan_path}")
    print(f"- draft: {outputs.draft_path}")
    print(f"- review_report: {outputs.review_report_path}")
    print(f"- review_markdown: {outputs.review_markdown_path}")
    print(f"- summary: {outputs.summary_path}")
    print(f"- final: {outputs.final_path}")


if __name__ == "__main__":
    main()
