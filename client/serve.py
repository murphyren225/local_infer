"""Personal side local service: a tiny HTTP server (stdlib only), bound to 127.0.0.1.

  GET  /                 the personal web page (client/web/index.html)
  GET  /api/config       which hub this service talks to
  *    /v1/*             proxied to the model side's gateway (so the page needs no CORS)
  POST /api/agent        run one harness task on this machine via `pi -p` and return its output
                         body: {"task": "...", "lane": "auto", "cwd": "~/work"}

Start with `client up` (background) or `client web` (foreground).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .gateway import pi_bin

WEB = Path(__file__).resolve().parent / "web"
GATEWAY = os.environ.get("CLIENT_GATEWAY", "http://127.0.0.1:4000").rstrip("/")
AGENT_TIMEOUT = int(os.environ.get("CLIENT_AGENT_TIMEOUT", "900"))


def run_agent(task: str, lane: str = "auto", cwd: str | None = None) -> dict:
    pi = pi_bin()
    if not pi:
        return {"error": "pi not installed on this machine (npm install -g --ignore-scripts @earendil-works/pi-coding-agent)"}
    workdir = Path(os.path.expanduser(cwd or "~")).resolve()
    if not workdir.is_dir():
        return {"error": f"cwd not a directory: {workdir}"}
    t0 = time.monotonic()
    try:
        r = subprocess.run([pi, "--provider", "home", "--model", lane, "-p", task],
                           cwd=str(workdir), capture_output=True, text=True, timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"error": f"agent timed out after {AGENT_TIMEOUT}s"}
    out = r.stdout.strip() or r.stderr.strip()[-2000:]
    out = out.removeprefix("</think>").strip()  # llama.cpp leaks the closing tag after a tool round
    return {"output": out, "exit": r.returncode,
            "cwd": str(workdir), "latency_ms": round((time.monotonic() - t0) * 1000)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _body(self) -> bytes | None:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._json(200, {"gateway": GATEWAY, "agent": bool(pi_bin())})
        elif self.path.startswith("/v1/"):
            self._proxy("GET")
        else:
            self._json(404, {})

    def do_POST(self):
        if self.path.startswith("/v1/"):
            self._proxy("POST")
        elif self.path == "/api/agent":
            try:
                req = json.loads(self._body() or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if not req.get("task"):
                return self._json(400, {"error": "task required"})
            self._json(200, run_agent(req["task"], req.get("lane", "auto"), req.get("cwd")))
        else:
            self._json(404, {})

    def _proxy(self, method: str) -> None:
        body = self._body()
        req = urllib.request.Request(GATEWAY + self.path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data, code = r.read(), r.status
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
