#!/usr/bin/env python
"""Cluster admin console + node registration endpoint.

Reads control-plane state from files (state/nodes.json, state/cluster_mode) and the
gateway over HTTP; never touches engine processes. Standalone script — runs with the
service venv, does not import the cluster package.
"""
import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
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
# PAIR (our fork) on this Mac: desktop app data dir + the ports the link tests use (docs/testing.md).
PAIR_HOME = Path(os.environ.get("PAIR_HOME", Path.home() / "Library/Application Support/Nvidia Corporation/Personal AI Router"))
PAIR_LM_PROXY = os.environ.get("PAIR_LM_PROXY", "http://127.0.0.1:11234")
PAIR_LOCAL_ENGINE = os.environ.get("PAIR_LOCAL_ENGINE", "http://127.0.0.1:11235")
PAIR_REMOTE_ENGINE_PORT = 1234      # manual nodes: LM-slot engine probed at addr:1234 (tunnelled SGLang)
PAIR_REMOTE_NODEINFO_PORT = 14318
LINKS_RUNNER = ROOT / "tests" / "links.py"
E2E_RUNNER = ROOT / "tests" / "e2e_chain.py"

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


# ---------------------------------------------------------------- links dashboard (tests/links.py as the engine)

@app.get("/links", response_class=HTMLResponse)
def links_page() -> str:
    return (Path(__file__).parent / "links.html").read_text(encoding="utf-8")


_CHECK = re.compile(r"^\s*([✓✗])\s+(.*)$")


