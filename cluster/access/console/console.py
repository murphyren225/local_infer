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
    last_seen: dict[str, int] = {}
    for w in ws:
        sid = w.get("scheduledOn") or ""
        counts[sid] = counts.get(sid, 0) + 1
        last_seen[sid] = max(last_seen.get(sid, 0), w.get("createdAt") or 0)
    # remote ids ordered by most recent job: the live manual node is the one PAIR used last;
    # stale ids (a peer that left the cluster weeks ago) sort to the back
    remote_ids = sorted((k for k in counts if k and k != self_id), key=lambda k: -last_seen[k])
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


# ---------------------------------------------------------------- architecture page: stages, nodes + drafts, pools, RL

@app.get("/arch", response_class=HTMLResponse)
def arch_page() -> str:
    return (Path(__file__).parent / "arch.html").read_text(encoding="utf-8")


def _env_file() -> dict:
    out = {}
    for line in _read(ROOT / ".env").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _yaml_scalar(text: str, key: str) -> str:
    m = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


async def _sglang_draft(base: str) -> dict:
    """SGLang exposes its launch args at /get_server_info; the speculative_* fields say whether a draft runs."""
    try:
        r = await client.get(base + "/get_server_info", timeout=4)
        if r.status_code != 200:
            return {}
        d = r.json()
        algo = d.get("speculative_algorithm")
        return {"engine": "sglang", "version": d.get("version"), "algorithm": algo,
                "tokens": d.get("speculative_num_draft_tokens") if algo else None,
                "note": ("n-gram draft from context, no draft model" if algo == "NGRAM" else
                         f"draft model {d.get('speculative_draft_model_path')}" if algo else "")}
    except (httpx.HTTPError, ValueError):
        return {}


def _llama_draft() -> dict:
    """llama-server does not report draft settings; read them from PAIR's override file for the LM slot."""
    ov = _pair_json("engines/lmstudio.json", {}) or {}
    args = (ov.get("runtime") or {}).get("args") or []
    if "--spec-type" in args:
        i = args.index("--spec-type")
        kind = args[i + 1] if i + 1 < len(args) else "?"
        n = args[args.index("--spec-draft-n-max") + 1] if "--spec-draft-n-max" in args else None
        return {"engine": "llama.cpp", "algorithm": kind if kind != "none" else None, "tokens": n,
                "note": "n-gram draft from context (llama.cpp --spec-type)" if kind.startswith("ngram") else ""}
    return {"engine": "llama.cpp", "algorithm": None}


@app.get("/api/arch")
async def arch_status():
    pair = await pair_status()
    # drafts per node
    for n in pair["nodes"]:
        if n["kind"] == "self":
            n["engine_kind"] = "llama-server"
            n["draft"] = _llama_draft()
            n["hw"] = "Mac CPU"
        else:
            n["engine_kind"] = "SGLang"
            n["draft"] = await _sglang_draft(f"http://{n['address']}:{PAIR_REMOTE_ENGINE_PORT}")
            n["hw"] = "RTX 4090"
    routes_text = _read(STATE / "routes.yaml")
    routes = re.findall(r"^  ([a-z]+):$", routes_text, re.M)
    judge = _yaml_scalar(routes_text, "provider") or ("llm" if "judge:" in routes_text else "")
    nodes = _nodes()
    pools = []
    for pool in ("strong", "weak"):
        ms = [n["model"] for n in nodes.values() if n["pool"] == pool]
        model = ms[0] if ms else ""
        holders = [n["name"] for n in pair["nodes"] if model in (n.get("models") or [])]
        pools.append({"pool": pool, "model": model, "holders": holders})
    catalogue = sorted({m for n in pair["nodes"] for m in (n.get("models") or [])} | {p["model"] for p in pools if p["model"]})
    try:
        dec_health = (await client.get(f"http://127.0.0.1:{_env_file().get('DECIDER_PORT', '4100')}/health", timeout=3)).json()
    except (httpx.HTTPError, ValueError):
        dec_health = {}
    decisions = len(_read(LOGS / "decisions.jsonl").splitlines())
    traces = len(list((LOGS / "rl").glob("*.json"))) if (LOGS / "rl").exists() else 0
    dataset_rows = len(_read(LOGS / "rl-dataset.jsonl").splitlines())
    policy = None
    try:
        p = json.loads((ROOT / "decider" / "policy.json").read_text())
        policy = {"version": p.get("version"), "trained_on": p.get("trained_on")}
    except (OSError, ValueError):
        pass
    drafts_text = _read(ROOT / "cluster" / "inference" / "drafts.yaml")
    drafts = re.findall(r"^  ([a-z0-9.-]+):\n    base:", drafts_text, re.M)
    try:
        hub = json.loads(_read(Path.home() / ".cluster-client" / "config.json") or "{}").get("hub", "")
    except ValueError:
        hub = ""
    try:
        client_ok = (await client.get("http://127.0.0.1:7000/v1/models", timeout=3)).status_code == 200
    except httpx.HTTPError:
        client_ok = False
    return {"client": {"ok": client_ok, "hub": hub},
            "gateway": {"ok": await _up(ROUTER + "/models"), "mode": _read(STATE / "cluster_mode") or "?", "routes": routes, "judge": judge},
            "decider": {"ok": bool(dec_health.get("ok")), "backend": dec_health.get("backend"), "decisions": decisions},
            "pair": {"ok": bool(pair["lm_proxy_models"]), "models": pair["lm_proxy_models"], "tunnel_ok": pair["tunnel_ok"], "nodes": pair["nodes"]},
            "pools": pools, "catalogue": catalogue,
            "rl": {"traces": traces, "dataset_rows": dataset_rows, "policy": policy},
            "drafts": _draft_summary(drafts_text)}


def _draft_summary(text: str) -> list[str]:
    """'qwen3-1.7b: 4090-sglang → ngram (measured), mac-llama-cpp → ngram (measured)' per model block."""
    out = []
    blocks = re.split(r"^  (?=[a-z0-9.-]+:\n    base:)", text.split("\nmodels:\n", 1)[-1].split("\ncorpus:", 1)[0], flags=re.M)
    for b in blocks:
        m = re.match(r"([a-z0-9.-]+):", b)
        if not m:
            continue
        pairs = re.findall(r"^    ([a-z0-9-]+):\n(?:      .*\n)*?      draft: (\w+)\n(?:      .*\n)*?      status: (\w+)", b, re.M)
        out.append(f"{m.group(1)}: " + (", ".join(f"{t} → {d} ({s})" for t, d, s in pairs) or "—"))
    return out


@app.post("/api/models/bind")
async def bind_pool(payload: dict):
    """Bind a pool to a model from the PAIR catalogue: persist in .env, then rebuild routes."""
    pool, model = str(payload.get("pool", "")), str(payload.get("model", "")).strip()
    if pool not in ("strong", "weak") or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model):
        return JSONResponse({"error": "pool must be strong|weak and model a catalogue name"}, status_code=400)
    env = _env_file()
    env[f"PAIR_{pool.upper()}_MODEL"] = model
    (ROOT / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    p = await asyncio.to_thread(subprocess.run, [str(ROOT / "bin" / "cluster"), "init"], cwd=str(ROOT),
                                capture_output=True, text=True, timeout=300)
    ok = p.returncode == 0 and "✗" not in p.stdout
    return {"pool": pool, "model": model, "ok": ok, "output": (p.stdout + p.stderr)[-800:]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
