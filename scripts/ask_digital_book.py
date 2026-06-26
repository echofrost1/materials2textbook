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

from materials2textbook.digital_book_qa import answer_digital_book_question, load_digital_book, render_book_answer_markdown
from materials2textbook.io_utils import write_text
from materials2textbook.llm.cache import CachingLLMProvider
from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider
from materials2textbook.llm.retry import RetryingLLMProvider


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

    parser = argparse.ArgumentParser(description="Ask a generated digital_book.json with local retrieval or an optional LLM.")
    parser.add_argument("question", help="Question to ask the digital textbook.")
    parser.add_argument(
        "--book",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables" / "digital_book" / "digital_book.json",
        help="Path to digital_book.json.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional Markdown output path.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum retrieved source blocks.")
    parser.add_argument("--use-llm", action="store_true", help="Use OpenAI-compatible LLM after retrieval.")
    parser.add_argument("--llm-base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--llm-api-key", default=None, help="API key. Prefer OPENAI_API_KEY in environment.")
    parser.add_argument("--llm-model", default=None, help="Model name. Defaults to OPENAI_MODEL.")
    parser.add_argument("--llm-max-retries", type=int, default=2, help="Retry failed LLM calls this many times.")
    parser.add_argument("--llm-retry-backoff", type=float, default=1.0, help="Initial retry backoff in seconds.")
    parser.add_argument(
        "--llm-cache-path",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables" / "digital_book" / "ask_book_llm_cache.json",
        help="Path for persistent LLM response cache.",
    )
    parser.add_argument("--no-llm-cache", action="store_true", help="Disable persistent LLM response cache.")
    args = parser.parse_args()

    llm_provider = None
    if args.use_llm:
        config = OpenAICompatibleConfig.from_env()
        if args.llm_base_url:
            config.base_url = args.llm_base_url
        if args.llm_api_key:
            config.api_key = args.llm_api_key
        if args.llm_model:
            config.model = args.llm_model
        if not config.is_configured:
            raise SystemExit("LLM is enabled but not configured. Set OPENAI_* env vars or pass --llm-* options.")
        llm_provider = OpenAICompatibleProvider(config)
        if args.llm_max_retries:
            llm_provider = RetryingLLMProvider(
                llm_provider,
                max_retries=args.llm_max_retries,
                backoff_seconds=args.llm_retry_backoff,
            )
        if not args.no_llm_cache:
            llm_provider = CachingLLMProvider(llm_provider, args.llm_cache_path)

    book = load_digital_book(args.book)
    answer = answer_digital_book_question(book, args.question, llm_provider=llm_provider, limit=args.limit)
    markdown = render_book_answer_markdown(answer)
    if args.output:
        write_text(args.output, markdown)
    print(markdown)


if __name__ == "__main__":
    main()
