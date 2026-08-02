#!/usr/bin/env python3
"""Local dashboard HTTP server shared by web, webview, and electron hosts."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agent_usage.scanner import build_report

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

_STATIC_CANDIDATES = [
    REPO_ROOT / "web" / "static",
    PACKAGE_DIR / "static",
    Path.cwd() / "web" / "static",
]
DEFAULT_STATIC = next((p for p in _STATIC_CANDIDATES if p.is_dir()), _STATIC_CANDIDATES[0])

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7840

_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()
CACHE_TTL = 15.0


def get_report(force: bool = False) -> dict:
    now = time.time()
    with _lock:
        if not force and _cache["data"] is not None and now - _cache["at"] < CACHE_TTL:
            return _cache["data"]
    data = build_report()
    with _lock:
        _cache["at"] = time.time()
        _cache["data"] = data
    return data


def _make_handler(static_dir: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_dir), **kwargs)

        def log_message(self, fmt: str, *args) -> None:
            if args and str(args[0]).startswith("GET /api"):
                super().log_message(fmt, *args)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/report":
                qs = parse_qs(parsed.query)
                force = qs.get("refresh", ["0"])[0] in ("1", "true", "yes")
                try:
                    data = get_report(force=force)
                    body = json.dumps(data).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    body = json.dumps({"error": str(e)}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return

            if parsed.path in ("", "/"):
                self.path = "/index.html"
            return super().do_GET()

    return Handler


def find_free_port(host: str = DEFAULT_HOST, start: int = DEFAULT_PORT, span: int = 20) -> int:
    for port in range(start, start + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return start


class DashboardServer:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        static_dir: Path | None = None,
    ) -> None:
        self.host = host
        self.port = port or find_free_port(host)
        self.static_dir = Path(static_dir or DEFAULT_STATIC)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, background: bool = True, warm: bool = True) -> str:
        if not self.static_dir.is_dir():
            raise FileNotFoundError(f"Static UI not found: {self.static_dir}")
        handler = _make_handler(self.static_dir)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        if warm:
            threading.Thread(target=lambda: get_report(force=True), daemon=True).start()

        def serve() -> None:
            assert self._httpd is not None
            self._httpd.serve_forever()

        if background:
            self._thread = threading.Thread(target=serve, daemon=True)
            self._thread.start()
        else:
            serve()
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def run_browser(
    host: str = DEFAULT_HOST,
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    # Allow electron/webview hosts to pin the root for frozen layouts
    root_env = os.environ.get("AGENT_READOUT_ROOT")
    static = None
    if root_env:
        candidate = Path(root_env) / "web" / "static"
        if candidate.is_dir():
            static = candidate

    server = DashboardServer(host=host, port=port, static_dir=static)
    url = server.start(background=True)
    print(f"Agent Usage → {url}")
    print("Scanning ~/.claude, ~/.codex, ~/.pi …")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nbye")
        server.stop()


def main() -> None:
    run_browser()


if __name__ == "__main__":
    main()
