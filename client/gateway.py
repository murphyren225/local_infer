"""The personal side's only view of the model side: an OpenAI-format HTTP endpoint. stdlib only."""
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path


def _post(url: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def pi_bin() -> str | None:
    """Pi from the per-user npm prefix (bin/install-client.sh) or PATH."""
    cand = Path.home() / ".cluster-client" / "npm" / "bin" / "pi"
    return str(cand) if cand.exists() else shutil.which("pi")


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def models(hub: str) -> list[str]:
    with urllib.request.urlopen(hub.rstrip("/") + "/v1/models", timeout=5) as r:
        return [m["id"] for m in json.load(r).get("data", [])]


def chat(hub: str, lane: str, prompt: str, max_tokens: int = 1024) -> dict:
    """One completion. Returns content, the model that actually answered, latency, tokens."""
    t0 = time.monotonic()
    d = _post(hub.rstrip("/") + "/v1/chat/completions",
              {"model": lane, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens})
    if "choices" not in d:
        raise RuntimeError(json.dumps(d.get("error", d))[:300])
    return {"content": d["choices"][0]["message"].get("content") or "",
            "model": d.get("model", "?"),
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "tokens": (d.get("usage") or {}).get("completion_tokens", 0)}


def read_prompts(path: Path) -> list[str]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    if path.suffix == ".jsonl":
        return [json.loads(ln)["prompt"] for ln in lines]
    return lines
