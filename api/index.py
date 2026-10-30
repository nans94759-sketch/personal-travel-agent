"""
Vercel Python Serverless Bridge for aiohttp Application (`server.py`)
---------------------------------------------------------------------
Starts the aiohttp application (`create_app()`) on an internal loopback socket
once per warm container and forwards all Vercel HTTP requests (`GET`, `POST`,
`PUT`, `DELETE`) preserving headers, cookies, and status codes.
"""

import asyncio
from http.server import BaseHTTPRequestHandler
import os
import socket
import sys
import threading
import time
import urllib.error
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


class handler(BaseHTTPRequestHandler):
    def _proxy(self) -> None:
        port = _ensure_loopback_server()
        target_url = f"http://127.0.0.1:{port}{self.path}"

        content_len = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_len) if content_len > 0 else None

        req_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "transfer-encoding"):
                req_headers[k] = v

        req = urllib.request.Request(
            target_url, data=body, headers=req_headers, method=self.command
        )
        try:
            with urllib.request.urlopen(req, timeout=55) as resp:
                status = resp.status
                resp_headers = resp.headers
                resp_body = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = e.headers
            resp_body = e.read()
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(f'{{"ok":false,"error":"{str(e)}"}}'.encode("utf-8"))
            return

        self.send_response(status)
        for hk, hv in resp_headers.items():
            if hk.lower() not in ("transfer-encoding", "connection", "content-encoding"):
                self.send_header(hk, hv)
        self.end_headers()
        if resp_body:
            self.wfile.write(resp_body)

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()
