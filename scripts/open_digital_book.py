"""Open a generated digital textbook in a local browser.

The reader uses relative media paths such as ``../../02_working_processing``.
For that reason this script serves the material root and opens the generated
``digital_book/index.html`` under it, instead of serving the book folder alone.
"""

from __future__ import annotations

import argparse
import functools
import socket
import sys
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from material_paths import default_raw_root, default_work_root


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATERIAL_ROOT = default_work_root()
DEFAULT_BOOK_RELATIVE = Path("05_final_deliverables") / "digital_book" / "index.html"
FALLBACK_BOOK_RELATIVES = (
    DEFAULT_BOOK_RELATIVE,
    Path("digital_book") / "index.html",
    Path("05_final_deliverables_book_mode_test") / "digital_book" / "index.html",
)


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the generated digital textbook preview.")
    parser.add_argument(
        "--material-root",
        type=Path,
        default=DEFAULT_MATERIAL_ROOT,
        help="Material root to serve. Defaults to local_runs/work_material1 unless configured.",
    )
    parser.add_argument(
        "--book-index",
        type=Path,
        default=None,
        help="Path to digital_book/index.html. Relative paths are resolved from --material-root.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8767, help="Preferred HTTP port. Default: 8767")
    parser.add_argument("--no-browser", action="store_true", help="Start server without opening a browser.")
    return parser.parse_args()


def find_available_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise SystemExit(f"No available port found from {preferred} to {preferred + 49}.")


def resolve_book_index(material_root: Path, book_index: Path | None) -> Path:
    material_root = material_root.resolve()
    if book_index:
        candidate = book_index if book_index.is_absolute() else material_root / book_index
        if candidate.exists():
            return candidate.resolve()
        raise SystemExit(f"Digital book index not found: {candidate}")

    for relative in FALLBACK_BOOK_RELATIVES:
        candidate = material_root / relative
        if candidate.exists():
            return candidate.resolve()

    searched = "\n".join(f"- {material_root / relative}" for relative in FALLBACK_BOOK_RELATIVES)
    raise SystemExit(f"No generated digital book found. Searched:\n{searched}")


def relative_url_path(root: Path, target: Path) -> str:
    try:
        relative = target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Book index must be inside material root:\nroot={root}\nindex={target}") from exc
    return "/" + relative.as_posix()


def main() -> int:
    args = parse_args()
    material_root = args.material_root.resolve()
    if not material_root.exists():
        raise SystemExit(f"Material root not found: {material_root}")

    index_path = resolve_book_index(material_root, args.book_index)
    port = find_available_port(args.host, args.port)
    url = f"http://{args.host}:{port}{relative_url_path(material_root, index_path)}?open={int(time.time())}"

    handler = functools.partial(NoCacheHandler, directory=str(material_root))
    server = ThreadingHTTPServer((args.host, port), handler)

    print(f"Serving material root: {material_root}")
    print(f"Opening digital book: {index_path}")
    print(f"URL: {url}")
    print("Press Ctrl+C to stop the preview server.")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
