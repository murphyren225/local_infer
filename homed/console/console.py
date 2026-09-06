#!/usr/bin/env python
"""homed 控制台 — 企业用户的 Web Surface。

聊天 + 文件上传 + 路由决策可视化(车道/延迟/升级事件/降级状态)。
跑在 :6006(AutoDL「自定义服务」直接暴露这个端口)。
"""
import os
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[2]
ROUTER = os.environ.get("ROUTER_URL", "http://127.0.0.1:4000/v1")
PORT = int(os.environ.get("CONSOLE_PORT", "6006"))

# 每个模型最近一次解码指标(仅统计经控制台发出的请求)
LANE_LAST: dict[str, dict] = {}


def _read_file(name: str) -> str:
    p = ROOT / "logs" / name
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _lane_names() -> dict:
    names = {"LARGE_NAME": "large", "SMALL_NAME": "small"}
    out = {}
    for line in _read_file("lanes.env").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k in names:
                out[names[k]] = v
    return out

app = FastAPI(title="homed console")
client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "homed" / "console" / "console.html").read_text(encoding="utf-8")


@app.post("/api/chat")
async def chat(payload: dict):
    """转发到 Switchyard,附带服务端测得的端到端延迟。"""
    start = time.monotonic()
    try:
        r = await client.post(f"{ROUTER}/chat/completions", json=payload)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"router unreachable: {exc}"}, status_code=502)
    latency_ms = round((time.monotonic() - start) * 1000)
    try:
        data = r.json()
    except ValueError:
        return JSONResponse({"error": r.text[:300]}, status_code=502)
    if isinstance(data, dict):
        data["_console"] = {"latency_ms": latency_ms}
        model = data.get("model")
        tokens = (data.get("usage") or {}).get("completion_tokens") or 0
        if model and r.status_code == 200:
            LANE_LAST[model] = {
                "latency_ms": latency_ms,
                "completion_tokens": tokens,
                "tok_s": round(tokens / (latency_ms / 1000), 1) if tokens and latency_ms else None,
                "at": time.strftime("%H:%M:%S"),
            }
    return JSONResponse(data, status_code=r.status_code)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 2_000_000:
        return JSONResponse({"error": "File too large (2MB limit)"}, status_code=400)
    text = raw.decode("utf-8", errors="replace")
    if text.count("�") > max(20, len(text) * 0.2):
        return JSONResponse(
            {"error": "Only text files are supported for now (txt/md/csv/json/code); PDF/Word are on the roadmap"},
            status_code=400,
        )
    return {
        "name": file.filename,
        "chars": len(text),
        "truncated": len(text) > 12000,
        "text": text[:12000],
    }


def _parse_escalations(log_path: Path) -> list[dict]:
    events = []
    if not log_path.exists():
        return events
    for line in log_path.read_text(errors="replace").splitlines():
        if "escalating to strong tier" not in line:
            continue
        detail = line.split("escalating to strong tier", 1)[1].strip(" :")
        events.append({"time": line[:19], "detail": detail[:300]})
    return events


@app.get("/api/status")
async def status():
    lanes = {}
    for name, port in (("large", 8001), ("small", 8002)):
        try:
            r = await client.get(f"http://127.0.0.1:{port}/health", timeout=3)
            lanes[name] = r.status_code == 200
        except httpx.HTTPError:
            lanes[name] = False

    mode_file = ROOT / "logs" / "cluster_mode"
    mode = mode_file.read_text().strip() if mode_file.exists() else "unknown"

    stats: dict = {}
    try:
        r = await client.get(f"{ROUTER}/routing/stats", timeout=5)
        raw = r.json()
        stats = {
            "requests": raw.get("total_requests"),
            "tokens": (raw.get("total_tokens") or {}).get("total"),
            "errors": raw.get("total_errors"),
        }
    except (httpx.HTTPError, ValueError):
        pass

    # ---- 设备面板: 谁在线、跑什么、最近解码指标 ----
    names = _lane_names()
    small_name = names.get("small", "small")
    large_name = names.get("large", "large")
    weak_local = _read_file("weak.src") != "remote"
    tunnel = _read_file("tunnel.target")

    hub_items = [
        {"label": "Router · Switchyard :4000", "ok": bool(stats)},
        {"label": "Console :6006", "ok": True},
    ]
    if weak_local:
        hub_items.append(
            {"label": f"{small_name} · llama.cpp (CPU)", "ok": lanes["small"]}
        )
    gpu_items = [{"label": f"{large_name} · vLLM", "ok": lanes["large"]}]
    if not weak_local:
        gpu_items.append({"label": f"{small_name} · vLLM", "ok": lanes["small"]})

    devices = [
        {
            "name": "Hub — this Mac",
            "hw": _read_file("local_hw.info") or "local machine",
            "ok": True,
            "items": hub_items,
            "last": LANE_LAST.get(small_name) if weak_local else None,
        },
        {
            "name": "GPU node — via SSH tunnel",
            "hw": _read_file("remote_gpu.info") or (tunnel or "not linked"),
            "ok": lanes["large"],
            "items": gpu_items,
            "last": LANE_LAST.get(large_name),
        },
        {
            "name": "Cloud fallback",
            "hw": "API",
            "ok": bool(os.environ.get("TOGETHER_API_KEY")) or "TOGETHER_API_KEY" in _read_file("../.env"),
            "items": [{"label": "standby (activates when local lanes die)", "ok": None}],
            "last": None,
        },
    ]

    return {
        "mode": mode,
        "lanes": lanes,
        "stats": stats,
        "devices": devices,
        "escalations": _parse_escalations(ROOT / "logs" / "switchyard.log")[-8:],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
