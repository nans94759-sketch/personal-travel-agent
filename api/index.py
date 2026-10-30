"""
Vercel Python Serverless Bridge for aiohttp Application (`server.py`)
---------------------------------------------------------------------
Starts the aiohttp application (`create_app()`) on an internal loopback socket
once per warm container and forwards all Vercel HTTP requests (`GET`, `HEAD`,
`POST`, `PUT`, `DELETE`) preserving original URL paths, query parameters,
headers, cookies, and status codes.
"""

import asyncio
from http.server import BaseHTTPRequestHandler
import os
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRAVEL_DATA_MODE", "commercial")

from aiohttp import web  # noqa: E402
from server import create_app  # noqa: E402

_LOOPBACK_PORT = 0
_SERVER_LOCK = threading.Lock()


def _ensure_loopback_server() -> int:
    global _LOOPBACK_PORT
    if _LOOPBACK_PORT > 0:
        return _LOOPBACK_PORT

    with _SERVER_LOCK:
        if _LOOPBACK_PORT > 0:
            return _LOOPBACK_PORT

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        ready_event = threading.Event()

        def _run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            app = create_app()
            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, "127.0.0.1", port)
            loop.run_until_complete(site.start())
            ready_event.set()
            loop.run_forever()

        t = threading.Thread(target=_run_loop, daemon=True)
        t.start()
        ready_event.wait(timeout=5.0)
        _LOOPBACK_PORT = port
        return _LOOPBACK_PORT


def _resolve_original_path(raw_path: str, headers: object) -> str:
    """Reconstruct the original request path & query string before Vercel rewrite."""
    parsed = urllib.parse.urlsplit(raw_path)
    qs_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    orig_path = None
    remaining_pairs = []
    for k, v in qs_pairs:
        if k == "__path" and orig_path is None:
            orig_path = v
        else:
            remaining_pairs.append((k, v))

    if not orig_path:
        # Fallback to x-matched-path or raw path if not rewritten via __path
        matched = getattr(headers, "get", lambda _k: "")("x-matched-path") or ""
        if matched and matched != "/api/index.py":
            orig_path = matched
        elif parsed.path == "/api/index.py":
            orig_path = "/"
        else:
            orig_path = parsed.path

    if not orig_path.startswith("/"):
        orig_path = "/" + orig_path

    new_qs = urllib.parse.urlencode(remaining_pairs)
    return f"{orig_path}?{new_qs}" if new_qs else orig_path


class handler(BaseHTTPRequestHandler):
    def _proxy(self, is_head: bool = False) -> None:
        port = _ensure_loopback_server()
        real_path = _resolve_original_path(self.path, self.headers)
        target_url = f"http://127.0.0.1:{port}{real_path}"

        content_len = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_len) if content_len > 0 else None

        req_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "transfer-encoding"):
                req_headers[k] = v

        method = "GET" if is_head else self.command
        req = urllib.request.Request(
            target_url, data=body, headers=req_headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=55) as resp:
                status = resp.status
                resp_headers = resp.headers
                resp_body = b"" if is_head else resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = e.headers
            resp_body = b"" if is_head else e.read()
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            if not is_head:
                self.wfile.write(f'{{"ok":false,"error":"{str(e)}"}}'.encode("utf-8"))
            return

        self.send_response(status)
        for hk, hv in resp_headers.items():
            if hk.lower() not in ("transfer-encoding", "connection", "content-encoding"):
                self.send_header(hk, hv)
        self.end_headers()
        if resp_body and not is_head:
            self.wfile.write(resp_body)

    def do_GET(self) -> None:
        self._proxy(is_head=False)

    def do_HEAD(self) -> None:
        self._proxy(is_head=True)

    def do_POST(self) -> None:
        self._proxy(is_head=False)

    def do_PUT(self) -> None:
        self._proxy(is_head=False)

    def do_DELETE(self) -> None:
        self._proxy(is_head=False)
