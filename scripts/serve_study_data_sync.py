#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.learning_analytics import parse_study_data


def make_handler(output_dir: Path) -> type[BaseHTTPRequestHandler]:
    output_dir.mkdir(parents=True, exist_ok=True)

    class StudyDataHandler(BaseHTTPRequestHandler):
        server_version = "Materials2TextbookStudySync/1.0"

        def do_OPTIONS(self) -> None:
            self._send_json({"ok": True})

        def do_GET(self) -> None:
            if self.path.rstrip("/") in {"", "/health"}:
                self._send_json({"ok": True, "service": "materials2textbook.study_data"})
                return
            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/study-data":
                self._send_json({"error": "Not found"}, status=404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                record = parse_study_data(payload)
                filename = _record_filename(record.book_id or "digital-book")
                path = output_dir / filename
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._send_json({"ok": True, "path": str(path), "book_id": record.book_id})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)

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

    return StudyDataHandler


def _record_filename(book_id: str) -> str:
    safe_book_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in book_id).strip("-") or "digital-book"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_book_id}-{stamp}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect study data posted by generated digital book readers.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8121, help="Port to bind.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "work_material1" / "05_final_deliverables" / "study_data_submissions",
        help="Directory for submitted study data JSON files.",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.output_dir))
    print(f"Study-data sync service listening on http://{args.host}:{args.port}/study-data")
    server.serve_forever()


if __name__ == "__main__":
    main()
