#!/usr/bin/env python
"""Cluster admin console + node registration endpoint.

Reads control-plane state from files (state/nodes.json, state/cluster_mode) and the
gateway over HTTP; never touches engine processes. Standalone script — runs with the
service venv, does not import the cluster package.
"""
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[3]
STATE, LOGS = ROOT / "state", ROOT / "logs"
ROUTER = os.environ.get("ROUTER_URL", "http://127.0.0.1:4000/v1")
PORT = int(os.environ.get("HOMED_CONSOLE_PORT", "6006"))

app = FastAPI(title="cluster console")
client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))
LANE_LAST: dict = {}


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _nodes() -> dict:
    try:
        return json.loads(_read(STATE / "nodes.json") or "{}").get("nodes", {})
    except ValueError:
        return {}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "console.html").read_text(encoding="utf-8")


@app.post("/api/chat")
async def chat(payload: dict):
    start = time.monotonic()
    try:
        r = await client.post(f"{ROUTER}/chat/completions", json=payload)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"router unreachable: {exc}"}, status_code=502)
    ms = round((time.monotonic() - start) * 1000)
    try:
        data = r.json()
    except ValueError:
        return JSONResponse({"error": r.text[:300]}, status_code=502)
    if isinstance(data, dict):
        data["_console"] = {"latency_ms": ms}
        model = data.get("model")
        tokens = (data.get("usage") or {}).get("completion_tokens") or 0
        if model and r.status_code == 200:
            LANE_LAST[model] = {"latency_ms": ms, "completion_tokens": tokens,
                                "tok_s": round(tokens / (ms / 1000), 1) if tokens and ms else None,
                                "at": time.strftime("%H:%M:%S")}
    return JSONResponse(data, status_code=r.status_code)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 2_000_000:
        return JSONResponse({"error": "File too large (2MB limit)"}, status_code=400)
    text = raw.decode("utf-8", errors="replace")
    if text.count("�") > max(20, len(text) * 0.2):
        return JSONResponse({"error": "Only text files are supported (txt/md/csv/json/code)"}, status_code=400)
    return {"name": file.filename, "chars": len(text), "truncated": len(text) > 12000, "text": text[:12000]}


@app.post("/api/register")
async def register(payload: dict):
    """Node join endpoint (control plane). Body: {token, name, pool, model, base_url, hw, engine}."""
    token = _read(STATE / "join_token")
    if not token or not secrets.compare_digest(token, str(payload.get("token", ""))):
        return JSONResponse({"error": "invalid token"}, status_code=403)
    required = ("name", "pool", "model", "base_url")
    if any(k not in payload for k in required):
        return JSONResponse({"error": f"missing fields, need {required}"}, status_code=400)
    nodes = _nodes()
    nodes[payload["name"]] = {"name": payload["name"], "pool": payload["pool"], "model": payload["model"],
                              "base_url": payload["base_url"], "hw": payload.get("hw", ""),
                              "engine": payload.get("engine", "vllm"), "local": False}
    STATE.mkdir(exist_ok=True)
    (STATE / "nodes.json").write_text(json.dumps({"nodes": nodes}, indent=2, ensure_ascii=False))
    return {"registered": payload["name"], "pool": payload["pool"]}


def _escalations() -> list:
    out = []
    p = LOGS / "switchyard.log"
    for line in _read(p).splitlines():
        if "escalating to strong tier" in line:
            out.append({"time": line[:19], "detail": line.split("escalating to strong tier", 1)[1].strip(" :")[:300]})
    return out[-8:]


@app.get("/api/status")
async def status():
    nodes = _nodes()
    lanes_ok, devices = {}, []
    for name, n in nodes.items():
        health = n["base_url"].rsplit("/v1", 1)[0] + "/health"
        try:
            ok = (await client.get(health, timeout=3)).status_code == 200
        except httpx.HTTPError:
            ok = False
        lanes_ok[n["pool"]] = lanes_ok.get(n["pool"], False) or ok
        devices.append({"name": f"{name} ({'local' if n.get('local') else 'remote'})",
                        "hw": n.get("hw") or n.get("engine", ""), "ok": ok,
                        "items": [{"label": f"{n['model']} · {n.get('engine','')} · {n['pool']} pool", "ok": ok}],
                        "last": LANE_LAST.get(n["model"])})
    stats = {}
    try:
        raw = (await client.get(f"{ROUTER}/routing/stats", timeout=5)).json()
        stats = {"requests": raw.get("total_requests"), "tokens": (raw.get("total_tokens") or {}).get("total"),
                 "errors": raw.get("total_errors")}
    except (httpx.HTTPError, ValueError):
        pass
    has_key = "TOGETHER_API_KEY" in _read(ROOT / ".env") or bool(os.environ.get("TOGETHER_API_KEY"))
    devices.append({"name": "Cloud fallback", "hw": "API", "ok": has_key,
                    "items": [{"label": "standby (activates when local pools die)", "ok": None}], "last": None})
    return {"mode": _read(STATE / "cluster_mode") or "unknown",
            "lanes": {"large": lanes_ok.get("strong", False), "small": lanes_ok.get("weak", False)},
            "devices": devices, "stats": stats, "escalations": _escalations()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
