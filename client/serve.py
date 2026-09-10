"""Personal side local service: a tiny HTTP server (stdlib only), bound to 127.0.0.1.

It is the personal side's single egress to the model side. Everything on this machine
(Pi, the web page, `client ask/batch`) goes through it, which is what lets it attach a
session identity to every request:

  GET  /                 the personal web page (client/web/index.html)
  GET  /api/config       {"gateway", "agent", "token"} — readable only same-origin
  *    /v1/*             proxied to the model side's gateway, with x-switchyard-session-id
                         (kept if the client sent one, else derived from the conversation)
  POST /api/agent        run one harness task on this machine via `pi -p`; needs the token
                         body: {"task": "...", "lane": "auto", "cwd": "~/work"}

Browser requests are accepted only from this service's own origin (Origin/Host check), so
a page on another site cannot drive /api/agent or spend cluster capacity through /v1.

Start with `client up` (background) or `client web` (foreground).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .gateway import SESSION_HEADER, pi_bin

WEB = Path(__file__).resolve().parent / "web"
HOME = Path.home() / ".cluster-client"
GATEWAY = os.environ.get("CLIENT_GATEWAY", "http://127.0.0.1:4000").rstrip("/")
AGENT_TIMEOUT = int(os.environ.get("CLIENT_AGENT_TIMEOUT", "900"))
TOKEN = secrets.token_urlsafe(24)
PORT = 7000


def derive_session(body: bytes | None) -> str | None:
    """A stable id for one conversation: hash of the first user message (plus the system
    prompt). Pi and other OpenAI clients resend the whole history each turn, so this stays
    constant across the turns of one session and differs between sessions."""
    if not body:
        return None
    try:
        msgs = json.loads(body).get("messages") or []
    except (ValueError, AttributeError):
        return None
    parts = []
    for role in ("system", "user"):
        for m in msgs:
            if m.get("role") == role:
                c = m.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                parts.append(str(c)[:2000])
                break
    if not parts:
        return None
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def origin_ok(headers, port: int) -> bool:
    """Reject browser requests from any origin but our own. Non-browser clients (Pi, curl)
    send no Origin and pass. Host must be loopback: defeats DNS rebinding."""
    host = (headers.get("Host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return False
    origin = headers.get("Origin")
    if origin is None:
        return True
    return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


def run_agent(task: str, lane: str = "auto", cwd: str | None = None) -> dict:
    pi = pi_bin()
    if not pi:
        return {"error": "pi not installed on this machine (bin/install-client.sh)"}
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

    def _guard(self) -> bool:
        if origin_ok(self.headers, PORT):
            return True
        self._json(403, {"error": "cross-origin request refused"})
        return False

    def do_GET(self):
        if not self._guard():
            return
        if self.path in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._json(200, {"gateway": GATEWAY, "agent": bool(pi_bin()), "token": TOKEN})
        elif self.path.startswith("/v1/"):
            self._proxy("GET")
        else:
            self._json(404, {})

    def do_POST(self):
        if not self._guard():
            return
        if self.path.startswith("/v1/"):
            self._proxy("POST")
        elif self.path == "/api/agent":
            if self.headers.get("X-Client-Token") != TOKEN:
                return self._json(403, {"error": "missing or wrong client token"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(415, {"error": "application/json required"})
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
        headers = {"Content-Type": "application/json"}
        session = self.headers.get(SESSION_HEADER) or (derive_session(body) if method == "POST" else None)
        if session:
            headers[SESSION_HEADER] = session
        req = urllib.request.Request(GATEWAY + self.path, data=body, method=method, headers=headers)
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
                obj["_client"] = {"latency_ms": round((time.monotonic() - start) * 1000), "session": session}
                data = json.dumps(obj, ensure_ascii=False).encode()
            except ValueError:
                pass  # streaming or non-JSON body: pass through untouched
        self._send(code, data)


def run(port: int = 7000) -> None:
    global PORT
    PORT = port
    HOME.mkdir(parents=True, exist_ok=True)
    tok = HOME / "token"
    tok.write_text(TOKEN)
    tok.chmod(0o600)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    run(int(os.environ.get("CLIENT_PORT", "7000")))
