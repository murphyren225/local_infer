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
        return JSONResponse({"error": f"路由器不可达: {exc}"}, status_code=502)
    latency_ms = round((time.monotonic() - start) * 1000)
    try:
        data = r.json()
    except ValueError:
        return JSONResponse({"error": r.text[:300]}, status_code=502)
    if isinstance(data, dict):
        data["_console"] = {"latency_ms": latency_ms}
    return JSONResponse(data, status_code=r.status_code)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 2_000_000:
        return JSONResponse({"error": "文件太大(上限 2MB)"}, status_code=400)
    text = raw.decode("utf-8", errors="replace")
    if text.count("�") > max(20, len(text) * 0.2):
        return JSONResponse(
            {"error": "暂只支持文本类文件(txt/md/csv/json/代码);PDF/Word 在路线图上"},
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

    return {
        "mode": mode,
        "lanes": lanes,
        "stats": stats,
        "escalations": _parse_escalations(ROOT / "logs" / "switchyard.log")[-8:],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