def _run_checks(cmd: list[str], env: dict | None = None, timeout: int = 900) -> dict:
    """Run a checker script; its `✓ / ✗` lines become checks, its RESULT line the verdict."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), env={**os.environ, **(env or {})},
                           capture_output=True, text=True, timeout=timeout)
        out = p.stdout + p.stderr
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "timeout", 1
    checks = [{"ok": m.group(1) == "✓", "msg": m.group(2)} for m in (_CHECK.match(l) for l in out.splitlines()) if m]
    passed = rc == 0 and "FAIL" not in out.split("RESULT")[-1]
    if not checks:
        checks = [{"ok": passed, "msg": out.strip()[-600:] or f"exit {rc}"}]
    return {"pass": passed, "checks": checks, "seconds": round(time.monotonic() - t0, 1), "output": out[-4000:]}


@app.get("/api/links/run/{n}")
async def links_run(n: int):
    if n not in range(1, 8):
        return JSONResponse({"error": "link must be 1..7"}, status_code=400)
    return await asyncio.to_thread(_run_checks, [sys.executable, str(LINKS_RUNNER), str(n)])


@app.get("/api/links/e2e")
async def links_e2e():
    env = {"E2E_HUB": ROUTER.rsplit("/v1", 1)[0], "E2E_PAIR_RPC": ""}
    d = await asyncio.to_thread(_run_checks, [sys.executable, str(E2E_RUNNER)], env)
    # e2e_chain.py prints evidence lines rather than ✓/✗; surface them as checks
    lines = [l.strip() for l in d["output"].splitlines() if l.startswith("  ") and "next:" not in l and "RL traces" not in l]
    d["checks"] = [{"ok": d["pass"], "msg": l} for l in lines] or d["checks"]
    return d


# ---------------------------------------------------------------- PAIR nodes / jobs / add-node

def _pair_json(name: str, default):
    try:
        return json.loads((PAIR_HOME / name).read_text())
    except (OSError, ValueError):
        return default


async def _models(base: str) -> list[str]:
    try:
        r = await client.get(base + "/v1/models", timeout=4)
        return [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []


async def _up(url: str) -> bool:
    try:
        return (await client.get(url, timeout=3)).status_code in (200, 404)
    except httpx.HTTPError:
        return False


@app.get("/api/pair")
async def pair_status():
    self_id = _pair_json("node-id.json", {}).get("node_uuid", "")
    manual = _pair_json("configs/manual-nodes.json", [])
    engine_state = (_pair_json("engine-bin/engine-state.json", {}) or {}).get("engines", {})
    ws = _pair_json("workloads-history.json", [])
    ws = ws.get("workloads", ws) if isinstance(ws, dict) else ws
    names = {self_id: "this-mac"}
    # a manual node's ledger id is its hostUuid; learn it from the desktop log is fragile, so label by exclusion
    counts: dict[str, int] = {}
    for w in ws:
        counts[w.get("scheduledOn", "")] = counts.get(w.get("scheduledOn", ""), 0) + 1
    remote_ids = [k for k in counts if k and k != self_id]
    nodes = [{"name": "this-mac", "kind": "self", "address": "127.0.0.1", "engine": "llama-server (LM slot)",
              "online": bool(await _models(PAIR_LOCAL_ENGINE)), "models": await _models(PAIR_LOCAL_ENGINE),
              "jobs": counts.get(self_id, 0),
              "desired": {k: v for k, v in engine_state.items()}}]
    for i, m in enumerate(manual):
        addr = m.get("address", "")
        eng = f"http://{addr}:{PAIR_REMOTE_ENGINE_PORT}"
        models = await _models(eng)
        rid = remote_ids[i] if i < len(remote_ids) else ""
        if rid:
            names[rid] = m.get("name", addr)
        nodes.append({"name": m.get("name", addr), "kind": "manual", "address": addr, "engine": "SGLang (LM slot, tunnelled)",
                      "online": bool(models) and await _up(f"http://{addr}:{PAIR_REMOTE_NODEINFO_PORT}/"),
                      "models": models, "jobs": counts.get(rid, 0) if rid else 0})
    jobs = [{"time": time.strftime("%H:%M:%S", time.localtime((w.get("createdAt") or 0) / 1000)),
             "model": w.get("model"), "node": names.get(w.get("scheduledOn", ""), "remote"), "state": w.get("state")}
            for w in ws[-12:]][::-1]
    tunnel_ok = bool(await _models(f"http://127.0.0.1:{PAIR_REMOTE_ENGINE_PORT}"))
    return {"self_id": self_id, "tunnel_ok": tunnel_ok, "lm_proxy_models": await _models(PAIR_LM_PROXY),
            "nodes": nodes, "jobs": jobs}


def _restart_pair_desktop() -> None:
    subprocess.Popen([str(ROOT / "bin" / "pair-desktop"), "restart"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


@app.post("/api/pair/nodes")
async def pair_add_node(payload: dict):
    """Add a remote PAIR node: persist it for the desktop app (manual-nodes.json), open the tunnel
    when an SSH target is given, then restart the desktop app so it replays node/add."""
    name = str(payload.get("name", "")).strip()
    addr = str(payload.get("address", "")).strip() or "127.0.0.1"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name):
        return JSONResponse({"error": "name: letters, digits, . _ - only"}, status_code=400)
    if not re.fullmatch(r"[A-Za-z0-9.:-]{1,80}", addr):
        return JSONResponse({"error": "address: host or IP only"}, status_code=400)
    target = str(payload.get("ssh_target", "")).strip()
    port = str(payload.get("ssh_port", "")).strip() or "22"
    if target and not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+", target):
        return JSONResponse({"error": "ssh_target must look like user@host"}, status_code=400)
    if not port.isdigit():
        return JSONResponse({"error": "ssh_port must be a number"}, status_code=400)
    entries = [e for e in _pair_json("configs/manual-nodes.json", []) if e.get("name") != name and e.get("id") != name]
    entries.append({"id": name, "address": addr, "name": name})
    (PAIR_HOME / "configs").mkdir(parents=True, exist_ok=True)
    (PAIR_HOME / "configs" / "manual-nodes.json").write_text(json.dumps(entries, indent=2))
    started_tunnel = False
    if target:
        LOGS.mkdir(exist_ok=True)
        subprocess.Popen([str(ROOT / "bin" / "tunnel-4090")], env={**os.environ, "TUNNEL_TARGET": target, "TUNNEL_PORT": port},
                         stdout=open(LOGS / f"tunnel-{name}.log", "a"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        started_tunnel = True
    _restart_pair_desktop()
    return {"added": name, "address": addr, "tunnel": started_tunnel, "restarting_pair": True}


@app.delete("/api/pair/nodes/{name}")
async def pair_remove_node(name: str):
    entries = _pair_json("configs/manual-nodes.json", [])
    keep = [e for e in entries if e.get("name") != name and e.get("id") != name]
    if len(keep) == len(entries):
        return JSONResponse({"error": "no such node"}, status_code=404)
    (PAIR_HOME / "configs" / "manual-nodes.json").write_text(json.dumps(keep, indent=2))
    _restart_pair_desktop()
    return {"removed": name, "restarting_pair": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
