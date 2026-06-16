#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.digital_book_qa import answer_digital_book_payload
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


def build_llm_provider(args: argparse.Namespace) -> Any:
    config = OpenAICompatibleConfig.from_env()
    if args.llm_base_url:
        config.base_url = args.llm_base_url
    if args.llm_api_key:
        config.api_key = args.llm_api_key
    if args.llm_model:
        config.model = args.llm_model
    if not config.is_configured:
        raise SystemExit("LLM ask service needs ECNU_PLUS_API_KEY, ECNU_PLUS_BASE_URL and ECNU_PLUS_MODEL.")
    provider: Any = OpenAICompatibleProvider(config)
    if args.llm_max_retries:
        provider = RetryingLLMProvider(
            provider,
            max_retries=args.llm_max_retries,
            backoff_seconds=args.llm_retry_backoff,
        )
    if not args.no_llm_cache:
        provider = CachingLLMProvider(provider, args.llm_cache_path)
    return provider


def make_handler(llm_provider: Any) -> type[BaseHTTPRequestHandler]:
    class AskHandler(BaseHTTPRequestHandler):
        server_version = "Materials2TextbookAsk/1.0"

        def do_OPTIONS(self) -> None:
            self._send_json({"ok": True})

        def do_GET(self) -> None:
            if self.path.rstrip("/") in {"", "/health"}:
                self._send_json({"ok": True, "service": "materials2textbook.ask_book"})
                return
            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/ask":
                self._send_json({"error": "Not found"}, status=404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                response = answer_digital_book_payload(payload, llm_provider=llm_provider)
                self._send_json(response)
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                self._send_json({"error": str(exc)}, status=500)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return AskHandler


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Serve the LLM endpoint used by the generated digital book reader.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8120, help="Port to bind.")
    parser.add_argument("--llm-base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--llm-api-key", default=None, help="API key. Prefer ECNU_PLUS_API_KEY in environment.")
    parser.add_argument("--llm-model", default=None, help="Model name. Defaults to ECNU_PLUS_MODEL or ecnu-plus.")
    parser.add_argument("--llm-max-retries", type=int, default=2, help="Retry failed LLM calls this many times.")
    parser.add_argument("--llm-retry-backoff", type=float, default=1.0, help="Initial retry backoff in seconds.")
    parser.add_argument(
        "--llm-cache-path",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables" / "digital_book" / "ask_service_llm_cache.json",
        help="Path for persistent LLM response cache.",
    )
    parser.add_argument("--no-llm-cache", action="store_true", help="Disable persistent LLM response cache.")
    args = parser.parse_args()

    provider = build_llm_provider(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(provider))
    print(f"Ask-book service listening on http://{args.host}:{args.port}/ask")
    server.serve_forever()


if __name__ == "__main__":
    main()
