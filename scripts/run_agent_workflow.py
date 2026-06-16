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

from materials2textbook.llm.cache import CachingLLMProvider
from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider
from materials2textbook.llm.retry import RetryingLLMProvider
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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Run the MVP multi-agent textbook workflow.")
    parser.add_argument(
        "--segments",
        type=Path,
        default=ROOT / "work_material1" / "02_working_processing" / "json" / "video_segments.jsonl",
        help="Input video_segments.jsonl from the material-processing pipeline.",
    )
    parser.add_argument(
        "--document-segments",
        type=Path,
        default=None,
        help="Optional document_segments.jsonl for PDF/PPT/Markdown/Text evidence chunks.",
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
        "--include-rejected",
        action="store_true",
        help="Include rejected segments. Default excludes rejected segments even in draft mode.",
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
    parser.add_argument(
        "--review-rounds",
        type=int,
        default=1,
        help="Run review/revision for N rounds. Defaults to one review round.",
    )
    parser.add_argument("--use-llm", action="store_true", help="Use an OpenAI-compatible LLM for supported agents.")
    parser.add_argument("--llm-base-url", default=None, help="OpenAI-compatible base URL, e.g. https://.../v1")
    parser.add_argument("--llm-api-key", default=None, help="API key. Prefer ECNU_PLUS_API_KEY in environment.")
    parser.add_argument("--llm-model", default=None, help="Model name. Defaults to ECNU_PLUS_MODEL or ecnu-plus.")
    parser.add_argument("--llm-max-retries", type=int, default=2, help="Retry failed LLM calls this many times.")
    parser.add_argument("--llm-retry-backoff", type=float, default=1.0, help="Initial retry backoff in seconds.")
    parser.add_argument(
        "--llm-cache-path",
        type=Path,
        default=None,
        help="Path for persistent LLM response cache. Defaults to OUTPUT_DIR/llm_cache.json.",
    )
    parser.add_argument("--no-llm-cache", action="store_true", help="Disable persistent LLM response cache.")
    args = parser.parse_args()

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
        if args.llm_max_retries:
            llm_provider = RetryingLLMProvider(
                llm_provider,
                max_retries=args.llm_max_retries,
                backoff_seconds=args.llm_retry_backoff,
            )
        if not args.no_llm_cache:
            cache_path = args.llm_cache_path or (args.output_dir.resolve() / "llm_cache.json")
            llm_provider = CachingLLMProvider(llm_provider, cache_path)

    workflow = TextbookWorkflow(llm_provider=llm_provider, use_llm=args.use_llm)
    config = WorkflowConfig(
        include_pending=not args.approved_only,
        include_rejected=args.include_rejected,
        min_teaching_value=args.min_teaching_value,
        max_chunks_per_knowledge_point=args.max_chunks_per_knowledge_point,
        review_rounds=args.review_rounds,
    )
    outputs = workflow.run(
        args.segments.resolve(),
        args.output_dir.resolve(),
        args.title,
        config,
        document_segments_path=args.document_segments.resolve() if args.document_segments else None,
    )
    print("Workflow outputs:")
    print(f"- outline: {outputs.outline_path}")
    print(f"- outline_markdown: {outputs.outline_markdown_path}")
    print(f"- evidence_chunks: {outputs.evidence_chunks_path}")
    print(f"- evidence_index: {outputs.evidence_markdown_path}")
    print(f"- chapter_plan: {outputs.chapter_plan_path}")
    print(f"- draft: {outputs.draft_path}")
    print(f"- draft_docx: {outputs.draft_docx_path}")
    print(f"- review_report: {outputs.review_report_path}")
    print(f"- review_markdown: {outputs.review_markdown_path}")
    print(f"- review_history: {outputs.review_history_path}")
    print(f"- revision_diff: {outputs.revision_diff_path}")
    print(f"- summary: {outputs.summary_path}")
    print(f"- final: {outputs.final_path}")
    print(f"- final_docx: {outputs.final_docx_path}")
    print(f"- digital_book: {outputs.digital_book_path}")
    print(f"- digital_book_index: {outputs.digital_book_index_path}")
    print(f"- digital_book_review: {outputs.digital_book_review_markdown_path}")
    print(f"- manifest: {outputs.manifest_path}")


if __name__ == "__main__":
    main()
