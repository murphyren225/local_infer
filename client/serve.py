"""Personal web front-end: a tiny local server (stdlib only).

Serves client/web/index.html and proxies /v1/* to the cluster gateway so the page
needs no CORS support from the gateway. One instance per person, on their machine.
Run: python3 -m client web --hub http://<hub>:4000
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
GATEWAY = os.environ.get("CLIENT_GATEWAY", "http://127.0.0.1:4000").rstrip("/")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._send(200, json.dumps({"gateway": GATEWAY}).encode())
        elif self.path.startswith("/v1/"):
            self._proxy("GET")
        else:
            self._send(404, b"{}")

    def do_POST(self):
        if self.path.startswith("/v1/"):
            self._proxy("POST")
        else:
            self._send(404, b"{}")

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(GATEWAY + self.path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
        except (urllib.error.URLError, OSError) as e:
            data, code = json.dumps({"error": f"gateway unreachable: {e}"}).encode(), 502
        if method == "POST" and code == 200:
            try:
                obj = json.loads(data)
                obj["_client"] = {"latency_ms": round((time.monotonic() - start) * 1000)}
                data = json.dumps(obj, ensure_ascii=False).encode()
            except ValueError:
                pass
        self._send(code, data)


def run(port: int = 7000) -> None:
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    run(int(os.environ.get("CLIENT_PORT", "7000")))
